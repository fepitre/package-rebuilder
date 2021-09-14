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

import subprocess

try:
    import koji
except ImportError:
    koji = None
try:
    import debian.debian_support
except ImportError:
    debian = None

from app.libs.exceptions import RebuilderExceptionAttest
from app.libs.rebuilder import getRebuilder


def generate_intoto_metadata(output, gpg_sign_keyid, buildinfo):
    new_files = [f['name'] for f in buildinfo['checksums-sha256']
                 if not f['name'].endswith('.dsc')]
    cmd = [
              "in-toto-run", "--step-name=rebuild", "--no-command",
              "--products"
          ] + list(new_files)
    cmd += ["--gpg", gpg_sign_keyid]
    try:
        subprocess.run(cmd, cwd=output, check=True)
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
