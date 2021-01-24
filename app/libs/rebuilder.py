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
import shutil
import glob
import time

from debian.deb822 import Deb822
from app.libs.exceptions import RebuilderExceptionBuild


class Rebuilder:
    def __init__(self, package, snapshot_query_url, sign_keyid=None):
        self.package = package
        self.snapshot_query_url = snapshot_query_url
        self.sign_keyid = sign_keyid
        self.logfile = "{}-{}.log".format(package, str(int(time.time())))

    def get_sources_dir(self):
        return '/deb/r{}/{}/sources'.format(
            self.package.release,
            self.package.package_set
        )

    def get_output_dir(self):
        return '{}/{}/{}'.format(
            self.get_sources_dir(),
            self.package.name,
            self.package.version
        )

    def run(self):
        try:
            # TODO: This will be generalized with wrapper for DEB and RPM
            build_cmd = [
                "python3",
                "/opt/debrebuild/debrebuild.py",
                "--debug",
                "--builder=mmdebstrap",
                "--output={}".format(self.get_output_dir()),
                "--query-url={}".format(self.snapshot_query_url),
            ]
            if self.sign_keyid:
                build_cmd += ["--gpg-sign-keyid", self.sign_keyid]

            build_cmd += [
                "--no-checksums-verification",
                "--gpg-verify",
                "--gpg-verify-key=/opt/debrebuild/tests/keys/qubes-debian-r4.asc",
                "--extra-repository-file=/opt/debrebuild/tests/repos/qubes-r4.list",
                "--extra-repository-key=/opt/debrebuild/tests/keys/qubes-debian-r4.asc",
                self.package.url
            ]
            # rebuild
            env = os.environ.copy()
            # env["TMPDIR"] = "/var/tmp"
            result = subprocess.run(build_cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, env=env)

            # Originally check=True was used but it seems that we cannot
            # get captured output?
            if result.returncode == 0:
                self.logfile = '/log-ok/{}'.format(self.logfile)
            else:
                self.logfile = '/log-fail/{}'.format(self.logfile)

            with open(self.logfile, 'w') as fd:
                fd.write(result.stdout.decode('utf8'))

            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, build_cmd)

            # create symlink to new buildinfo and rebuild link file
            os.chdir(self.get_output_dir())
            buildinfo = glob.glob(
                "{}*.buildinfo".format(self.package.name))[0]
            link = glob.glob("rebuild*.link")[0]
            os.symlink(buildinfo, "buildinfo")
            os.symlink(link, "metadata")

            # clean useless artifacts
            extracted_srcdir = "{}-{}".format(
                self.package.name, self.package.version.split('-')[0])
            shutil.rmtree(extracted_srcdir)

            files_to_remove = glob.glob("*.deb")
            files_to_remove += glob.glob("*.changes")
            files_to_remove += glob.glob("*.tar.*")
            for f in files_to_remove:
                os.remove(f)

            with open(buildinfo) as fd:
                for paragraph in Deb822.iter_paragraphs(fd.read()):
                    for item in paragraph.items():
                        if item[0] == 'Binary':
                            binary = item[1].split()

            os.chdir(self.get_sources_dir())
            for binpkg in binary:
                if not os.path.exists(binpkg):
                    os.symlink(self.package.name, binpkg)

        except (subprocess.CalledProcessError, FileNotFoundError,
                FileExistsError, IndexError, OSError) as e:
            if os.path.exists(self.get_output_dir()):
                shutil.rmtree(self.get_output_dir())
            raise RebuilderExceptionBuild(
                "Failed to build {}: {}".format(self.package.url, str(e)))
