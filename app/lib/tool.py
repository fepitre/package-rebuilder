import base64
import glob
import json
import os

from app.lib.attest import BaseAttester
from app.lib.common import DEBIAN, DEBIAN_ARCHES, parse_deb_buildinfo_fname
from app.lib.get import getPackage
from app.lib.rebuild import getRebuilder


def metadata_to_db(app, dist):
    result = []
    # get previous triggered packages builds
    stored_packages = get_rebuild_packages(app)

    distribution = dist.distribution
    arch = dist.arch
    if DEBIAN.get(dist.distribution):
        arch = DEBIAN_ARCHES.get(arch, arch)

    attester = BaseAttester()

    repr_basedir = attester.metadata_dir(distribution, reproducible=True)
    unrepr_basedir = attester.metadata_dir(distribution, reproducible=False)
    if not os.path.exists(repr_basedir) and not unrepr_basedir:
        return result
    buildinfo_files = glob.glob(f"/var/lib/rebuilder/rebuild/{dist.project}/buildinfos/*.buildinfo")
    for buildinfo in buildinfo_files:
        parsed_bn = parse_deb_buildinfo_fname(buildinfo)
        name = parsed_bn["name"]
        version = parsed_bn["version"]
        epoch = parsed_bn['epoch']
        if not parsed_bn:
            continue
        if len(parsed_bn['arch']) > 1:
            continue
        if parsed_bn['arch'][0] != arch:
            continue
        # due partial metadata generation we need to check in unreproducible metadata
        # exists in order to know the global status of a given package
        metadata = glob.glob(f"{repr_basedir}/{name}/{version}/rebuild.*.{arch}.link")
        metadata = metadata[0] if metadata else ""
        metadata_unrepr = glob.glob(f"{unrepr_basedir}/{name}/{version}/rebuild.*.{arch}.link")
        metadata_unrepr = metadata_unrepr[0] if metadata_unrepr else ""

        global_metadata = {}
        if metadata:
            global_metadata["reproducible"] = metadata
        if metadata_unrepr:
            global_metadata["unreproducible"] = metadata_unrepr

        package = getPackage({
            "name": name,
            "version": version,
            "arch": arch,
            "epoch": epoch,
            "status": "reproducible" if not metadata_unrepr else "unreproducible",
            "distribution": distribution,
            "buildinfos": {
                "new": buildinfo
            },
            "metadata": global_metadata
        })
        package.log = get_latest_log_file(package)
        if not stored_packages.get(str(package), None):
            result.append(dict(package))
    return result


def get_rebuild_packages(app, status=None, with_id=False):
    rebuilt_packages = {}
    failed_packages = {}

    parsed_packages = []
    tasks = get_backend_tasks(app)
    for task in tasks:
        task_status, parsed_task = rebuild_task_parser(task)
        if parsed_task:
            for p in parsed_task:
                package = getPackage(p)
                if with_id:
                    package["_id"] = task["_id"]
                # When a job fail it has retry/failure status from celery point of view
                # but 'report' queue generate a success with "failure" status. We keep them
                # for log and celery status reference.
                if task_status == "success" and \
                        package.status not in ("reproducible", "unreproducible"):
                    failed_packages[str(package)] = package
                    continue
                if status and package.status not in status:
                    continue
                parsed_packages.append(package)
    # create dict to help into getting package info faster
    for p in sorted(parsed_packages, key=lambda x: str(x)):
        if failed_packages.get(str(p), None) and p.status not in ("reproducible", "unreproducible"):
            p.log = failed_packages[str(p)].log
            p.retries = failed_packages[str(p)].retries
        rebuilt_packages[str(p)] = p
    return rebuilt_packages


# TODO: convert and refactor below functions into client to be used notably for creating a BASH cli


def get_latest_log_file(package):
    builder = getRebuilder(package.distribution)
    output_dir = f"/var/lib/rebuilder/rebuild/{builder.project}"
    pkg_log_files = glob.glob(f"{output_dir}/logs/{package}-*.log")
    pkg_log_files = sorted([f for f in pkg_log_files], reverse=True)
    return pkg_log_files[0] if pkg_log_files else ""


def get_latest_diffoscope_file(package):
    diffoscope_log = ""
    if not package.log:
        return diffoscope_log
    log = f"{os.path.dirname(package.log)}/{os.path.splitext(package.log)[0]}.diffoscope.log"
    if os.path.exists(log):
        diffoscope_log = log
    return diffoscope_log


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
    task_status = task["status"].lower()
    # Get successful build from 'report' queue as a build is considered finished
    # when all the post process like collecting logs is done
    if task["status"] == 'SUCCESS' and isinstance(task["result"], dict) \
            and task["result"].get("report", None):
        parsed_task = task["result"]["report"]
    elif (task["status"] == 'FAILURE' or task["status"] == 'RETRY') \
            and task["result"]["exc_type"] == "RebuilderExceptionBuild":
        # We have stored package info in exception
        parsed_task = task["result"]["exc_message"][0]
        parsed_task[0]["status"] = task_status
    return task_status, parsed_task


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
        r = doc
        if not isinstance(doc["result"], str):
            continue
        r["result"] = json.loads(doc["result"])
        results.append(r)
    return results


def delete_backend_tasks_by_celery_status(app, status):
    backend = app.backend
    col = backend.collection.find()
    for _, doc in enumerate(col):
        if doc["status"] == status:
            backend._forget(doc["_id"])


def delete_backend_tasks_by_backend_id(app, ids):
    backend = app.backend
    for id in ids:
        try:
            backend._forget(id)
        except Exception:
            continue
