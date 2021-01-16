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
import re

from packaging.version import parse as parse_version
from app.libs.exceptions import RebuilderExceptionGet

QUBES_DEB_REPOSITORY = "https://deb.qubes-os.org"
QUBES_RPM_REPOSITORY = "https://yum.qubes-os.org"

DEBIAN = {
    "stretch": "9",
    "buster": "10",
    "bullseye": "11"
}


class BuildPackage(dict):
    def __init__(self, name, version, arch, release, package_set, dist, url,
                 status="fail", retry=0):
        dict.__init__(self, name=name, version=version, arch=arch,
                      release=release, dist=dist, package_set=package_set,
                      url=url, status=status, retry=retry)

    def __getattr__(self, item):
        return self[item]

    def __setattr__(self, key, value):
        self[key] = value

    def __repr__(self):
        return f'{self.release}-{self.package_set}-{self.dist}-{self.name}-{self.version}'

    @classmethod
    def fromdict(cls, pkg):
        return cls(pkg["name"], pkg["version"], pkg["arch"], pkg["release"],
                   pkg["package_set"], pkg["dist"], pkg["url"],
                   pkg["status"], pkg["retry"])


class Repository:
    def __init__(self):
        self.files = []

        try:
            cmd = ["rsync", "--list-only", "--recursive",
                   "--exclude=all-versions",
                   "rsync://deb.qubes-os.org/qubes-mirror/repo/deb/"]
            result = subprocess.check_output(cmd)
            lines = result.decode('utf8').strip('\n').split('\n')
            for line in lines:
                line = line.split()
                if line[0].startswith('d'):
                    continue
                self.files.append(line[-1])
        except FileNotFoundError as e:
            raise RebuilderExceptionGet(
                "Failed to get buildinfo: {}".format(str(e)))

    def get_buildinfos(self):
        files = []
        for f in self.files:
            if f.endswith(".buildinfo"):
                files.append(f)
        return files

    def get_packages(self, release, package_set, dist):
        packages = {}
        for f in self.get_buildinfos():
            if not f.startswith('r%s' % release):
                continue
            basename = os.path.basename(f).replace('.buildinfo', '').split('_')
            # TODO: VM and Debian specific only
            if len(basename) == 3:
                name = basename[0]
                version = basename[1]
                parsed_version = re.match(r'.*\+deb([0-9]+)u.*', version)
                if not DEBIAN.get(dist):
                    continue
                if parsed_version and parsed_version.group(1) != DEBIAN[dist]:
                    continue
                arch = basename[2]
                if not packages.get(name, []):
                    packages[name] = []
                rebuild = BuildPackage(
                    name=name,
                    version=version, arch=arch,
                    release=release,
                    package_set=package_set,
                    dist=dist,
                    url="{}/{}".format(QUBES_DEB_REPOSITORY, f))
                packages[name].append(rebuild)
        for pkg in packages.keys():
            packages[pkg].sort(
                key=lambda pkg: parse_version(pkg.version), reverse=True)
        return packages
