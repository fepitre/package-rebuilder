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
import uuid

import celery.bootsteps
import subprocess
import os
import shutil

from app.celery import app
from app.lib.log import log
from app.config.config import Config
from app.lib.exceptions import RebuilderException, \
    RebuilderExceptionUpload, RebuilderExceptionBuild, RebuilderExceptionReport, \
    RebuilderExceptionDist, RebuilderExceptionAttest, RebuilderExceptionGet
from app.lib.common import get_project
from app.lib.get import getPackage, RebuilderDist
from app.lib.tool import metadata_to_db, get_rebuild_packages, get_celery_queued_tasks
from app.lib.rebuild import getRebuilder
from app.lib.attest import process_attestation
from app.lib.report import generate_results


# fixme: improve serialize/deserialize Package

class BaseTask(celery.Task):
    autoretry_for = (RebuilderExceptionBuild, RebuilderExceptionAttest,)
    throws = (RebuilderException,)
    max_retries = Config["celery"]["max_retries"]
    # Let snapshot service to get the latest data from official repositories
    default_retry_delay = 60 * 60


class RebuildTask(BaseTask):

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        results, = exc.args
        package = results[0]
        # Ensure to keep a trace of retries for backend
        package["retries"] = self.request.retries
        package["status"] = "retry"
        report.delay(package)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        results, = exc.args
        package = results[0]
        report.delay(package)

    def on_success(self, retval, task_id, args, kwargs):
        package = retval["rebuild"][0]
        attest.delay(package)


@app.task(base=BaseTask)
def _generate_results(project):
    # generate plots from results
    try:
        log.debug(f"Generating results for project {project}")
        generate_results(app, project)
    except RebuilderException as e:
        log.error(f"Failed to generate plots: {str(e)}")
    upload.delay(project=project, upload_results=True)


@app.task(base=BaseTask)
def _metadata_to_db(dist):
    try:
        dist = RebuilderDist(dist)
        log.debug(f"Provisionning DB for {dist} data)")
        for p in metadata_to_db(app, dist):
            app.backend._store_result(
                task_id=uuid.uuid4(),
                result={"report": [p]},
                state="SUCCESS"
            )
    except Exception as e:
        log.error(f"Failed to generate DB results: {str(e)}")


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    for project in Config["project"].keys():
        schedule_get = Config["project"][project]["schedule_get"]
        for dist in Config["project"][project]["dist"]:
            sender.add_periodic_task(schedule_get, get.s(dist), name=dist)

        # fixme: improve how we expose results
        schedule_generate_results = Config["project"][project]["schedule_generate_results"]
        sender.add_periodic_task(schedule_generate_results, _generate_results.s(project))


@app.task(base=BaseTask)
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
            stored_packages = get_rebuild_packages(app)

            # queued packages to be rebuilt
            rebuild_queued_tasks = get_celery_queued_tasks(app, "rebuild")

            for package in packages:
                # check if package has already been triggered for build
                stored_package = stored_packages.get(str(package), None)
                if stored_package and stored_package.status in \
                        ("reproducible", "unreproducible", "failure", "retry"):
                    if stored_package.status in ("reproducible", "unreproducible"):
                        log.debug(f"{package}: already built ({stored_package.status}). Skipping")
                        continue
                    if stored_package.status == "failure":
                        if not force_retry:
                            log.debug(f"{package}: already built ({stored_package.status}). Skipping")
                            continue
                    if stored_package.status == "retry":
                        log.debug(f"{package}: already submitted. Skipping.")
                        continue

                if dict(package) not in rebuild_queued_tasks:
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


@app.task(base=RebuildTask)
def rebuild(package):
    try:
        package = getPackage(package)
    except Exception as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionBuild from e
    builder = getRebuilder(package.distribution)
    package = builder.run(package=package)
    result = {"rebuild": [dict(package)]}
    return result


@app.task(base=BaseTask)
def attest(package):
    try:
        package = getPackage(package)
    except Exception as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionAttest from e

    project = get_project(package.distribution)
    if not project:
        raise RebuilderExceptionAttest(f"Cannot determine underlying project for {package}")

    if package.status not in ("reproducible", "unreproducible"):
        raise RebuilderExceptionAttest(f"Cannot determine package status for {package}")

    if not os.path.exists(package.artifacts):
        raise RebuilderExceptionAttest(f"Cannot find package artifacts for {package}")

    gpg_sign_keyid = Config["project"].get(project, {}).get('in-toto-sign-key-fpr', None)
    gpg_sign_keyid_unreproducible = Config["project"].get(project, {}).get('in-toto-sign-key-unreproducible-fpr', None)
    if gpg_sign_keyid and gpg_sign_keyid_unreproducible:
        if not os.path.exists(f"{package.artifacts}/summary.out"):
            raise RebuilderExceptionAttest(f"Cannot find summary results for {package}")
        with open(f"{package.artifacts}/summary.out") as fd:
            summary = json.loads(fd.read())

        if not summary.get("sha256", None):
            raise RebuilderExceptionAttest(f"Missing sha256 entries in summary!")
        files = summary["sha256"]
        repr_files = [f for f in files.keys()
                      if files[f]["sha256"]["old"] == files[f]["sha256"]["new"]]
        unrepr_files = [f for f in files.keys()
                        if files[f]["sha256"]["old"] != files[f]["sha256"]["new"]]

        # generate in-toto reproducible metadata
        if repr_files:
            process_attestation(
                package=package,
                gpg_sign_keyid=gpg_sign_keyid,
                files=repr_files,
                reproducible=True,
            )
        # generate in-toto unreproducible metadata
        if unrepr_files:
            process_attestation(
                package=package,
                gpg_sign_keyid=gpg_sign_keyid_unreproducible,
                files=unrepr_files,
                reproducible=False,
            )
    else:
        log.info(f"Unable to sign in-toto reproducible/unreproducible metadata: "
                 f"no GPG keyid provided for project '{project}.")

    report.delay(package)
    result = {"attest": [dict(package)]}
    return result


@app.task(base=BaseTask)
def report(package):
    try:
        package = getPackage(package)
    except Exception as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionReport from e

    builder = getRebuilder(package.distribution)
    output_dir = f"/rebuild/{builder.project}"

    # collect log
    log_dir = f"{output_dir}/logs"
    os.makedirs(log_dir, exist_ok=True)
    src_log = package.log
    log_file = os.path.basename(package.log)
    dst_log = f"{log_dir}/{log_file}"
    if not os.path.exists(src_log):
        raise RebuilderExceptionReport(f"Cannot find build log file {src_log}")
    if not os.path.exists(dst_log):
        shutil.move(src_log, dst_log)

    # store new log location
    package.log = dst_log

    # new buildinfo exists only when build passed
    if package.buildinfos.get("new", None):
        buildinfo_dir = f"{output_dir}/buildinfos"
        os.makedirs(buildinfo_dir, exist_ok=True)
        src_buildinfo = package.buildinfos["new"]
        buildinfo_file = os.path.basename(src_buildinfo)
        dst_buildinfo = f"{buildinfo_dir}/{buildinfo_file}"
        if not os.path.exists(src_buildinfo):
            raise RebuilderExceptionReport(f"Cannot find buildinfo file {src_buildinfo}")
        if not os.path.exists(dst_buildinfo):
            shutil.move(src_buildinfo, dst_buildinfo)

        # store new buildinfo location
        package.buildinfos["new"] = dst_buildinfo

    # collect diffoscope if exists
    diffoscope_src_log = f"{package.artifacts}/diffoscope.out"
    if package.status == "unreproducible" and os.path.exists(diffoscope_src_log):
        diffoscope_dst_log = f"{log_dir}/{os.path.splitext(log_file)[0]}.diffoscope.log"
        if not os.path.exists(diffoscope_dst_log):
            shutil.move(diffoscope_src_log, diffoscope_dst_log)
        if not os.path.exists(diffoscope_dst_log):
            raise RebuilderExceptionReport(
                f"Cannot find build diffoscope log file {diffoscope_dst_log}")
        package.diffoscope = diffoscope_dst_log

    # remove artifacts
    if os.path.exists(package.artifacts):
        shutil.rmtree(package.artifacts)
    else:
        log.error(f"Cannot find package artifacts for cleaning {package}")

    result = {"report": [dict(package)]}
    upload.delay(package)
    return result


@app.task(base=BaseTask)
def upload(package=None, project=None, upload_results=False, upload_all=False):
    try:
        package = getPackage(package) if package else None
    except Exception as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionUpload from e

    ssh_key = Config["common"].get("repo-ssh-key", None)
    remote_ssh_host = Config["common"].get("repo-remote-ssh-host", None)
    remote_ssh_basedir = Config["common"].get("repo-remote-ssh-basedir", None)

    if package:
        project = get_project(package.distribution)

    if not project:
        raise RebuilderExceptionUpload(f"Cannot determine underlying project for {package}")

    ssh_key = Config["project"][project].get(
        "repo-ssh-key", ssh_key)
    remote_ssh_host = Config["project"][project].get(
        "repo-remote-ssh-host", remote_ssh_host)
    remote_ssh_basedir = Config["project"][project].get(
        "repo-remote-ssh-basedir", remote_ssh_basedir)

    try:
        if ssh_key and remote_ssh_host and remote_ssh_basedir:
            # pay attention to latest "/", we use rsync!
            dir_to_upload = [
                f"/rebuild/{project}/logs/",
                f"/rebuild/{project}/buildinfos/",
                f"/rebuild/{project}/sources/",
                f"/rebuild/{project}/unreproducible/sources/",
            ]
            # if upload_all:
            #     dir_to_upload += [
            #         f"/rebuild/{project}/",
            #     ]
            # if package and package.status in ("reproducible", "unreproducible"):
            #     if package.metadata.get("reproducible"):
            #         metadata_path = get_intoto_metadata_package(
            #             package, unreproducible=False
            #         )
            #         dir_to_upload.append(f"{metadata_path}/")
            #     if package.metadata.get("unreproducible"):
            #         metadata_path_unrepr = get_intoto_metadata_package(
            #             package, unreproducible=True
            #         )
            #         dir_to_upload.append(f"{metadata_path_unrepr}/")
            if upload_results:
                dir_to_upload.append(f"/rebuild/{project}/results/")
            for local_dir in dir_to_upload:
                # fixme: maybe ssh keyword is useless: someone could
                #  serve a local mirror directly
                remote_host = remote_ssh_host
                remote_basedir = remote_ssh_basedir
                remote_dir = f"{remote_basedir}/{local_dir}"
                remote_path = f"{remote_host}:{remote_dir}"

                createfolder_cmd = [
                    "ssh", "-i", f"/root/.ssh/{ssh_key}",
                    "-o", "StrictHostKeyChecking=no", remote_host,
                    "mkdir", "-p", remote_dir
                ]
                subprocess.run(createfolder_cmd, check=True)

                cmd = [
                    "rsync", "-av", "--progress", "-e",
                    f"ssh -i /root/.ssh/{ssh_key} -o StrictHostKeyChecking=no",
                    local_dir, remote_path
                ]
                subprocess.run(cmd, check=True)
        else:
            raise FileNotFoundError("Missing SSH key or SSH remote destination")
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
        log.error(str(e))
        raise RebuilderExceptionUpload("Failed to upload")
    result = {"upload": [dict(package)] if package else []}
    return result
