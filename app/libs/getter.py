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
from app.libs.exceptions import RebuilderExceptionDist, RebuilderExceptionGet


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


class RebuilderDist:
    def __init__(self, dist):
        try:
            # qubes-4.1-vm-bullseye.amd64
            # qubes-4.1-vm-fc32.noarch
            # sid.all
            # bullseye+essential+build_essential.all
            # fedora-33.amd64
            self.name, self.arch = dist.rsplit('.', 1)
        except ValueError:
            raise RebuilderExceptionDist(f"Cannot parse dist: {dist}.")

        if is_qubes(dist):
            self.repo = QubesRepository(self.name)
            self.package_sets = ["full"]
            self.distribution = "qubes"
        elif is_fedora(dist):
            self.repo = FedoraRepository(self.name)
            self.package_sets = []
            self.distribution = "fedora"
        elif is_debian(dist):
            self.name, package_sets = "{}+".format(self.name).split('+', 1)
            self.package_sets = [pkg_set for pkg_set in package_sets.split('+')
                                 if pkg_set]
            self.distribution = "debian"
            self.repo = DebianRepository(self.name, self.package_sets)
        else:
            raise RebuilderExceptionDist(f"Unsupported distribution: {dist}")

    def __repr__(self):
        result = f'{self.name}.{self.arch}'
        return result


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
    def __init__(self, dist, package_sets):
        self.dist = dist
        self.package_sets = package_sets
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

    def get_buildinfos(self, arch):
        files = []
        url = f"https://buildinfos.debian.net/buildinfo-pool_{self.dist}_{arch}.list"
        resp = requests.get(url)
        if not resp.ok:
            return files
        buildinfo_pool = resp.text.rstrip('\n').split('\n')

        filtered_packages = []
        for pkgset_name in self.package_sets:
            url = f"https://jenkins.debian.net/userContent/reproducible/" \
                  f"debian/pkg-sets/{self.dist}/{pkgset_name}.pkgset"
            resp = requests.get(url)
            if not resp.ok:
                continue
            content = resp.text.rstrip('\n').split('\n')
            filtered_packages += content
        filtered_packages = set(sorted(filtered_packages))

        for buildinfo in buildinfo_pool:
            if filtered_packages and buildinfo.split('/')[-1].split('_')[0] \
                    not in filtered_packages:
                continue
            files.append(f"https://buildinfos.debian.net{buildinfo}")
        return files

    def get_buildpackages(self, arch):
        packages = {}
        arch = DEBIAN_ARCHES.get(arch, arch)
        for f in self.get_buildinfos(arch):
            parsed_bn = parse_deb_buildinfo_fname(f)
            if not parsed_bn:
                continue
            if not packages.get(parsed_bn['name'], []):
                packages[parsed_bn['name']] = []
            # WIP: Ignore buildinfo having e.g. amd64-source?
            if len(parsed_bn['arch']) > 1:
                continue
            if parsed_bn['arch'][0] != arch:
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
                if len(parsed_bn['arch']) > 1:
                    continue
                arch = DEBIAN_ARCHES.get(arch, arch)
                if parsed_bn['arch'][0] != arch:
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
