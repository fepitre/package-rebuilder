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
from app.libs.getter import BuildPackage
from app.libs.exceptions import RebuilderExceptionRecord


class Recorder:
    def __init__(self, uri):
        self.conn = MongoClient(uri, connect=False)
        self.db = self.conn['RebuilderDB']

    @staticmethod
    def _gen_id(patterns):
        return hashlib.md5(''.join(patterns).encode()).hexdigest()

    def _clear(self, do=False):
        if do:
            self.db["builds"].drop()

    def _get(self, col, query):
        results = []
        collection = self.db[col]
        queried_results = list(collection.find(query))
        for result in queried_results:
            # del result['_id']
            results.append(result)

        return results

    def _insert(self, col, data, provided_id=None):
        collection = self.db[col]
        if provided_id:
            data['_id'] = provided_id
        inserted_id = collection.insert_one(data).inserted_id
        return inserted_id

    def _update(self, col, data, provided_id):
        collection = self.db[col]

        updated = collection.update_one({'_id': provided_id}, {'$set': data})

        return provided_id, updated.modified_count == 0

    def _delete(self, col, input_id):
        collection = self.db[col]
        deleted = collection.delete_one({'_id': input_id})

        return deleted.deleted_count == 0

    def _dump(self, col, limit, keep_id):
        collection = self.db[col]
        cursor = collection.find()
        data = list(cursor)
        dumped_data = []
        for num, doc in enumerate(data):
            if not keep_id:
                del doc["_id"]
            else:
                doc["_id"] = str(doc["_id"])
            dumped_data.append(doc)
            if limit and limit == num:
                break

        return dumped_data

    def _dump_to_stdout(self, col, limit=None, keep_id=False):
        print(json.dumps(self._dump(col=col, limit=limit, keep_id=keep_id),
                         indent=4))

    def _dump_to_json(self, col, limit=None, keep_id=False, dst=None):
        if not dst:
            dst = 'rebuilderdb-data-%d.json' % int(time.time())
        with open(dst, 'w') as dst_fd:
            dst_fd.write(
                json.dumps(self._dump(col=col, limit=limit, keep_id=keep_id),
                           indent=4))

    def _load(self, col, src):
        collection = self.db[col]
        with open(src, 'r') as src_fd:
            data = json.loads(src_fd.read())
        try:
            collection.insert_many(data)
        except Exception as e:
            print(e)
            pass

    def dump_buildrecord(self, limit=None, keep_id=False):
        return self._dump("builds", limit=limit, keep_id=keep_id)

    def dump_buildrecord_to_json(self, limit=None, keep_id=False, dst=None):
        self._dump_to_json("builds", limit=limit, keep_id=keep_id, dst=dst)

    def dump_buildrecord_to_stdout(self, limit=None, keep_id=False):
        self._dump_to_stdout("builds", limit=limit, keep_id=keep_id)

    def insert_buildrecord(self, package):
        buildrecord = dict(package)
        buildrecord_id = self._insert(
            "builds", buildrecord, self._gen_id([str(package)]))
        return buildrecord_id

    def delete_buildrecord(self, package):
        buildrecord_id = self._gen_id([str(package)])
        return self._delete("builds", buildrecord_id)

    def get_buildrecord_with_id(self, buildrecord_id):
        result = {}
        queried_result = self._get('builds', {'_id': buildrecord_id})
        return queried_result[0] if queried_result else result

    def get_buildrecord(self, package):
        buildrecord_id = self._gen_id([str(package)])
        buildrecord = self.get_buildrecord_with_id(buildrecord_id)
        if buildrecord:
            try:
                del buildrecord['_id']
                buildrecord = BuildPackage.from_dict(buildrecord)
            except KeyError as e:
                raise RebuilderExceptionRecord(str(e))
        return buildrecord

    def update_buildrecord(self, package):
        # buildrecord = dict(package)
        # return self._update("builds", buildrecord, self._gen_id([str(package)]))
        # buildrecord = dict(package)
        self.delete_buildrecord(package)
        return self.insert_buildrecord(package)
