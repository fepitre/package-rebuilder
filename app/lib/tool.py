import base64
import glob
import json
import os
import debian.deb822

from app.config import Config
from app.lib.attest import BaseAttester
from app.lib.common import DEBIAN, DEBIAN_ARCHES, parse_deb_buildinfo_fname
from app.lib.get import getPackage
from app.lib.rebuild import getRebuilder
from app.lib.report import RebuilderDB


def metadata_to_database(app, dist, **kwargs):
    result = []
    cli = RebuilderDB(conn=app.backend._get_connection(), project=dist.project)

    # get previous triggered packages builds
    stored_packages = cli.dump_buildrecords_as_dict()

    distribution = dist.distribution
    arch = dist.arch
    if DEBIAN.get(dist.distribution):
        arch = DEBIAN_ARCHES.get(arch, arch)
        distribution = "unstable"

    gpg_sign_keyid = Config["project"].get(dist.project, {}).get('in-toto-sign-key-fpr', None)
    gpg_sign_keyid_unrepr = Config["project"].get(dist.project, {}).get('in-toto-sign-key-unreproducible-fpr', None)
    if not gpg_sign_keyid and not gpg_sign_keyid_unrepr:
        return result
    repr_attester = BaseAttester(keyid=gpg_sign_keyid)
    unrepr_attester = BaseAttester(keyid=gpg_sign_keyid_unrepr)

    repr_basedir = repr_attester.metadata_dir(distribution, reproducible=True)
    unrepr_basedir = unrepr_attester.metadata_dir(distribution, reproducible=False)
    if not os.path.exists(repr_basedir) and not unrepr_basedir:
        return result

    rebuild_dir = kwargs.get("rebuild_dir", "/var/lib/rebuilder/rebuild")
    buildinfo_files = glob.glob(f"{rebuild_dir}/{dist.project}/buildinfos/*.buildinfo")
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
        repr_metadata = glob.glob(f"{repr_basedir}/{name}/{version}/rebuild.*.{arch}.link")
        repr_metadata = repr_metadata[0] if repr_metadata else None
        unrepr_metadata = glob.glob(f"{unrepr_basedir}/{name}/{version}/rebuild.*.{arch}.link")
        unrepr_metadata = unrepr_metadata[0] if unrepr_metadata else None

        metadata = {}
        files = {}
        with open(buildinfo) as fd:
            parsed_buildinfo = debian.deb822.BuildInfo(fd)

        if repr_metadata:
            if repr_attester.verify_metadata(repr_metadata):
                metadata["reproducible"] = repr_metadata
                with open(repr_metadata) as fd:
                    parsed_link = json.loads(fd.read())
                files_names = [f.split('_')[0] for f in parsed_link["signed"]["products"]]
                files["reproducible"] = []
                for binpkg in parsed_buildinfo.get_binary():
                    if binpkg in files_names:
                        files["reproducible"].append(binpkg)
            else:
                print(f"Cannot find valid reproducible metadata: {repr_metadata}")

        if unrepr_metadata:
            if unrepr_attester.verify_metadata(unrepr_metadata):
                metadata["unreproducible"] = unrepr_metadata
                with open(unrepr_metadata) as fd:
                    parsed_link = json.loads(fd.read())
                files_names = [f.split('_')[0] for f in parsed_link["signed"]["products"]]
                files["unreproducible"] = []
                for binpkg in parsed_buildinfo.get_binary():
                    if binpkg in files_names:
                        files["unreproducible"].append(binpkg)
            else:
                print(f"Cannot find valid unreproducible metadata: {unrepr_metadata}")

        package = getPackage({
            "name": name,
            "version": version,
            "arch": arch,
            "epoch": epoch,
            "status": "reproducible" if not unrepr_metadata else "unreproducible",
            "distribution": distribution,
            "buildinfos": {
                "new": buildinfo
            },
            "metadata": metadata,
            "files": files
        })
        package.log = get_latest_log_file(package)
        if package.status == "unreproducible":
            package.diffoscope = get_latest_diffoscope_file(package)
        if not stored_packages.get(str(package), None):
            result.append(dict(package))

    for p in result:
        cli.insert_buildrecord(p)
    return result


def get_rebuild_packages(app, dist):
    # get retry packages for celery tasks for the moment.
    retry_packages = {}
    celery_retry_packages = {}
    active_retry_packages = {}
    # tasks = get_backend_tasks(app)
    with open("tasks.json") as fd:
        tasks = json.loads(fd.read())
    for task in tasks:
        if task["status"] == 'RETRY' and task["result"]["exc_type"] == "RebuilderExceptionBuild":
            # We have stored package info in exception
            parsed_task = task["result"]["exc_message"][0]
            for p in parsed_task:
                p["status"] = "retry"
                package = getPackage(p)
                if celery_retry_packages.get(str(package), None):
                    if os.path.basename(package.log) <= os.path.basename(celery_retry_packages[str(package)].log):
                        continue
                celery_retry_packages[str(package)] = package
        if task["status"] == 'SUCCESS' and isinstance(task["result"], dict) \
                    and task["result"].get("report", None):
            for p in task["result"]["report"]:
                if p["status"] != "retry":
                    continue
                package = getPackage(p)
                if retry_packages.get(str(package), None):
                    if os.path.basename(package.log) <= os.path.basename(retry_packages[str(package)].log):
                        continue
                retry_packages[str(package)] = package

    # fixme: find a better way to store retry task info. Right now we rely on "rebuild" task being
    #  retried and marked as is by celery correlated to "report" task having logged info (notably
    #  with timestamp pattern)
    for p in celery_retry_packages:
        if p in retry_packages:
            active_retry_packages[str(p)] = retry_packages[str(p)]

    # get reproducible, unreproducible, failure tasks from rebuild database
    cli = RebuilderDB(conn=app.backend._get_connection(), project=dist.project)
    stored_packages = cli.dump_buildrecords()

    packages = {}
    for p in sorted(list(active_retry_packages.values()) + stored_packages, key=lambda x: str(x)):
        packages[str(p)] = p
    return packages


# TODO: convert and refactor below functions into client to be used notably for creating a BASH cli


def get_latest_log_file(package, rebuild_dir="/var/lib/rebuilder/rebuild"):
    builder = getRebuilder(package.distribution)
    output_dir = f"{rebuild_dir}/{builder.project}"
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


# def rebuild_task_parser(task):
#     parsed_task = None
#     task_status = task["status"].lower()
#     # Get successful build from 'report' queue as a build is considered finished
#     # when all the post process like collecting logs is done
#     if task["status"] == 'SUCCESS' and isinstance(task["result"], dict) \
#             and task["result"].get("report", None):
#         parsed_task = task["result"]["report"]
#     elif (task["status"] == 'FAILURE' or task["status"] == 'RETRY') \
#             and task["result"]["exc_type"] == "RebuilderExceptionBuild":
#         # We have stored package info in exception
#         parsed_task = task["result"]["exc_message"][0]
#         parsed_task[0]["status"] = task_status
#     return task_status, parsed_task
