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
import glob
import shutil
import debian.deb822

from app.celery import app
from app.libs.logger import log
from app.config.config import Config
from app.libs.exceptions import RebuilderExceptionGet, \
    RebuilderExceptionUpload, RebuilderExceptionBuild, \
    RebuilderExceptionDist, RebuilderExceptionAttest, RebuilderException
from app.libs.common import get_celery_queued_tasks
from app.libs.getter import BuildPackage, RebuilderDist, get_rebuilt_packages
from app.libs.rebuilder import getRebuilder
from app.libs.attester import generate_intoto_metadata, get_intoto_metadata_output_dir
from app.libs.reporter import generate_results


class RebuilderTask(celery.Task):
    autoretry_for = (RebuilderExceptionBuild, RebuilderExceptionUpload)
    throws = (RebuilderException,)
    max_retries = Config['max_retries']
    # Let snapshot service to get the latest data from official repositories
    default_retry_delay = 60 * 60

# TODO: improve serialize/deserialize Package


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


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    schedule = Config['schedule']
    for dist in Config['dist'].split():
        sender.add_periodic_task(schedule, get.s(dist), name=dist)


@app.task(base=RebuilderTask)
def get(dist, force_retry=False):
    result = {}
    if dist in get_celery_queued_tasks(app, "get"):
        log.debug(f"{dist}: already submitted. Skipping.")
    else:
        try:
            dist = RebuilderDist(dist)
            packages = dist.repo.get_packages_to_rebuild()
            if not packages:
                log.debug(f"No packages found for {dist}")

            # get previous triggered packages builds
            stored_packages = get_rebuilt_packages(app)

            for package in packages:
                # check if metadata exists
                metadata = os.path.join(get_intoto_metadata_output_dir(package), 'metadata')
                metadata_unrepr = os.path.join(
                    get_intoto_metadata_output_dir(package, unreproducible=True), 'metadata')
                if os.path.exists(metadata):
                    package.status = "reproducible"
                elif os.path.exists(metadata_unrepr):
                    package.status = "unreproducible"
                if package.status in ("reproducible", "unreproducible"):
                    log.debug(f"{package}: already built ({package.status}). Skipping")
                    result.setdefault("rebuild", []).append(dict(package))
                    continue

                # check if package has already been triggered for build
                stored_package = None
                for p in stored_packages:
                    if p == package:
                        stored_package = p
                        break
                if stored_package and stored_package.status in ("reproducible", "unreproducible"):
                    log.debug(f"{package}: already built ({stored_package.status}). Skipping")
                    continue
                if stored_package and stored_package.status == "fail":
                    if not force_retry:
                        log.debug(f"{package}: already built ({stored_package.status}). Skipping")
                        continue
                if stored_package and stored_package.status == "retry":
                    log.debug(f"{package}: already submitted. Skipping.")
                    continue

                if dict(package) not in get_celery_queued_tasks(app, "rebuild"):
                    log.debug(f"{package}: submitted for rebuild.")
                    # Add rebuild task
                    rebuild.delay(package)
                    # For debug purposes
                    result.setdefault("get", []).append(dict(package))
                else:
                    log.debug(f"{package}: already submitted. Skipping.")
        except RebuilderExceptionDist:
            log.error(f"Cannot parse dist: {dist}.")
        except RebuilderExceptionGet as e:
            log.error(str(e))
    return result


@app.task(base=RebuilderTask)
def rebuild(package):
    try:
        package = BuildPackage.from_dict(package)
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionBuild from e
    try:
        builder = getRebuilder(
            package=package,
            snapshot_query_url=Config['snapshot'],
            snapshot_mirror=Config['snapshot']
        )
        package = builder.run()
    except RebuilderExceptionBuild as e:
        args, = e.args
        package = args[0]
        # Ensure to keep a trace of retries for backend
        package["retries"] = rebuild.request.retries
        upload.delay(package)
        raise RebuilderExceptionBuild(package)
    attest.delay(package)
    result = {"rebuild": [dict(package)]}
    return result


@app.task(base=RebuilderTask)
def attest(package):
    try:
        package = BuildPackage.from_dict(package)
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionAttest from e

    if package.status not in ("reproducible", "unreproducible"):
        raise RebuilderExceptionAttest(f"Cannot determine package status for {package}")

    os.chdir(package.artifacts)
    buildinfo = glob.glob(f"{package.name}*.buildinfo")
    if not buildinfo:
        raise RebuilderExceptionAttest(f"Cannot find buildinfo for {package}")
    buildinfo = buildinfo[0]
    with open(buildinfo) as fd:
        parsed_buildinfo = debian.deb822.BuildInfo(fd)

    # generate in-toto metadata
    generate_intoto_metadata(package.artifacts, Config['sign_keyid'], parsed_buildinfo)
    link = glob.glob("rebuild*.link")
    if not link:
        raise RebuilderExceptionAttest(f"Cannot find link for {package}")
    link = link[0]

    # create final output directory
    outputdir = get_intoto_metadata_output_dir(package, unreproducible=package.status == "unreproducible")
    os.makedirs(outputdir, exist_ok=True)
    shutil.copy2(os.path.join(package.artifacts, buildinfo), outputdir)
    shutil.copy2(os.path.join(package.artifacts, link), outputdir)

    # create symlink to new buildinfo and rebuild link file
    os.chdir(outputdir)
    if not os.path.exists("buildinfo"):
        os.symlink(buildinfo, "buildinfo")
    if not os.path.exists("metadata"):
        os.symlink(link, "metadata")

    os.chdir(os.path.join(outputdir, '../../'))
    for binpkg in parsed_buildinfo.get_binary():
        if not os.path.exists(binpkg):
            os.symlink(package.name, binpkg)

    # remove artifacts
    shutil.rmtree(package.artifacts)

    upload.delay(package)
    result = {"attest": [dict(package)]}
    return result


@app.task(base=RebuilderTask)
def upload(package):
    try:
        package = BuildPackage.from_dict(package)
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionUpload from e

    # collect log
    builder = getRebuilder(package=package)
    output_dir = f"/rebuild/{builder.distdir}"
    if package.status == "reproducible":
        log_dir = f"{output_dir}/log-ok"
    elif package.status == "unreproducible":
        log_dir = f"{output_dir}/log-ok-unreproducible"
    else:
        log_dir = f"{output_dir}/log-fail"
    os.makedirs(log_dir, exist_ok=True)
    dst_log = f"{log_dir}/{package.log}"
    if not os.path.exists(dst_log):
        shutil.move(f"/artifacts/{builder.distdir}/{package.log}", dst_log)

    # generate plots from results
    try:
        generate_results(app)
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
            raise FileNotFoundError('Missing SSH key or SSH remote destination')
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
        log.error(str(e))
        raise RebuilderExceptionUpload("Failed to upload")
    result = {"upload": [dict(package)]}
    return result
