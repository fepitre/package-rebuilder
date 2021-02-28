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

import celery
import pymongo
import subprocess
import os

from app.celery import app
from app.libs.logger import log
from app.config.config import Config
from app.libs.exceptions import RebuilderExceptionGet, \
    RebuilderExceptionUpload, RebuilderExceptionBuild, \
    RebuilderExceptionRecord
from app.libs.getter import getRepository, BuildPackage
from app.libs.rebuilder import getRebuilder
from app.libs.recorder import Recorder


class RebuilderTask(celery.Task):

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        if self.max_retries == self.request.retries:
            debug_msg = 'max_retries'
        else:
            debug_msg = exc

        # fail.delay(args=args, debug=debug_msg)


def is_task_active_or_reserved_or_scheduled(args):
    inspect = app.control.inspect()
    queues = []
    tasks_args = []
    active = inspect.active()
    reserved = inspect.reserved()
    scheduled = inspect.scheduled()
    if active:
        queues.append(active)
    if reserved:
        queues.append(reserved)
    if scheduled:
        queues.append(scheduled)
    for d in queues:
        for _, queue in d.items():
            for task in queue:
                if task.get('args'):
                    tasks_args.append(task['args'][0])
    return dict(args) in tasks_args


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    schedule = Config['schedule']
    for dist in Config['dist'].split():
        sender.add_periodic_task(schedule, get.s(dist, "x86_64"), name=dist)


@app.task(base=RebuilderTask)
def get(dist, arch):
    status = True
    repo = getRepository(dist)
    try:
        packages = repo.get_buildpackages(arch)
        db = Recorder(Config['mongodb'])
        for name in packages.keys():
            if not packages[name]:
                # log.error(f"Nothing to build for package {name} ({dist})")
                continue
            package = packages[name][0]
            # We have implemented a retry information to prevent useless retry
            # from periodic tasks submission. This is to ensure fail status
            # in case of non-reproducibility. Limitation here is retry due
            # to network issues. There could be some improvement by storing
            # celery status/result in a mongodb backend directly.
            buildrecord = db.get_buildrecord(package)
            if buildrecord and buildrecord['retry'] == 3:
                log.error(f"{package}: max retry reached.")
                continue
            if buildrecord and buildrecord['status'] == "success":
                log.debug(f"{package}: already built.")
                continue
            if not is_task_active_or_reserved_or_scheduled(package):
                rebuild.delay(package)
            else:
                log.debug(f"{package}: already submitted.")
    except (RebuilderExceptionGet, pymongo.errors.ServerSelectionTimeoutError) as e:
        log.error(str(e))
        status = False
    return status


@app.task(base=RebuilderTask)
def rebuild(package):
    status = True
    # TODO: improve serialize/deserialize Package
    try:
        package = BuildPackage.from_dict(package)
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionBuild(str(e))
    builder = getRebuilder(package=package,
                           snapshot_query_url=Config['snapshot'],
                           sign_keyid=Config['sign_keyid'])
    metadata = os.path.join(builder.get_output_dir(), 'metadata')
    if not os.path.exists(metadata):
        try:
            builder.run()
        except RebuilderExceptionBuild as e:
            log.error(str(e))
            status = False
        record.delay(package, status)
        upload.delay()
    else:
        log.debug("{}: in-toto metadata already exists.".format(package))
    return status


@app.task(base=RebuilderTask, default_retry_delay=60, max_retries=3,
          autoretry_for=[RebuilderExceptionRecord])
def record(package, build_status):
    status = True
    try:
        package = BuildPackage.from_dict(package)
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionRecord(str(e))
    try:
        db = Recorder(Config['mongodb'])
        buildrecord = db.get_buildrecord(package)
        if not buildrecord:
            log.debug("{}: new buildrecord.".format(package))
            if build_status:
                package.status = "success"
            status = db.insert_buildrecord(package)
        else:
            if not build_status:
                if buildrecord.retry >= 3:
                    log.error("{}: max retries".format(package))
                else:
                    log.debug("{}: retry.".format(package))
                    buildrecord.retry += 1
                status = db.update_buildrecord(
                    buildrecord)
    except pymongo.errors.ServerSelectionTimeoutError as e:
        raise RebuilderExceptionRecord("{}: failed to save.".format(str(e)))

    return status


@app.task(base=RebuilderTask, default_retry_delay=60, max_retries=3,
          autoretry_for=[RebuilderExceptionUpload])
def upload():
    status = True
    try:
        if Config['ssh_key'] and Config['remote_ssh_host'] and \
                Config['remote_ssh_basedir']:
            # pay attention to latest "/", we use rsync!
            dir_to_upload = ["/rebuild/"]

            for local_dir in dir_to_upload:
                remote_host = Config['remote_ssh_host']
                remote_basedir = Config['remote_ssh_basedir']
                remote_dir = "{}/{}".format(remote_basedir, local_dir)
                remote_path = "{}:{}".format(remote_host, remote_dir)

                createfolder_cmd = [
                    "ssh", "-i", "/root/.ssh/{}".format(Config['ssh_key']),
                    "-o", "StrictHostKeyChecking=no", "{}".format(remote_host),
                    "mkdir", "-p", "{}".format(remote_dir)
                ]
                subprocess.run(createfolder_cmd, check=True)

                cmd = [
                    "rsync", "-av", "--progress", "-e",
                    "ssh -i /root/.ssh/{} -o StrictHostKeyChecking=no".format(
                        Config['ssh_key']),
                    local_dir, remote_path
                ]
                subprocess.run(cmd, check=True)
        else:
            log.critical('Missing SSH key or SSH remote destination')
            status = False
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
        log.error(str(e))
        raise RebuilderExceptionUpload("Failed to upload")
    return status
