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

try:
    import koji
except ImportError:
    koji = None
try:
    import debian.debian_support
except ImportError:
    debian = None

DEBIAN = {
    "buster": "10",
    "bullseye": "11",
    "bookworm": "12",
    "trixie": "13",
    "sid": "unstable",
    "unstable": "sid"
}

DEBIAN_ARCHES = {
    "x86_64": "amd64",
    "noarch": "all"
}


def is_qubes(distribution):
    return distribution.startswith("qubes")


def is_fedora(distribution):
    return distribution.startswith("fedora") or distribution.startswith("fc")


def is_debian(distribution):
    return DEBIAN.get(distribution, None) is not None


def get_project(distribution):
    if is_qubes(distribution):
        return "qubesos"
    elif is_fedora(distribution):
        return "fedora"
    elif is_debian(distribution):
        return "debian"


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
