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
import json
import base64

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


def is_qubes(dist):
    return dist.startswith("qubes")


def is_fedora(dist):
    return dist.startswith("fedora") or dist.startswith("fc")


def is_debian(dist):
    dist, package_sets = f"{dist}+".split('+', 1)
    return DEBIAN.get(dist, None) is not None


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


def get_celery_active_tasks(app, name=None):
    inspect = app.control.inspect()
    tasks = []
    queues = []
    active = inspect.active()
    if active:
        queues.append(active)
    for d in queues:
        for _, queue in d.items():
            for task in queue:
                if name and task.get("name", None) != name:
                    continue
                if task.get('args', None):
                    tasks.append(task['args'][0])
    return tasks


def rebuild_task_parser(task):
    parsed_task = None
    if task["status"] == 'SUCCESS' and isinstance(task["result"], dict)\
            and task["result"].get("rebuild", None):
        parsed_task = task["result"]["rebuild"]
    elif (task["status"] == 'FAILURE' or task["status"] == 'RETRY') \
            and task["result"]["exc_type"] == "RebuilderExceptionBuild":
        # We have stored package info in exception
        parsed_task = task["result"]["exc_message"][0]
        parsed_task[0]["status"] = task["status"].lower()
    return parsed_task


def get_celery_queued_tasks(app, queue_name):
    with app.pool.acquire(block=True) as conn:
        tasks = conn.default_channel.client.lrange(queue_name, 0, -1)

    submitted_tasks = []
    for task in tasks:
        j = json.loads(task)
        body = json.loads(base64.b64decode(j['body']))
        submitted_tasks.append(body[0][0])
    return submitted_tasks


def get_celery_unacked_tasks(app):
    with app.pool.acquire(block=True) as conn:
        tasks = conn.default_channel.client.hvals("unacked")

    submitted_tasks = []
    for task in tasks:
        task = task.decode("utf-8")
        j = json.loads(task)
        if not isinstance(j, list):
            continue
        j = j[0]
        body = json.loads(base64.b64decode(j['body']))
        submitted_tasks.append(body[0][0])
    return submitted_tasks


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


def delete_backend_tasks(app, status):
    backend = app.backend
    col = backend.collection.find()
    results = []
    for _, doc in enumerate(col):
        if doc["status"] == status:
            backend.collection.delete_one({"_id": doc["_id"]})
            results.append(doc)
    return results
