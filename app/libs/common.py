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

import time
import hashlib
import json

from pymongo import MongoClient

DEBIAN = {
    "buster": "10",
    "bullseye": "11",
    "bookworm": "12",
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


class MongoCli:
    def __init__(self, uri):
        self.conn = MongoClient(uri, connect=False)
        self.db = None

    @staticmethod
    def _gen_id(patterns):
        return hashlib.md5(''.join(patterns).encode()).hexdigest()

    def connect(self, db):
        self.db = self.conn[db]

    def clear(self, do=False):
        if do:
            self.db["builds"].drop()

    def get(self, col, query):
        results = []
        collection = self.db[col]
        queried_results = list(collection.find(query))
        for result in queried_results:
            # del result['_id']
            results.append(result)

        return results

    def insert(self, col, data, provided_id=None):
        collection = self.db[col]
        if provided_id:
            data['_id'] = provided_id
        inserted_id = collection.insert_one(data).inserted_id
        return inserted_id

    def update(self, col, data, provided_id):
        collection = self.db[col]

        updated = collection.update_one({'_id': provided_id}, {'$set': data})

        return provided_id, updated.modified_count == 0

    def delete(self, col, input_id):
        collection = self.db[col]
        deleted = collection.delete_one({'_id': input_id})

        return deleted.deleted_count == 0

    def dump(self, col, limit, keep_id):
        collection = self.db[col]
        cursor = collection.find()
        data = list(cursor)
        dumped_data = []
        for num, doc in enumerate(data):
            if not keep_id:
                del doc["_id"]
            dumped_data.append(doc)
            if limit and limit == num:
                break

        return dumped_data

    def load(self, col, src):
        collection = self.db[col]
        with open(src, 'r') as src_fd:
            data = json.loads(src_fd.read())
        try:
            collection.insert_many(data)
        except Exception as e:
            print(e)
            pass
