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
import tempfile

try:
    import koji
except ImportError:
    koji = None
try:
    import debian.debian_support
    import debian.deb822
except ImportError:
    debian = None

from app.lib.exceptions import RebuilderExceptionAttest
from app.lib.rebuild import getRebuilder


class BaseAttester:
    def __init__(self, **kwargs):
        self.keyid = kwargs.get("keyid", None)
        self.rebuild_dir = kwargs.get("rebuild_dir", "/var/lib/rebuilder/rebuild")

    def metadata_dir(self, distribution, reproducible):
        builder = getRebuilder(distribution)
        output_dir = f"{self.rebuild_dir}/{builder.distdir}"
        sources = "sources" if reproducible else "unreproducible/sources"
        return f"{output_dir}/{sources}"

    def metadata_package_dir(self, package, reproducible):
        basedir = self.metadata_dir(package.distribution, reproducible=reproducible)
        return f"{basedir}/{package.name}/{package.version}"

    def generate_metadata(self, artifacts, filenames):
        if not self.keyid:
            raise RebuilderExceptionAttest("No GPG key id provided for metadata generation!")
        if not filenames:
            raise RebuilderExceptionAttest(f"No files provided for in-toto metadata generation!")
        output = tempfile.mkdtemp(dir=artifacts)
        cmd = ["in-toto-run", f"--step-name=rebuild", "--no-command", "--products"] + filenames
        cmd += ["--gpg", self.keyid, "--metadata-directory", output]
        try:
            os.makedirs(output, exist_ok=True)
            subprocess.run(cmd, cwd=artifacts, check=True)
            return output
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RebuilderExceptionAttest(f"in-toto metadata generation failed: {str(e)}")

    # fixme: improve merge as it does not support concurrent access
    def merge_metadata(self, output):
        if not self.keyid:
            raise RebuilderExceptionAttest("No GPG key id provided for metadata generation!")
        links = glob.glob(f"{output}/rebuild.{self.keyid[:8].lower()}.*.link")
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
            cmd = ["in-toto-sign", "--gpg", self.keyid, "-f", "rebuild.link"]
            subprocess.run(cmd, cwd=output, check=True)
        except Exception as e:
            raise RebuilderExceptionAttest(f"Failed to merge links: {str(e)}")
        finally:
            if os.path.exists(f"{output}/rebuild.link"):
                os.remove(f"{output}/rebuild.link")


def process_attestation(package, gpg_sign_keyid, files, reproducible, **kwargs):
    with open(package.buildinfos["new"]) as fd:
        parsed_buildinfo = debian.deb822.BuildInfo(fd)
    # if parsed_buildinfo.get_version()._BaseVersion__epoch:
    #     package.epoch = parsed_buildinfo.get_version()._BaseVersion__epoch

    attester = BaseAttester(keyid=gpg_sign_keyid, **kwargs)

    # generate tmp in-toto metadata in output directory for files
    outputdir_tmp = attester.generate_metadata(package.artifacts, files)

    # define final in-toto metadata filename with respect to tmp one
    tmp_link = f"rebuild.{gpg_sign_keyid[:8].lower()}.link"
    if not os.path.exists(f"{outputdir_tmp}/{tmp_link}"):
        raise RebuilderExceptionAttest(f"Cannot find link for {package}")
    final_link = f"rebuild.{gpg_sign_keyid[:8].lower()}.{package.arch}.link"

    # create final output directory
    outputdir = attester.metadata_package_dir(package, reproducible=reproducible)
    os.makedirs(outputdir, exist_ok=True)

    # copy generated metadata in final location
    shutil.copy2(f"{outputdir_tmp}/{tmp_link}", f"{outputdir}/{final_link}")

    # update package metadata entry
    key = "reproducible" if reproducible else "unreproducible"
    if not package.metadata:
        package.metadata = {}
    package.metadata[key] = f"{outputdir}/{final_link}"

    # generate symlinks for binary packages
    files_names = [f.split('_')[0] for f in files]
    os.chdir(os.path.join(outputdir, "../../"))
    if not package.files:
        package.files = {}
    for binpkg in parsed_buildinfo.get_binary():
        package.files.setdefault(key, [])
        if binpkg in files_names:
            package.files[key].append(binpkg)
        if not os.path.exists(binpkg):
            os.symlink(package.name, binpkg)

    # combine all available links (one link == one architecture)
    os.chdir(outputdir)
    attester.merge_metadata(outputdir)

    # create symlink to new link file
    os.chdir(outputdir)
    if not os.path.exists("metadata"):
        os.symlink(f"rebuild.{gpg_sign_keyid[:8].lower()}.link", "metadata")

    return outputdir
