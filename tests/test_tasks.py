import glob
import os
import tempfile
import requests_mock
import shutil
import pytest

from unittest.mock import patch, MagicMock

from app.celery import app
from app.tasks.rebuilder import get, rebuild, attest, report
from app.lib.rebuild import BaseRebuilder

TEST_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)))
os.environ["PACKAGE_REBUILDER_CONF"] = f"{TEST_DIR}/rebuilder.conf"
os.environ["GNUPGHOME"] = f"{TEST_DIR}/gnupg"

global tmpdir, rootdir, artifacts_dir, rebuild_dir, package


def setup_module(module):
    with app.pool.acquire(block=True) as conn:
        del conn.default_channel.client["rebuild"]
    global tmpdir, rootdir, artifacts_dir, rebuild_dir, package
    tmpdir = tempfile.TemporaryDirectory()
    rootdir = tmpdir.name
    artifacts_dir = f"{rootdir}/artifacts"
    rebuild_dir = f"{rootdir}/rebuild"
    package = {
        "name": "bash", "epoch": None, "version": "5.1-2+b3", "arch": "amd64",
        "distribution": "unstable", "metadata": None, "artifacts": None, "status": None,
        "log": None, "diffoscope": None, "retries": 0, "files": None,
        "buildinfos": {"old": "https://buildinfos.debian.net/"
                              "buildinfo-pool/b/bash/bash_5.1-2+b3_amd64.buildinfo"}
    }


def teardown_module(module):
    tmpdir.cleanup()


@pytest.fixture
def global_var():
    pytest.tmpdir = tempfile.TemporaryDirectory()


def test_tasks_get(requests_mock):
    #
    # get
    #
    with open(f"{TEST_DIR}/data/buildinfo-pool_unstable_amd64.list", "r") as fd:
        requests_mock.get("https://buildinfos.debian.net/"
                          "buildinfo-pool_unstable_amd64.list", text=fd.read())
    with open(f"{TEST_DIR}/data/single.pkgset", "r") as fd:
        requests_mock.get("https://jenkins.debian.net/userContent/reproducible/debian/pkg-sets/"
                          "unstable/single.pkgset", text=fd.read())
    result = get("unstable+single.amd64")
    global package
    assert result == {"get": [package]}


@patch("app.lib.rebuild.subprocess.run")
def test_tasks_rebuild(mock_run, requests_mock):
    #
    # rebuild
    #
    with open(f"{TEST_DIR}/data/fake_bash_5.1-2+b3_amd64.buildinfo", "r") as fd:
        requests_mock.get("https://buildinfos.debian.net/"
                          "buildinfo-pool/b/bash/bash_5.1-2+b3_amd64.buildinfo", text=fd.read())

    # fake tempdir generated for build
    def gen_temp_dir(*args, **lwargs):
        return f"{rootdir}/build"
    os.makedirs(f"{rootdir}/build")
    BaseRebuilder.gen_temp_dir = gen_temp_dir

    mock_stdout = MagicMock()
    mock_stdout.configure_mock(
        **{
            "stdout": b"Build is unreproducible!",
            "returncode": 2
        }
    )
    mock_run.return_value = mock_stdout
    shutil.copy2(f"{TEST_DIR}/data/bash_5.1-2+b3_amd64.deb", f"{rootdir}/build")
    shutil.copy2(f"{TEST_DIR}/data/bash-static_5.1-2+b3_amd64.deb", f"{rootdir}/build")
    shutil.copy2(f"{TEST_DIR}/data/bash-amd64-summary.out", f"{rootdir}/build/summary.out")
    shutil.copy2(f"{TEST_DIR}/data/fake_bash_5.1-2+b3_amd64.buildinfo", f"{rootdir}/build/bash_5.1-2+b3_amd64.buildinfo")

    global package
    result = rebuild(package, artifacts_dir=artifacts_dir)
    assert isinstance(result, dict) and len(result.get("rebuild", [])) > 0
    package = result["rebuild"][0]

    expected_fields = {
        "name": "bash", "version": "5.1-2+b3", "arch": "amd64",
        "distribution": "unstable", "status": "unreproducible", "buildinfos": {
            "old": "https://buildinfos.debian.net/"
                   "buildinfo-pool/b/bash/bash_5.1-2+b3_amd64.buildinfo",
            "new": f"{artifacts_dir}/debian/build/bash_5.1-2+b3_amd64.buildinfo"
        }, "log": glob.glob(f"{artifacts_dir}/debian/bash-5.1-2+b3.amd64-*.log")[0]
    }
    for k in ["name", "version", "arch", "distribution", "status", "buildinfos", "log"]:
        assert package[k] == expected_fields[k]


def test_tasks_attest():
    #
    # attest
    #
    global package
    result = attest(package, rebuild_dir=rebuild_dir)
    assert isinstance(result, dict) and len(result.get("attest", [])) > 0
    package = result["attest"][0]

    output_repr_dir = f"{rebuild_dir}/debian/sources/bash/5.1-2+b3"
    assert package["metadata"]["reproducible"] == f"{output_repr_dir}/rebuild.632f8c69.amd64.link"

    assert os.path.exists(f"{output_repr_dir}/rebuild.632f8c69.amd64.link")
    assert os.path.exists(f"{output_repr_dir}/rebuild.632f8c69.link")
    assert os.path.islink(f"{output_repr_dir}/metadata")

    output_unrepr_dir = f"{rebuild_dir}/debian/unreproducible/sources/bash/5.1-2+b3"
    assert package["metadata"]["unreproducible"] == f"{output_unrepr_dir}/rebuild.417490c2.amd64.link"

    assert os.path.exists(f"{output_unrepr_dir}/rebuild.417490c2.amd64.link")
    assert os.path.exists(f"{output_unrepr_dir}/rebuild.417490c2.link")
    assert os.path.islink(f"{output_unrepr_dir}/metadata")


def test_tasks_report():
    #
    # report
    #
    global package
    result = report(package, rebuild_dir=rebuild_dir)
    assert isinstance(result, dict) and len(result.get("report", [])) > 0
    package = result["report"][0]

    # we check that path is the real expected
    assert package["log"] == f"{rebuild_dir}/debian/logs/{os.path.basename(package['log'])}"
    assert os.path.exists(package["log"])

    assert package["buildinfos"]["new"] == f"{rebuild_dir}/debian/buildinfos/bash_5.1-2+b3_amd64.buildinfo"
    assert os.path.exists(package["buildinfos"]["new"])
