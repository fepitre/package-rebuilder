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
import re
import requests
import subprocess

try:
    import koji
except ImportError:
    koji = None
try:
    import debian.debian_support
except ImportError:
    debian = None

from packaging.version import parse as parse_version
from app.libs.common import DEBIAN, DEBIAN_ARCHES, is_qubes, is_debian, is_fedora
from app.libs.exceptions import RebuilderExceptionGet


def parse_rpm_buildinfo_fname(buildinfo):
    bn = os.path.basename(
        buildinfo).replace('.buildinfo', '').replace('-buildinfo', '')
    if not koji.check_NVRA(bn):
        return
    parsed_bn = koji.parse_NVRA(bn)
    # TODO: use 'verrel' terminology even for Debian?
    parsed_bn['version'] = '{}-{}'.format(
        parsed_bn['version'], parsed_bn['release'])
    return parsed_bn


def parse_deb_buildinfo_fname(buildinfo):
    bn = os.path.basename(buildinfo)
    parsed_tmp = bn.replace('.buildinfo', '').split('_')
    parsed_bn = {}
    if len(parsed_tmp) == 3:
        if parsed_tmp[1] == "":
            return
        parsed_nv = debian.debian_support.NativeVersion(parsed_tmp[1])
        parsed_bn['name'] = parsed_tmp[0]
        parsed_bn['epoch'] = parsed_nv._BaseVersion__epoch
        parsed_bn['version'] = parsed_nv._BaseVersion__full_version
        parsed_bn['arch'] = parsed_tmp[2].split('-')
    return parsed_bn


def getRepository(dist):
    # qubes-4.1-vm-bullseye
    # qubes-4.1-vm-fc32
    # sid
    # fedora-33
    if is_qubes(dist):
        repo = QubesRepository(dist)
    elif is_fedora(dist):
        repo = FedoraRepository(dist)
    elif is_debian(dist):
        repo = DebianRepository(dist)
    else:
        raise RebuilderExceptionGet("Unsupported distribution: {}".format(dist))
    return repo


class BuildPackage(dict):
    def __init__(self, name, epoch, version, arch, dist, url,
                 status="fail", retry=0):
        dict.__init__(self, name=name, epoch=epoch, version=version, arch=arch,
                      dist=dist, url=url, status=status, retry=retry)

    def __getattr__(self, item):
        return self[item]

    def __setattr__(self, key, value):
        self[key] = value

    def __repr__(self):
        result = f'{self.dist}-{self.name}-{self.version}.{self.arch}'
        if self.epoch and self.epoch != 0:
            result = f'{self.epoch}:{result}'
        return result

    @classmethod
    def from_dict(cls, pkg):
        return cls(**pkg)


class FedoraRepository:
    def __init__(self, dist):
        pass


class DebianRepository:
    def __init__(self, dist):
        self.dist = dist
        try:
            if is_debian(self.dist):
                if not debian:
                    raise RebuilderExceptionGet(
                        "Cannot build {}: python-debian not found".format(dist))
            else:
                raise RebuilderExceptionGet(
                    "Unknown dist: {}".format(self.dist))
        except (ValueError, FileNotFoundError) as e:
            raise RebuilderExceptionGet(f"Failed to sync repository: {str(e)}")

    def get_buildinfos(self):
        files = []
        resp = requests.get("https://buildinfos.debian.net/buildinfo-pool.list")
        if not resp.ok:
            return files
        content = resp.text.split('\n')

        # WIP: for testing purposed only
        essential = "base-files base-passwd bash coreutils dash debianutils diffutils dpkg findutils glibc grep gzip hostname init-system-helpers ncurses perl sed shadow sysvinit tar util-linux"
        required = "apt base-files base-passwd bash coreutils dash debconf debianutils diffutils dpkg e2fsprogs findutils gcc-10 gcc-9 glibc grep gzip hostname init-system-helpers mawk ncurses pam perl sed shadow sysvinit tar tzdata util-linux"
        build_essential = "acl attr audit base-files base-passwd bash binutils build-essential bzip2 cdebconf coreutils dash db5.3 debconf debianutils diffutils dpkg e2fsprogs elogind findutils gawk gcc-10 gcc-defaults gdbm glibc gmp grep gzip hostname init-system-helpers isl keyutils krb5 libcap2 libcap-ng libnsl libselinux libsigsegv libtirpc libxcrypt libzstd linux lsb make-dfsg mpclib3 mpfr4 ncurses openssl pam patch pcre2 pcre3 perl readline sed shadow systemd sysvinit tar util-linux xz-utils zlib"

        packages = set(essential.split() + required.split() + build_essential.split())
        for buildinfo in content:
            if not buildinfo.split('/')[-1].split('_')[0] in packages:
                continue
            files.append(
                "https://buildinfos.debian.net{}".format(buildinfo.strip()))
        return files

    def get_buildpackages(self, arch):
        packages = {}
        for f in self.get_buildinfos():
            parsed_bn = parse_deb_buildinfo_fname(f)
            if not parsed_bn:
                continue
            if not packages.get(parsed_bn['name'], []):
                packages[parsed_bn['name']] = []
            arch = DEBIAN_ARCHES.get(arch, arch)
            if not set(parsed_bn['arch']).intersection(("all", arch)):
                continue
            rebuild = BuildPackage(
                name=parsed_bn['name'],
                epoch=parsed_bn['epoch'],
                version=parsed_bn['version'],
                arch=arch,
                dist=self.dist,
                url=f
            )
            packages[parsed_bn['name']].append(rebuild)
        for pkg in packages.keys():
            packages[pkg].sort(
                key=lambda pkg: parse_version(pkg.version), reverse=True)
        return packages


class QubesRepository:
    def __init__(self, qubes_dist):
        self.qubes_dist = qubes_dist
        self.dist = None
        try:
            self.release, self.package_set, self.dist = \
                qubes_dist.lstrip('qubes-').split('-', 2)
            if is_fedora(self.dist):
                if not koji:
                    raise RebuilderExceptionGet(
                        f"Cannot build {self.dist}: python-koji not found")
            elif is_debian(self.dist):
                if not debian:
                    raise RebuilderExceptionGet(
                        f"Cannot build {self.dist}: python-debian not found")
        except ValueError as e:
            raise RebuilderExceptionGet(
                f"Failed to parse dist repository: {str(e)}")

    @staticmethod
    def get_rsync_files(url):
        files = []
        cmd = [
            "rsync", "--list-only", "--recursive",
            "--exclude=all-versions", url
        ]
        result = subprocess.check_output(cmd)
        lines = result.decode('utf8').strip('\n').split('\n')
        for line in lines:
            line = line.split()
            if line[0].startswith('d'):
                continue
            files.append(line[-1])
        return files

    def get_buildinfos(self):
        files = []
        qubes_rsync_baseurl = "rsync://ftp.qubes-os.org/qubes-mirror/repo"
        try:
            if is_fedora(self.dist):
                for repo in ["current", "current-testing", "security-testing"]:
                    baseurl = f"{qubes_rsync_baseurl}/yum"
                    relurl = f"r{self.release}/{repo}/{self.package_set}/{self.dist}"
                    url = f"{baseurl}/{relurl}/"
                    # WIP: wait for Fedora to merge RPM PR
                    remote_files = [os.path.join(relurl, f)
                                    for f in self.get_rsync_files(url)
                                    if f.endswith(".buildinfo") or
                                    re.match(r".*-buildinfo.*\.rpm", f)]
                    files += [os.path.join("https://yum.qubes-os.org", f)
                              for f in remote_files]
            elif is_debian(self.dist):
                baseurl = f"{qubes_rsync_baseurl}/deb"
                relurl = f"r{self.release}/vm"
                url = f"{baseurl}/{relurl}/"
                files = [os.path.join(relurl, f)
                         for f in self.get_rsync_files(url)
                         if f.endswith(".buildinfo")]
                files = [os.path.join("https://deb.qubes-os.org", f)
                         for f in files]
            else:
                raise RebuilderExceptionGet(f"Unknown dist: {self.dist}")
        except (ValueError, FileNotFoundError) as e:
            raise RebuilderExceptionGet(f"Failed to sync repository: {str(e)}")
        return files

    def get_buildpackages(self, arch):
        packages = {}
        for f in self.get_buildinfos():
            if is_fedora(self.dist):
                parsed_bn = parse_rpm_buildinfo_fname(f)
                if not parsed_bn:
                    continue
                if parsed_bn['arch'] not in ("noarch", arch):
                    continue
            elif is_debian(self.dist):
                parsed_bn = parse_deb_buildinfo_fname(f)
                if not parsed_bn:
                    continue
                arch = DEBIAN_ARCHES.get(arch, arch)
                if not set(parsed_bn['arch']).intersection(("all", arch)):
                    continue
                if '+deb{}u'.format(DEBIAN.get(self.dist)) not in \
                        parsed_bn['version']:
                    continue
            else:
                continue
            if not packages.get(parsed_bn['name'], []):
                packages[parsed_bn['name']] = []
            rebuild = BuildPackage(
                name=parsed_bn['name'],
                epoch=parsed_bn['epoch'],
                version=parsed_bn['version'],
                arch=arch,
                dist=self.qubes_dist,
                url=f,
            )
            packages[parsed_bn['name']].append(rebuild)
        for pkg in packages.keys():
            packages[pkg].sort(
                key=lambda pkg: parse_version(pkg.version), reverse=True)
        return packages
