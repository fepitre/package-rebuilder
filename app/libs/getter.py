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
from app.libs.common import DEBIAN, DEBIAN_ARCHES, is_qubes, is_debian, is_fedora, \
    get_backend_tasks, rebuild_task_parser, parse_deb_buildinfo_fname, parse_rpm_buildinfo_fname
from app.libs.exceptions import RebuilderExceptionDist, RebuilderExceptionGet
from app.libs.logger import log


def get_rebuilt_packages(app):
    parsed_packages = []
    tasks = get_backend_tasks(app)
    for task in tasks:
        parsed_task = rebuild_task_parser(task)
        if parsed_task:
            for p in parsed_task:
                package = BuildPackage.from_dict(p)
                parsed_packages.append(package)
    return parsed_packages


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
            self.repo = QubesRepository(self.name, self.arch)
            self.package_sets = ["full"]
            self.distribution = "qubes"
        elif is_fedora(dist):
            self.repo = FedoraRepository(self.name)
            self.package_sets = []
            self.distribution = "fedora"
        elif is_debian(self.name):
            self.name, package_sets = "{}+".format(self.name).split('+', 1)
            self.package_sets = [pkg_set for pkg_set in package_sets.split('+')
                                 if pkg_set]
            # If no package set is provided, we understand it as "all"
            if not self.package_sets:
                self.package_sets = ["all"]
            self.distribution = "debian"
            self.repo = DebianRepository(self.name, self.arch, self.package_sets)
        else:
            raise RebuilderExceptionDist(f"Unsupported distribution: {dist}")

    def __repr__(self):
        result = f'{self.name}.{self.arch}'
        return result


class BuildPackage(dict):
    def __init__(self, name, epoch, version, arch, dist, url,
                 artifacts="", status="", log="", retries=0):
        dict.__init__(self, name=name, epoch=epoch, version=version, arch=arch,
                      dist=dist, url=url, artifacts=artifacts, status=status,
                      log=log, retries=retries)

    def __getattr__(self, item):
        return self[item]

    def __setattr__(self, key, value):
        self[key] = value

    def __repr__(self):
        result = f'{self.dist}-{self.name}-{self.version}.{self.arch}'
        if self.epoch and self.epoch != 0:
            result = f'{self.epoch}:{result}'
        return result

    def __eq__(self, other):
        return repr(self) == repr(other)

    @classmethod
    def from_dict(cls, pkg):
        return cls(**pkg)


class FedoraRepository:
    def __init__(self, dist):
        pass


class DebianRepository:
    def __init__(self, dist, arch, package_sets):
        self.dist = dist
        self.arch = DEBIAN_ARCHES.get(arch, arch)
        self.package_sets = package_sets
        self.packages = None
        try:
            if is_debian(self.dist):
                if debian is None:
                    raise RebuilderExceptionGet(f"Cannot build {self.dist}: python-debian not found")
            else:
                raise RebuilderExceptionGet(f"Unknown dist: {self.dist}")
        except (ValueError, FileNotFoundError) as e:
            raise RebuilderExceptionGet(f"Failed to sync repository: {str(e)}")

    def get_package_names_in_debian_set(self, pkgset_name):
        packages = []
        url = f"https://jenkins.debian.net/userContent/reproducible/" \
              f"debian/pkg-sets/{self.dist}/{pkgset_name}.pkgset"
        try:
            resp = requests.get(url)
            if resp.ok:
                content = resp.text.rstrip('\n').split('\n')
                packages = set(sorted(content))
        except requests.exceptions.ConnectionError as e:
            log.error(f"Failed to get {pkgset_name}: {str(e)}")
        return packages

    def get_buildinfo_files(self, arch):
        files = []
        url = f"https://buildinfos.debian.net/buildinfo-pool_{self.dist}_{arch}.list"
        try:
            resp = requests.get(url)
            if not resp.ok:
                return files
        except requests.exceptions.ConnectionError:
            return files

        buildinfo_pool = resp.text.rstrip('\n').split('\n')
        for buildinfo in buildinfo_pool:
            files.append(f"https://buildinfos.debian.net{buildinfo}")
        return files

    def get_packages(self):
        packages = {}
        latest_packages = []
        for f in self.get_buildinfo_files(self.arch):
            parsed_bn = parse_deb_buildinfo_fname(f)
            if not parsed_bn:
                continue
            if not packages.get(parsed_bn['name'], None):
                packages[parsed_bn['name']] = []
            # fixme: ignore buildinfo having e.g. amd64-source?
            if len(parsed_bn['arch']) > 1:
                continue
            if parsed_bn['arch'][0] != self.arch:
                continue
            rebuild = BuildPackage(
                name=parsed_bn['name'],
                epoch=parsed_bn['epoch'],
                version=parsed_bn['version'],
                arch=self.arch,
                dist=self.dist,
                url=f
            )
            packages[parsed_bn['name']].append(rebuild)
        for pkg in packages.keys():
            packages[pkg].sort(key=lambda pkg: parse_version(pkg.version), reverse=True)
            if packages[pkg]:
                latest_packages.append(packages[pkg][0])
        self.packages = latest_packages
        return self.packages

    def get_packages_to_rebuild(self, package_set=None):
        if not self.packages:
            self.packages = self.get_packages()
        packages_to_rebuild = []
        filtered_package_names = []
        if package_set:
            package_sets = [package_set]
        else:
            package_sets = self.package_sets
        if "all" not in package_sets:
            for pkgset_name in package_sets:
                filtered_package_names += self.get_package_names_in_debian_set(pkgset_name)
            filtered_package_names = set(sorted(filtered_package_names))
            for package in self.packages:
                if package.name in filtered_package_names:
                    packages_to_rebuild.append(package)
        else:
            packages_to_rebuild = self.packages
        return packages_to_rebuild


class QubesRepository:
    def __init__(self, qubes_dist, arch):
        self.qubes_dist = qubes_dist
        self.dist = None
        self.arch = arch
        self.packages = None
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

    def get_buildinfo_files(self):
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

    def get_packages(self):
        packages = {}
        latest_packages = []
        for f in self.get_buildinfo_files():
            if is_fedora(self.dist):
                parsed_bn = parse_rpm_buildinfo_fname(f)
                if not parsed_bn:
                    continue
                if parsed_bn['arch'] not in ("noarch", self.arch):
                    continue
            elif is_debian(self.dist):
                parsed_bn = parse_deb_buildinfo_fname(f)
                if not parsed_bn:
                    continue
                if len(parsed_bn['arch']) > 1:
                    continue
                self.arch = DEBIAN_ARCHES.get(self.arch, self.arch)
                if parsed_bn['arch'][0] != self.arch:
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
                arch=self.arch,
                dist=self.qubes_dist,
                url=f,
            )
            packages[parsed_bn['name']].append(rebuild)
        for pkg in packages.keys():
            packages[pkg].sort(key=lambda pkg: parse_version(pkg.version), reverse=True)
            latest_packages.append(packages[pkg][0])
        return latest_packages

    def get_packages_to_rebuild(self, package_set=None):
        if not self.packages:
            self.packages = self.get_packages()
        return self.packages
