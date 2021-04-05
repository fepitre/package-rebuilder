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
import requests

from app.celery import app
from app.libs.logger import log
from app.config.config import Config
from app.libs.exceptions import RebuilderExceptionGet, \
    RebuilderExceptionUpload, RebuilderExceptionBuild, \
    RebuilderExceptionRecord, RebuilderException, \
    RebuilderExceptionDist
from app.libs.getter import BuildPackage, RebuilderDist
from app.libs.rebuilder import getRebuilder
from app.libs.recorder import Recorder


class RebuilderTask(celery.Task):
    pass


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


@app.task(base=RebuilderTask)
def state():
    import numpy as np
    import matplotlib.pyplot as plt
    from packaging.version import parse as parse_version

    def func(pct, allvals):
        absolute = round(pct / 100. * np.sum(allvals))
        return "{:.1f}%\n({:d})".format(pct, absolute)

    try:
        db = Recorder(Config['mongodb'])
        data = [x for x in db.dump_buildrecord()]
        for dist in Config['dist'].split():
            dist = RebuilderDist(dist)
            data_dist = [x for x in data
                         if x['dist'] == dist.name and x['arch'] == dist.arch]
            data_ordered = {}
            for x in data_dist:
                if data_ordered.get(x["name"], None):
                    if parse_version(x["version"]) <= parse_version(
                            data_ordered[x["name"]]["version"]):
                        continue
                data_ordered[x["name"]] = x

            for pkgset_name in dist.package_sets:
                if dist.distribution == "debian":
                    url = f"https://jenkins.debian.net/userContent/reproducible/" \
                          f"debian/pkg-sets/{dist.name}/{pkgset_name}.pkgset"
                    resp = requests.get(url)
                    if not resp.ok:
                        continue
                    content = resp.text.rstrip('\n').split('\n')
                else:
                    content = data_ordered.keys()

                result = {"repro": [], "unrepro": [], "fail": [], "pending": []}
                for pkg_name in content:
                    if data_ordered.get(pkg_name, None):
                        if data_ordered[pkg_name]["status"] == "reproducible":
                            result["repro"].append(pkg_name)
                        elif data_ordered[pkg_name]["status"] == "unreproducible":
                            result["unrepro"].append(pkg_name)
                        elif data_ordered[pkg_name]["status"] == "fail":
                            result["fail"].append(pkg_name)
                    else:
                        result["pending"].append(pkg_name)

                x = []
                legends = []
                explode = []
                colors = []
                if result["repro"]:
                    count = len(result["repro"])
                    x.append(count)
                    legends.append(f"Reproducible")
                    colors.append("green")
                    explode.append(0)
                if result["unrepro"]:
                    count = len(result["unrepro"])
                    x.append(count)
                    legends.append(f"Unreproducible")
                    colors.append("orange")
                    explode.append(0)
                if result["fail"]:
                    count = len(result["fail"])
                    x.append(count)
                    legends.append(f"Failed")
                    colors.append("red")
                    explode.append(0)
                if result["pending"]:
                    count = len(result["pending"])
                    x.append(count)
                    legends.append(f"Pending")
                    colors.append("grey")
                    explode.append(0)

                fig, ax = plt.subplots(figsize=(9, 6), subplot_kw=dict(aspect="equal"))
                wedges, texts, autotexts = ax.pie(x, colors=colors, explode=explode, autopct=lambda pct: func(pct, x), shadow=True, startangle=90, normalize=True)
                ax.legend(wedges, legends, title="Status", loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))
                ax.set(aspect="equal", title=f"{dist.name}+{pkgset_name}.{dist.arch}")
                fig.savefig(f"/rebuild/{dist.distribution}/{dist.name}_{pkgset_name}.{dist.arch}.png")
        upload.delay()
    except (pymongo.errors.ServerSelectionTimeoutError, RebuilderExceptionDist, FileNotFoundError, ValueError) as e:
        raise RebuilderException("{}: failed to generate status.".format(str(e)))


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    schedule = Config['schedule']
    for dist in Config['dist'].split():
        sender.add_periodic_task(schedule, get.s(dist), name=dist)


@app.task(base=RebuilderTask)
def get(dist):
    status = True
    try:
        dist = RebuilderDist(dist)
        packages = dist.repo.get_buildpackages(dist.arch)
        if not packages:
            log.debug(f"No packages found for {dist}")
        db = Recorder(Config['mongodb'])
        for name in packages.keys():
            if not packages[name]:
                log.debug(f"Nothing to build for package {name} ({dist})")
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
            if buildrecord and buildrecord['status'] in ("reproducible", "unreproducible"):
                log.debug(f"{package}: already built.")
                continue
            if not is_task_active_or_reserved_or_scheduled(package):
                rebuild.delay(package)
            else:
                log.debug(f"{package}: already submitted.")
    except RebuilderExceptionDist:
        log.error(f"Cannot parse dist: {dist}.")
        status = False
    except (RebuilderExceptionGet, pymongo.errors.ServerSelectionTimeoutError) as e:
        log.error(str(e))
        status = False
    return status


@app.task(base=RebuilderTask)
def rebuild(package):
    status = "fail"
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
    metadata_unrepr = os.path.join(
        builder.get_output_dir(unreproducible=True), 'metadata')
    if not os.path.exists(metadata) and not os.path.exists(metadata_unrepr):
        try:
            status = builder.run()
        except RebuilderExceptionBuild as e:
            log.error(str(e))
        upload.delay()
    else:
        if metadata:
            status = "reproducible"
        elif metadata_unrepr:
            status = "unreproducible"
        log.debug("{}: in-toto metadata already exists.".format(package))
    record.delay(package, status)
    return status


@app.task(base=RebuilderTask, default_retry_delay=60, max_retries=3,
          autoretry_for=[RebuilderExceptionRecord])
def record(package, build_status):
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
            package.status = build_status
            status = db.insert_buildrecord(package)
        else:
            if buildrecord.retry >= 3:
                log.error("{}: max retries".format(package))
            else:
                log.debug("{}: retry.".format(package))
                buildrecord.retry += 1
            status = db.update_buildrecord(buildrecord)
    except pymongo.errors.ServerSelectionTimeoutError as e:
        raise RebuilderExceptionRecord("{}: failed to save.".format(str(e)))
    state.delay()
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
