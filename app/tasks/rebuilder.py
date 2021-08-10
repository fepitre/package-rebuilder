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
import celery.bootsteps
import subprocess
import os
import requests
import base64
import json

import sqlalchemy.exc

from app.celery import app
from app.libs.logger import log
from app.config.config import Config
from app.libs.exceptions import RebuilderExceptionGet, \
    RebuilderExceptionUpload, RebuilderExceptionBuild, \
    RebuilderExceptionDist, RebuilderException
from app.libs.getter import BuildPackage, RebuilderDist
from app.libs.rebuilder import getRebuilder


class RebuilderTask(celery.Task):
    autoretry_for = (RebuilderExceptionBuild,)
    max_retries = 2


def get_celery_queued_tasks(queue_name):
    with app.pool.acquire(block=True) as conn:
        tasks = conn.default_channel.client.lrange(queue_name, 0, -1)
        submitted_tasks = []

    for task in tasks:
        j = json.loads(task)
        body = json.loads(base64.b64decode(j['body']))
        submitted_tasks.append(body[0][0])

    return submitted_tasks


def get_celery_active_tasks():
    inspect = app.control.inspect()
    tasks = []
    queues = []
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
                    tasks.append(task['args'][0])
    return tasks


def generate_results():
    import json
    import numpy as np
    import matplotlib.pyplot as plt

    from packaging.version import parse as parse_version
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from celery.backends.database.models import TaskExtended

    url = Config['backend']
    # this is useful if we call the function outside the workers
    if 'CELERY_BACKEND_URL' in os.environ:
        url = os.environ['CELERY_BACKEND_URL']

    engine = create_engine(url.replace('db+sqlite', 'sqlite'))
    Base = declarative_base()
    Base.metadata.bind = engine
    Base.metadata.create_all(engine)
    DBSession = sessionmaker(bind=engine)
    session = DBSession()

    def func(pct, allvals):
        absolute = round(pct / 100. * np.sum(allvals))
        return "{:.1f}%\n({:d})".format(pct, absolute)

    try:
        results = [r.to_dict() for r in session.query(TaskExtended).all()]
        data = [r["result"]["rebuild"] for r in results
                if r.get("result", {}).get("rebuild", None)]
        for dist in Config['dist'].split():
            dist = RebuilderDist(dist)
            results_path = f"/rebuild/{dist.distribution}/results"
            os.makedirs(results_path, exist_ok=True)

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
                    try:
                        resp = requests.get(url)
                        if not resp.ok:
                            continue
                    except requests.exceptions.ConnectionError as e:
                        log.error(f"Failed to get {pkgset_name}: {str(e)}")
                        continue
                    content = resp.text.rstrip('\n').split('\n')
                else:
                    content = data_ordered.keys()

                result = {"repro": [], "unrepro": [], "fail": [], "pending": []}
                for pkg_name in data_ordered.keys():
                    if pkg_name not in content:
                        continue
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
                fig.savefig(f"{results_path}/{dist.name}_{pkgset_name}.{dist.arch}.png")

                with open(f"{results_path}/{dist}_db.json", "w") as fd:
                    fd.write(json.dumps(data_ordered, indent=2) + "\n")
        upload.delay()
    except (RebuilderExceptionDist, FileNotFoundError, ValueError, sqlalchemy.exc.SQLAlchemyError) as e:
        raise RebuilderException("{}: failed to generate status.".format(str(e)))


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    schedule = Config['schedule']
    for dist in Config['dist'].split():
        sender.add_periodic_task(schedule, get.s(dist), name=dist)


@app.task(base=RebuilderTask)
def get(dist):
    result = {"get": []}
    if dist in get_celery_queued_tasks("get"):
        log.debug(f"{dist}: already submitted. Skipping.")
    else:
        try:
            dist = RebuilderDist(dist)
            packages = dist.repo.get_buildpackages(dist.arch)
            if not packages:
                log.debug(f"No packages found for {dist}")
            for name in packages.keys():
                if not packages[name]:
                    log.debug(f"Nothing to build for package {name} ({dist})")
                    continue
                package = packages[name][0]
                if dict(package) not in get_celery_queued_tasks("rebuild"):
                    rebuild.delay(package)
                    result["get"].append(dict(package))
                    log.debug(f"{package}: submitted for rebuild.")
                else:
                    log.debug(f"{package}: already submitted. Skipping.")
        except RebuilderExceptionDist:
            log.error(f"Cannot parse dist: {dist}.")
        except RebuilderExceptionGet as e:
            log.error(str(e))
    return result


@app.task(base=RebuilderTask)
def rebuild(package):
    # TODO: improve serialize/deserialize Package
    try:
        package = BuildPackage.from_dict(package)
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionBuild from e
    builder = getRebuilder(
        package=package,
        snapshot_query_url=Config['snapshot'],
        snapshot_mirror=Config['snapshot'],
        sign_keyid=Config['sign_keyid']
    )
    metadata = os.path.join(builder.get_output_dir(), 'metadata')
    metadata_unrepr = os.path.join(builder.get_output_dir(unreproducible=True), 'metadata')
    if not os.path.exists(metadata) and not os.path.exists(metadata_unrepr):
        try:
            package = builder.run()
        finally:
            upload.delay()
    else:
        if metadata:
            package.status = "reproducible"
        elif metadata_unrepr:
            package.status = "unreproducible"
        log.debug("{}: in-toto metadata already exists.".format(package))

    result = {"rebuild": dict(package)}
    return result


@app.task(base=RebuilderTask)
def upload():
    status = True

    # generate plots from results
    try:
        generate_results()
    except RebuilderException as e:
        log.error(f"Failed to generate plots: {str(e)}")

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
