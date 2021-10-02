#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2021 Frédéric Pierret (fepitre) <frederic.pierret@qubes-os.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
import os
import subprocess
import json
import glob
import shutil

try:
    import koji
except ImportError:
    koji = None
try:
    import debian.debian_support
    import debian.deb822
except ImportError:
    debian = None

from app.libs.exceptions import RebuilderExceptionAttest
from app.libs.rebuilder import getRebuilder


# fixme: improve merge as it does not support concurrent access
def merge_intoto_metadata(output, gpg_sign_keyid):
    links = glob.glob(f"{output}/rebuild.{gpg_sign_keyid[:8].lower()}.*.link")
    final_link = {}
    try:
        for link in links:
            with open(link, 'r') as fd:
                parsed_link = json.loads(fd.read())
            if not final_link:
                final_link = parsed_link
                del final_link["signatures"]
            final_link["signed"]["products"].update(parsed_link["signed"]["products"])
        with open(f"{output}/rebuild.link", "w") as fd:
            fd.write(json.dumps(final_link))
        cmd = ["in-toto-sign", "--gpg", gpg_sign_keyid, "-f", "rebuild.link"]
        subprocess.run(cmd, cwd=output, check=True)
    except Exception as e:
        raise RebuilderExceptionAttest(f"Failed to merge links: {str(e)}")
    finally:
        if os.path.exists(f"{output}/rebuild.link"):
            os.remove(f"{output}/rebuild.link")


def generate_intoto_metadata(cwd, output, gpg_sign_keyid, files):
    if not files:
        raise RebuilderExceptionAttest(f"No files provided for in-toto metadata generation!")
    cmd = ["in-toto-run", f"--step-name=rebuild", "--no-command", "--products"] + files
    cmd += ["--gpg", gpg_sign_keyid, "--metadata-directory", output]
    try:
        os.makedirs(output, exist_ok=True)
        subprocess.run(cmd, cwd=cwd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise RebuilderExceptionAttest(f"in-toto metadata generation failed: {str(e)}")


def get_intoto_metadata_basedir(distribution, unreproducible=False):
    builder = getRebuilder(distribution)
    output_dir = f"/rebuild/{builder.distdir}"
    sources = 'unreproducible/sources' if unreproducible else 'sources'
    return f"{output_dir}/{sources}"


def get_intoto_metadata_package(package, unreproducible=False):
    basedir = get_intoto_metadata_basedir(package.distribution, unreproducible=unreproducible)
    return f"{basedir}/{package.name}/{package.version}"


def process_attestation(package, output, gpg_sign_keyid, files, unreproducible):
    generate_intoto_metadata(package.artifacts, output, gpg_sign_keyid, files)

    tmp_link = f"rebuild.{gpg_sign_keyid[:8].lower()}.link"
    if not os.path.exists(f"{output}/{tmp_link}"):
        raise RebuilderExceptionAttest(f"Cannot find link for {package}")
    final_link = f"rebuild.{gpg_sign_keyid[:8].lower()}.{package.arch}.link"

    # create final output directory
    outputdir = get_intoto_metadata_package(package, unreproducible=unreproducible)
    os.makedirs(outputdir, exist_ok=True)

    shutil.copy2(f"{output}/{tmp_link}", f"{outputdir}/{final_link}")

    # update metadata
    key = "unreproducible" if unreproducible else "reproducible"
    package.metadata[key] = f"{outputdir}/{final_link}"

    os.chdir(os.path.join(outputdir, "../../"))
    with open(package.buildinfos["new"]) as fd:
        parsed_buildinfo = debian.deb822.BuildInfo(fd)
    for binpkg in parsed_buildinfo.get_binary():
        if not os.path.exists(binpkg):
            os.symlink(package.name, binpkg)

    # combine all available links (one link == one architecture)
    os.chdir(outputdir)
    merge_intoto_metadata(outputdir, gpg_sign_keyid)

    # create symlink to new link file
    os.chdir(outputdir)
    if not os.path.exists("metadata"):
        os.symlink(f"rebuild.{gpg_sign_keyid[:8].lower()}.link", "metadata")
