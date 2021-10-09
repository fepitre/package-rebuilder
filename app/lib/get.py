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
    import debian.deb822
except ImportError:
    debian = None

from packaging.version import parse as parse_version
from app.lib.common import DEBIAN, DEBIAN_ARCHES, is_qubes, is_debian, is_fedora, get_project, \
    parse_deb_buildinfo_fname, parse_rpm_buildinfo_fname
from app.lib.exceptions import RebuilderExceptionDist, RebuilderExceptionGet
from app.lib.log import log


def getPackage(package_as_dict):
    if not isinstance(package_as_dict, dict):
        raise RebuilderExceptionGet("Cannot parse input")
    distribution = package_as_dict.get("distribution", None)
    if not distribution:
        raise RebuilderExceptionGet(f"Cannot find distribution for: {package_as_dict}")
    if is_qubes(distribution):
        package = QubesPackage.from_dict(package_as_dict)
    elif is_fedora(distribution):
        package = FedoraPackage.from_dict(package_as_dict)
    elif is_debian(distribution):
        package = DebianPackage.from_dict(package_as_dict)
    else:
        raise RebuilderExceptionGet(f"Unsupported distribution: {distribution}")
    return package


# fixme: introduce a build timestamp: currently timestamp is given by logfile basename
class Package(dict):
    def __init__(self, name, epoch, version, arch, distribution, buildinfos,
                 metadata=None, artifacts=None, status=None, log=None, diffoscope=None,
                 retries=0, files=None):
        dict.__init__(self, name=name, epoch=epoch, version=version, arch=arch,
                      distribution=distribution, metadata=metadata, artifacts=artifacts,
                      status=status, log=log, diffoscope=diffoscope, retries=retries,
                      buildinfos=buildinfos, files=files)

    def __getattr__(self, item):
        return self[item]

    def __setattr__(self, key, value):
        self[key] = value

    def __repr__(self):
        result = f"{self.name}-{self.version}.{self.arch}"
        if self.epoch and self.epoch != 0:
            result = f"{self.epoch}:{result}"
        if self.status:
            result = f"{result}={self.status}"
        return result

    def __str__(self):
        result = f"{self.name}-{self.version}.{self.arch}"
        if self.epoch and self.epoch != 0:
            result = f"{self.epoch}:{result}"
        return result

    def __eq__(self, other):
        return repr(self) == repr(other)

    @classmethod
    def from_dict(cls, pkg):
        return cls(**pkg)

    def to_dict(self):
        d = dict(self)
        for k in ["artifacts", "retries"]:
            del d[k]
        return d


class DebianPackage(Package):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class FedoraPackage(Package):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class QubesPackage(Package):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __repr__(self):
        result = super().__repr__()
        result = f'{self.distribution}_{result}'
        return result


class FedoraRepository:
    def __init__(self, distribution):
        pass


class DebianRepository:
    def __init__(self, distribution, arch, package_sets, **kwargs):
        self.distribution = distribution
        self.arch = DEBIAN_ARCHES.get(arch, arch)
        self.package_sets = package_sets
        self.packages = None
        try:
            if is_debian(self.distribution):
                if debian is None:
                    raise RebuilderExceptionGet(
                        f"Cannot build {self.distribution}: python-debian not found")
            else:
                raise RebuilderExceptionGet(f"Unknown dist: {self.distribution}")
        except (ValueError, FileNotFoundError) as e:
            raise RebuilderExceptionGet(f"Failed to sync repository: {str(e)}")

        self.package_sets_baseurl = kwargs.get(
            "package_sets_baseurl", f"https://jenkins.debian.net/userContent/reproducible/"
                                    f"debian/pkg-sets/{self.distribution}")
        self.buildinfos_baseurl = kwargs.get("buildinfos_baseurl", "https://buildinfos.debian.net")

    def get_package_names_in_debian_set(self, pkgset_name):
        packages = []
        url = f"{self.package_sets_baseurl}/{pkgset_name}.pkgset"
        try:
            resp = requests.get(url)
            if resp.ok:
                content = [p.strip() for p in resp.text.strip('\n').split('\n')]
                packages = set(sorted(content))
        except requests.exceptions.ConnectionError as e:
            log.error(f"Failed to get {pkgset_name}: {str(e)}")
        return packages

    def get_buildinfo_files(self):
        files = []
        url = f"{self.buildinfos_baseurl}/buildinfo-pool_{self.distribution}_{self.arch}.list"
        try:
            resp = requests.get(url)
            if not resp.ok:
                return files
        except requests.exceptions.ConnectionError:
            return files

        buildinfo_pool = resp.text.rstrip('\n').split('\n')
        for buildinfo in buildinfo_pool:
            files.append(f"{self.buildinfos_baseurl}{buildinfo}")
        return files

    def get_packages(self):
        packages = {}
        latest_packages = []
        for f in self.get_buildinfo_files():
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
            rebuild = DebianPackage(
                name=parsed_bn['name'],
                epoch=parsed_bn['epoch'],
                version=parsed_bn['version'],
                arch=self.arch,
                distribution=self.distribution,
                buildinfos={"old": f}
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
        if "full" not in package_sets:
            for pkgset_name in package_sets:
                filtered_package_names += self.get_package_names_in_debian_set(pkgset_name)
            filtered_package_names = sorted(set(filtered_package_names))
            for package in self.packages:
                if package.name in filtered_package_names:
                    packages_to_rebuild.append(package)
        else:
            packages_to_rebuild = self.packages
        return packages_to_rebuild


class QubesRepository:
    def __init__(self, qubes_dist, arch):
        self.qubes_dist = qubes_dist
        self.distribution = None
        self.arch = arch
        self.packages = None
        try:
            # fixme: clarify package_set being dom0/vm and packages set being pre-defined list
            #  of packages elsewhere.
            self.release, self.package_set, self.distribution = \
                qubes_dist.lstrip('qubes-').split('-', 2)
            if is_fedora(self.distribution):
                if not koji:
                    raise RebuilderExceptionGet(
                        f"Cannot build {self.distribution}: python-koji not found")
            elif is_debian(self.distribution):
                if not debian:
                    raise RebuilderExceptionGet(
                        f"Cannot build {self.distribution}: python-debian not found")
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
            if is_fedora(self.distribution):
                for repo in ["current", "current-testing", "security-testing"]:
                    baseurl = f"{qubes_rsync_baseurl}/yum"
                    relurl = f"r{self.release}/{repo}/{self.package_set}/{self.distribution}"
                    url = f"{baseurl}/{relurl}/"
                    # WIP: wait for Fedora to merge RPM PR
                    remote_files = [os.path.join(relurl, f)
                                    for f in self.get_rsync_files(url)
                                    if f.endswith(".buildinfo") or
                                    re.match(r".*-buildinfo.*\.rpm", f)]
                    files += [os.path.join("https://yum.qubes-os.org", f)
                              for f in remote_files]
            elif is_debian(self.distribution):
                baseurl = f"{qubes_rsync_baseurl}/deb"
                relurl = f"r{self.release}/vm"
                url = f"{baseurl}/{relurl}/"
                files = [os.path.join(relurl, f)
                         for f in self.get_rsync_files(url)
                         if f.endswith(".buildinfo")]
                files = [os.path.join("https://deb.qubes-os.org", f)
                         for f in files]
            else:
                raise RebuilderExceptionGet(f"Unknown dist: {self.distribution}")
        except (ValueError, FileNotFoundError) as e:
            raise RebuilderExceptionGet(f"Failed to sync repository: {str(e)}")
        return files

    def get_packages(self):
        packages = {}
        latest_packages = []
        for f in self.get_buildinfo_files():
            if is_fedora(self.distribution):
                parsed_bn = parse_rpm_buildinfo_fname(f)
                if not parsed_bn:
                    continue
                if parsed_bn['arch'] not in ("noarch", self.arch):
                    continue
            elif is_debian(self.distribution):
                resp = requests.get(f)
                if not resp.ok:
                    continue
                parsed_bn = parse_deb_buildinfo_fname(f)
                if not parsed_bn:
                    continue
                # fixme: QubesOS does not distinguish "all" and "amd64" in buildinfo names
                parsed_buildinfo = debian.deb822.BuildInfo(resp.content)
                architecture = [arch for arch in parsed_buildinfo["Architecture"].split()
                                if arch not in ("source", "all")]
                if architecture:
                    # fixme: cannot predict which binary arch will be built
                    build_arch = "amd64"
                elif "all" in parsed_buildinfo["Architecture"].split():
                    build_arch = "all"
                else:
                    continue
                # self.arch is the request arch to rebuild
                self.arch = DEBIAN_ARCHES.get(self.arch, self.arch)
                if self.arch != build_arch:
                    continue
                if '+deb{}u'.format(DEBIAN.get(self.distribution)) not in \
                        parsed_buildinfo['version']:
                    continue
            else:
                continue
            if not packages.get(parsed_bn['name'], []):
                packages[parsed_bn['name']] = []
            rebuild = QubesPackage(
                name=parsed_bn['name'],
                epoch=parsed_bn['epoch'],
                version=parsed_bn['version'],
                arch=self.arch,
                distribution=self.qubes_dist,
                buildinfos={"old": f},
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


class RebuilderDist:
    def __init__(self, dist):
        try:
            # 'dist' is defined as:
            #   {distribution}+{package_set_1}+{package_set_2}+...+{package_set_N}.{arch}
            #  where 'distribution' is the distribution name, 'package_set_*' defines known
            #  distribution set of packages and 'arch' is architecture.

            # Examples:
            # qubes-4.1-vm-bullseye.amd64
            # qubes-4.1-vm-fc32.noarch
            # sid.all
            # bullseye+essential+build_essential.all
            # fedora-33.amd64

            self.distribution_with_package_sets, self.arch = dist.rsplit('.', 1)
            self.distribution, package_sets = \
                f"{self.distribution_with_package_sets}+".split('+', 1)
            self.package_sets = [pkg_set for pkg_set in package_sets.split('+')
                                 if pkg_set]
            # If no package set is provided, we understand it as "full"
            if not self.package_sets:
                self.package_sets = ["full"]
            self.project = get_project(self.distribution)
        except ValueError:
            raise RebuilderExceptionDist(f"Cannot parse dist: {dist}.")

        if is_qubes(self.distribution):
            self.repo = QubesRepository(self.distribution, self.arch)
        elif is_fedora(self.distribution):
            self.repo = FedoraRepository(self.distribution)
        elif is_debian(self.distribution):
            self.repo = DebianRepository(self.distribution, self.arch, self.package_sets)
        else:
            raise RebuilderExceptionDist(f"Unsupported distribution: {dist}")

    def __repr__(self):
        result = f'{self.distribution}.{self.arch}'
        return result
