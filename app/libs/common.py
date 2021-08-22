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

import json

DEBIAN = {
    "buster": "10",
    "bullseye": "11",
    "bookworm": "12",
    "trixie": "13",
    "unstable": ""
}

DEBIAN_ARCHES = {
    "x86_64": "amd64",
    "noarch": "all"
}


def is_qubes(dist):
    return dist.startswith("qubes")


def is_fedora(dist):
    return dist.startswith("fedora") or dist.startswith("fc")


def is_debian(dist):
    dist, package_sets = "{}+".format(dist).split('+', 1)
    return DEBIAN.get(dist, None) is not None


def get_backend_tasks(app):
    backend = app.backend
    col = backend.collection.find()
    results = []
    for _, doc in enumerate(col):
        r = {}
        for f in ['status', 'result']:
            value = doc[f]
            if f == 'result' and isinstance(doc[f], str):
                value = json.loads(doc[f])
            r[f] = value
        results.append(r)
    return results
