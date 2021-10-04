import os
import shutil
import tempfile

from unittest.mock import MagicMock, patch

import pytest

from app.lib.exceptions import RebuilderExceptionBuild
from app.lib.get import getPackage
from app.lib.rebuild import BaseRebuilder, DebianRebuilder, QubesRebuilderDEB, getRebuilder

TEST_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)))


def test_rebuild_debian():
    rebuilder = getRebuilder("bullseye")
    assert isinstance(rebuilder, DebianRebuilder)


def test_rebuild_qubes_deb():
    rebuilder = getRebuilder("qubes-4.1-vm-bullseye")
    assert isinstance(rebuilder, QubesRebuilderDEB)


def _create_rebuild(mock_run, basedir, package, return_code, stdout):
    mock_stdout = MagicMock()
    mock_stdout.configure_mock(
        **{
            "stdout": stdout,
            "returncode": return_code
        }
    )
    mock_run.return_value = mock_stdout

    # fake tempdir generated for build
    def gen_temp_dir(*args, **lwargs):
        return f"{basedir}/build"
    os.makedirs(f"{basedir}/build")
    shutil.copy2(f"{TEST_DIR}/data/{os.path.basename(package.buildinfos['old'])}", f"{basedir}/build")

    BaseRebuilder.gen_temp_dir = gen_temp_dir
    rebuilder = getRebuilder(package.distribution, artifacts_dir=f"{basedir}/artifacts")
    rebuilder.run(package)
    return package


@patch("app.lib.rebuild.subprocess.run")
def test_rebuild_debian_reproducible(mock_run):
    package = getPackage({
        'name': '0xffff',
        'epoch': None,
        'version': '0.8-1+b1',
        'arch': 'amd64',
        'distribution': 'bullseye',
        'buildinfos': {
            "old": 'https://buildinfos.debian.net/buildinfo-pool'
                   '/0/0xffff/0xffff_0.8-1+b1_amd64.buildinfo'
        }
    })
    stdout = b"Build is reproducible!"
    with tempfile.TemporaryDirectory() as basedir:
        package = _create_rebuild(mock_run, basedir, package, 0, stdout)

        assert package.status == "reproducible"
        assert package.log is not None
        with open(package.log, "rb") as fd:
            assert fd.read() == stdout
        assert package.buildinfos.get("new", None) is not None


@patch("app.lib.rebuild.subprocess.run")
def test_rebuild_debian_unreproducible(mock_run):
    package = getPackage({
        'name': 'bash',
        'epoch': None,
        'version': '5.1-2+b3',
        'arch': 'amd64',
        'distribution': 'bullseye',
        'buildinfos': {
            "old": 'https://buildinfos.debian.net/buildinfo-pool'
                   '/b/bash/bash_5.1-2+b3_amd64.buildinfo'
        }
    })
    stdout = b"Build is unreproducible!"
    with tempfile.TemporaryDirectory() as basedir:
        package = _create_rebuild(mock_run, basedir, package, 2, stdout)

        assert package.status == "unreproducible"
        assert package.log is not None
        with open(package.log, "rb") as fd:
            assert fd.read() == stdout
        assert package.buildinfos.get("new", None) is not None


@patch("app.lib.rebuild.subprocess.run")
def test_rebuild_debian_failure(mock_run):
    package = getPackage({
        'name': 'bash',
        'epoch': None,
        'version': '5.1-2+b3',
        'arch': 'amd64',
        'distribution': 'bullseye',
        'buildinfos': {
            "old": 'https://buildinfos.debian.net/buildinfo-pool'
                   '/b/bash/bash_5.1-2+b3_amd64.buildinfo'
        }
    })
    stdout = b"Build failed!"
    with tempfile.TemporaryDirectory() as basedir:
        with pytest.raises(RebuilderExceptionBuild) as e:
            _create_rebuild(mock_run, basedir, package, 1, stdout)
            package = e.value.args[0][0]

            assert package.status == "failure"
            assert package.log is not None
            with open(package.log, "rb") as fd:
                assert fd.read() == stdout
            assert package.buildinfos.get("new", None) is None
