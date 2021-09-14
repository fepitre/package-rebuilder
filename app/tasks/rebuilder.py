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
from app.libs.exceptions import RebuilderException, \
    RebuilderExceptionUpload, RebuilderExceptionBuild, RebuilderExceptionReport, \
    RebuilderExceptionDist, RebuilderExceptionAttest, RebuilderExceptionGet
from app.libs.common import get_celery_queued_tasks, get_project
from app.libs.getter import BuildPackage, RebuilderDist, get_rebuilt_packages, metadata_to_db
from app.libs.rebuilder import getRebuilder, get_latest_log_file
from app.libs.attester import generate_intoto_metadata, get_intoto_metadata_package
from app.libs.reporter import generate_results


# fixme: improve serialize/deserialize Package

class BaseTask(celery.Task):
    autoretry_for = (RebuilderExceptionBuild,)
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
        report.delay(package)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        results, = exc.args
        package = results[0]
        report.delay(package)

    def on_success(self, retval, task_id, args, kwargs):
        package = retval["rebuild"][0]
        attest.delay(package)


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    for project in Config["project"].keys():
        schedule = Config["project"][project]["schedule"]
        for dist in Config["project"][project]["dist"]:
            sender.add_periodic_task(schedule, get.s(dist), name=dist)

        # fixme: improve how we expose results
        sender.add_periodic_task(60, _generate_results.s(project))


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
            stored_packages = get_rebuilt_packages(app)

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

                # check if metadata exists: this should be superseded by _metadata_to_db
                if not stored_package:
                    metadata = os.path.join(get_intoto_metadata_package(package), 'metadata')
                    metadata_unrepr = os.path.join(
                        get_intoto_metadata_package(package, unreproducible=True), 'metadata')
                    if os.path.exists(metadata):
                        package.status = "reproducible"
                    elif os.path.exists(metadata_unrepr):
                        package.status = "unreproducible"
                    if package.status in ("reproducible", "unreproducible"):
                        log.debug(f"{package}: already built ({package.status}). Skipping")
                        package.log = get_latest_log_file(package)
                        result.setdefault("rebuild", []).append(dict(package))
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
        package = BuildPackage.from_dict(package)
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionBuild from e
    builder = getRebuilder(package.distribution)
    package = builder.run(package=package)
    result = {"rebuild": [dict(package)]}
    return result


@app.task(base=BaseTask)
def attest(package):
    try:
        package = BuildPackage.from_dict(package)
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionAttest from e

    project = get_project(package.distribution)
    if not project:
        raise RebuilderExceptionAttest(f"Cannot determine underlying project for {package}")

    if package.status not in ("reproducible", "unreproducible"):
        raise RebuilderExceptionAttest(f"Cannot determine package status for {package}")

    if not os.path.exists(package.artifacts):
        raise RebuilderExceptionAttest(f"Cannot find package artifacts for {package}")

    os.chdir(package.artifacts)
    buildinfo = glob.glob(f"{package.name}*.buildinfo")
    if not buildinfo:
        raise RebuilderExceptionAttest(f"Cannot find buildinfo for {package}")
    buildinfo = buildinfo[0]
    with open(buildinfo) as fd:
        parsed_buildinfo = debian.deb822.BuildInfo(fd)

    gpg_sign_keyid = Config["project"].get(project, {}).get('in-toto-sign-key-fpr', None)
    if gpg_sign_keyid:
        # generate in-toto metadata
        generate_intoto_metadata(package.artifacts, gpg_sign_keyid, parsed_buildinfo)
        link = glob.glob("rebuild*.link")
        if not link:
            raise RebuilderExceptionAttest(f"Cannot find link for {package}")
        link = link[0]

        # create final output directory
        outputdir = get_intoto_metadata_package(
            package, unreproducible=package.status == "unreproducible")
        os.makedirs(outputdir, exist_ok=True)
        shutil.copy2(os.path.join(package.artifacts, buildinfo), outputdir)
        shutil.copy2(os.path.join(package.artifacts, link), outputdir)

        # create symlink to new buildinfo and rebuild link file
        os.chdir(outputdir)
        if not os.path.exists("buildinfo"):
            os.symlink(buildinfo, "buildinfo")
        if not os.path.exists("metadata"):
            os.symlink(link, "metadata")

        os.chdir(os.path.join(outputdir, "../../"))
        for binpkg in parsed_buildinfo.get_binary():
            if not os.path.exists(binpkg):
                os.symlink(package.name, binpkg)
    else:
        log.info(f"Unable to sign in-toto metadata: "
                 f"no GPG keyid provided for project '{project}.")

    report.delay(package)
    result = {"attest": [dict(package)]}
    return result


@app.task(base=BaseTask)
def report(package):
    try:
        package = BuildPackage.from_dict(package)
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionReport from e

    # collect log
    builder = getRebuilder(package.distribution)
    output_dir = f"/rebuild/{builder.project}"
    log_dir = f"{output_dir}/logs"
    os.makedirs(log_dir, exist_ok=True)
    src_log = f"/artifacts/{builder.distdir}/{package.log}"
    dst_log = f"{log_dir}/{package.log}"
    if not os.path.exists(src_log):
        raise RebuilderExceptionReport(f"Cannot find build log file {src_log}")
    if not os.path.exists(dst_log):
        shutil.move(src_log, dst_log)
    if not os.path.exists(dst_log):
        raise RebuilderExceptionReport(f"Cannot find build log file {dst_log}")

    # remove artifacts
    if os.path.exists(package.artifacts):
        shutil.rmtree(package.artifacts)
    else:
        log.error(f"Cannot find package artifacts for cleaning {package}")

    result = {"report": [dict(package)]}
    upload.delay(package)
    return result


@app.task(base=BaseTask)
def upload(package=None, project=None):
    try:
        package = BuildPackage.from_dict(package) if package else None
    except KeyError as e:
        log.error("Failed to parse package.")
        raise RebuilderExceptionUpload from e

    ssh_key = Config["common"].get("repo-ssh-key", None)
    remote_ssh_host = Config["common"].get("repo-remote-ssh-host", None)
    remote_ssh_basedir = Config["common"].get("repo-remote-ssh-basedir", None)

    if package:
        project = get_project(package.distribution)
        if not project:
            raise RebuilderExceptionUpload(f"Cannot determine underlying project for {package}")

    if project:
        ssh_key = Config["project"][project].get(
            "repo-ssh-key", ssh_key)
        remote_ssh_host = Config["project"][project].get(
            "repo-remote-ssh-host", remote_ssh_host)
        remote_ssh_basedir = Config["project"][project].get(
            "repo-remote-ssh-basedir", remote_ssh_basedir)

    try:
        if ssh_key and remote_ssh_host and remote_ssh_basedir:
            # pay attention to latest "/", we use rsync!
            dir_to_upload = ["/rebuild/"]

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


@app.task(base=BaseTask)
def _generate_results(project):
    # generate plots from results
    try:
        generate_results(app, project)
    except RebuilderException as e:
        log.error(f"Failed to generate plots: {str(e)}")
    upload.delay(project=project)


@app.task(base=BaseTask)
def _metadata_to_db(dist, unreproducible=False):
    result = {"rebuild": []}
    try:
        dist = RebuilderDist(dist)
        result["rebuild"] = metadata_to_db(app, dist, unreproducible=unreproducible)
    except Exception as e:
        log.error(f"Failed to generate DB results: {str(e)}")
    return result
