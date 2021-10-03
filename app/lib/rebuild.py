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
import shutil
import subprocess
import time
import tempfile
import glob

from app.config.config import Config
from app.lib.common import is_qubes, is_debian, is_fedora
from app.lib.exceptions import RebuilderExceptionBuild


# fixme: don't use wrapper but import directly Rebuilder functions
#       from debrebuild and rpmreproduce


def getRebuilder(distribution, **kwargs):
    if is_qubes(distribution):
        # In the case of QubesOS distribution is the underlying TemplateVM
        qubes_release, package_set, distribution = distribution.lstrip('qubes-').split('-', 2)
        if is_debian(distribution):
            rebuilder = QubesRebuilderDEB(
                qubes_release=qubes_release,
                package_set=package_set,
                snapshot_query_url=Config["project"].get("qubesos", {}).get('snapshot', None),
                snapshot_mirror=Config["project"].get("qubesos", {}).get('snapshot', None),
                **kwargs
            )
        elif is_fedora(distribution):
            rebuilder = QubesRebuilderRPM(**kwargs)
        else:
            raise RebuilderExceptionBuild(f"Unsupported Qubes distribution: {distribution}")
    elif is_fedora(distribution):
        rebuilder = FedoraRebuilder(**kwargs)
    elif is_debian(distribution):
        rebuilder = DebianRebuilder(
            snapshot_query_url=Config["project"].get("debian", {}).get('snapshot', None),
            snapshot_mirror=Config["project"].get("debian", {}).get('snapshot', None),
            **kwargs
        )
    else:
        raise RebuilderExceptionBuild(f"Unsupported distribution: {distribution}")
    return rebuilder


class BaseRebuilder:
    def __init__(self, **kwargs):
        self.sign_keyid = kwargs.get('sign_keyid', None)
        self.artifacts_dir = kwargs.get('artifacts_dir', "/var/lib/rebuilder/artifacts")

    @staticmethod
    def gen_temp_dir(package):
        tempdir = tempfile.mkdtemp(
            prefix=f"{package.name}-{package.version}",
        )
        return tempdir


class FedoraRebuilder:
    def __init__(self, **kwargs):
        pass


class DebianRebuilder(BaseRebuilder):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.project = "debian"
        self.distdir = self.project
        self.basedir = f"{self.artifacts_dir}/{self.project}"
        self.snapshot_query_url = kwargs.get(
            'snapshot_query_url', 'http://snapshot.notset.fr')
        self.snapshot_mirror = kwargs.get(
            'snapshot_mirror', "http://snapshot.notset.fr")
        self.extra_build_args = None

    def debrebuild(self, tempdir, package):
        # WIP: use internal Rebuilder class instead of wrapping through shell
        build_cmd = [
            "python3",
            "/opt/debrebuild/debrebuild.py",
            "--debug",
            "--use-metasnap",
            "--builder=mmdebstrap",
            "--output={}".format(tempdir),
            "--query-url={}".format(self.snapshot_query_url),
            "--snapshot-mirror={}".format(self.snapshot_mirror)
        ]
        if self.sign_keyid:
            build_cmd += ["--gpg-sign-keyid", self.sign_keyid]
        if self.extra_build_args:
            build_cmd += self.extra_build_args
        build_cmd += [package.buildinfos["old"]]

        # rebuild
        env = os.environ.copy()
        result = subprocess.run(build_cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env)
        return result, build_cmd

    def run(self, package):
        logfile = f"{package}-{str(int(time.time()))}.log"
        try:
            tempdir = self.gen_temp_dir(package)
            result, build_cmd = self.debrebuild(tempdir, package)

            logfile = f'{self.basedir}/{logfile}'
            os.makedirs(os.path.dirname(logfile), exist_ok=True)
            with open(logfile, 'wb') as fd:
                fd.write(result.stdout)

            artifactsdir = os.path.join(self.basedir, os.path.basename(tempdir))
            os.makedirs(artifactsdir)
            for f in [os.path.join(tempdir, f)
                      for f in os.listdir(tempdir)
                      if os.path.isfile(os.path.join(tempdir, f))]:
                shutil.copy2(f, artifactsdir)
            if tempdir and os.path.exists(tempdir):
                shutil.rmtree(tempdir)
            package.artifacts = artifactsdir

            # This is for recording logfile entry into DB
            package.log = logfile

            if result.returncode in (0, 2):
                buildinfo = glob.glob(f"{package.artifacts}/{package.name}*.buildinfo")
                if not buildinfo:
                    raise RebuilderExceptionBuild(f"Cannot find buildinfo for {package}")
                buildinfo = buildinfo[0]
                package.buildinfos["new"] = buildinfo

            if result.returncode == 0:
                package.status = "reproducible"
            elif result.returncode == 2:
                package.status = "unreproducible"
            else:
                package.status = "failure"

            if result.returncode not in (0, 2):
                raise subprocess.CalledProcessError(
                    result.returncode, build_cmd)

            return package
        except (subprocess.CalledProcessError, FileNotFoundError,
                FileExistsError, IndexError, OSError):
            raise RebuilderExceptionBuild([dict(package)])


class QubesRebuilderRPM(FedoraRebuilder):
    def __init__(self, qubes_release, package_set, **kwargs):
        super().__init__(**kwargs)


class QubesRebuilderDEB(DebianRebuilder):
    def __init__(self, qubes_release, package_set, **kwargs):
        super().__init__(**kwargs)
        self.project = "qubesos"
        self.distdir = f"{self.project}/deb/r{qubes_release}/{package_set}"
        self.basedir = f"{self.artifacts_dir}/{self.distdir}"
        self.extra_build_args = [
            "--gpg-verify",
            "--gpg-verify-key=/opt/debrebuild/tests/keys/qubes-debian-r4.asc",
            "--extra-repository-file=/opt/debrebuild/tests/repos/qubes-r4.list",
            "--extra-repository-key=/opt/debrebuild/tests/keys/qubes-debian-r4.asc",
        ]
