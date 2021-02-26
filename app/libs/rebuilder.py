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
import tempfile
import debian.deb822

from app.libs.common import is_qubes, is_debian, is_fedora
from app.libs.exceptions import RebuilderExceptionBuild


# TODO: Don't use wrapper but import directly Rebuilder functions
#       from debrebuild and rpmreproduce


def getRebuilder(package, **kwargs):
    if is_qubes(package.dist):
        qubes_release, package_set, dist = \
            package.dist.lstrip('qubes-').split('-', 2)
        if is_debian(dist):
            rebuilder = QubesRebuilderDEB(package, **kwargs)
        elif is_fedora(dist):
            rebuilder = QubesRebuilderRPM(package, **kwargs)
        else:
            raise RebuilderExceptionBuild(
                f"Unsupported Qubes distribution: {package.dist}")
    elif is_fedora(package.dist):
        rebuilder = FedoraRebuilder(package, **kwargs)
    elif is_debian(package.dist):
        rebuilder = DebianRebuilder(package, **kwargs)
    else:
        raise RebuilderExceptionBuild(
            f"Unsupported distribution: {package.dist}")
    return rebuilder


class BaseRebuilder:
    def __init__(self, package, **kwargs):
        self.package = package
        self.sign_keyid = kwargs.get('sign_keyid', None)
        self.logfile = "{}-{}.log".format(package, str(int(time.time())))

    def gen_temp_dir(self):
        tempdir = tempfile.mkdtemp(
            prefix='{}-{}'.format(self.package.name, self.package.version))
        return tempdir

    def get_output_dir(self):
        pass


class FedoraRebuilder:
    def __init__(self, package, **kwargs):
        pass


class DebianRebuilder(BaseRebuilder):
    def __init__(self, package, **kwargs):
        super().__init__(package, **kwargs)
        self.logfile = "debian-{}".format(self.logfile)
        self.basedir = "/rebuild/debian"
        self.snapshot_query_url = kwargs.get(
            'snapshot_query_url', 'http://snapshot.debian.org')
        self.extra_build_args = None

    def get_output_dir(self):
        return '{}/sources/{}/{}'.format(
            self.basedir,
            self.package.name,
            self.package.version
        )

    def debrebuild(self, tempdir):
        # WIP: use internal Rebuilder class instead of wrapping through shell
        build_cmd = [
            "python3",
            "/opt/debrebuild/debrebuild.py",
            "--debug",
            "--use-metasnap",
            "--builder=mmdebstrap",
            "--output={}".format(tempdir),
            "--query-url={}".format(self.snapshot_query_url),
            "--no-checksums-verification",
        ]
        if self.sign_keyid:
            build_cmd += ["--gpg-sign-keyid", self.sign_keyid]
        if self.extra_build_args:
            build_cmd += self.extra_build_args
        build_cmd += [self.package.url]

        # rebuild
        env = os.environ.copy()
        result = subprocess.run(build_cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env)
        return result, build_cmd

    def run(self):
        try:
            tempdir = self.gen_temp_dir()
            result, build_cmd = self.debrebuild(tempdir)

            if result.returncode == 0:
                self.logfile = f'{self.basedir}/log-ok/{self.logfile}'
            else:
                self.logfile = f'{self.basedir}/log-fail/{self.logfile}'

            os.makedirs(os.path.dirname(self.logfile), exist_ok=True)
            with open(self.logfile, 'wb') as fd:
                fd.write(result.stdout)

            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, build_cmd)

            os.chdir(tempdir)
            buildinfo = glob.glob("{}*.buildinfo".format(self.package.name))[0]
            link = glob.glob("rebuild*.link")[0]

            # create final output directory
            os.makedirs(self.get_output_dir(), exist_ok=True)
            shutil.copy2(
                os.path.join(tempdir, buildinfo), self.get_output_dir())
            shutil.copy2(os.path.join(tempdir, link), self.get_output_dir())
            shutil.rmtree(tempdir)

            # create symlink to new buildinfo and rebuild link file
            os.chdir(self.get_output_dir())
            if buildinfo:
                os.symlink(buildinfo, "buildinfo")
            os.symlink(link, "metadata")

            with open(buildinfo) as fd:
                parsed_buildinfo = debian.deb822.BuildInfo(fd)

            os.chdir(os.path.join(self.get_output_dir(), '../../'))
            for binpkg in parsed_buildinfo.get_binary():
                if not os.path.exists(binpkg):
                    os.symlink(self.package.name, binpkg)

        except (subprocess.CalledProcessError, FileNotFoundError,
                FileExistsError, IndexError, OSError) as e:
            if os.path.exists(self.get_output_dir()):
                shutil.rmtree(self.get_output_dir())
            raise RebuilderExceptionBuild(
                "Failed to build {}: {}".format(self.package.url, str(e)))


class QubesRebuilderRPM(FedoraRebuilder):
    def __init__(self, package, **kwargs):
        super().__init__(package, **kwargs)


class QubesRebuilderDEB(DebianRebuilder):
    def __init__(self, package, **kwargs):
        super().__init__(package, **kwargs)
        qubes_release, package_set, _ = \
            package.dist.lstrip('qubes-').split('-', 2)
        self.basedir = f'/rebuild/qubes/deb/r{qubes_release}/{package_set}'
        self.logfile = self.logfile.replace('debian-', '')
        self.extra_build_args = [
            "--gpg-verify",
            "--gpg-verify-key=/opt/debrebuild/tests/keys/qubes-debian-r4.asc",
            "--extra-repository-file=/opt/debrebuild/tests/repos/qubes-r4.list",
            "--extra-repository-key=/opt/debrebuild/tests/keys/qubes-debian-r4.asc",
        ]
