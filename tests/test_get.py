import os
import pytest
import pytest_mock
import requests_mock
import requests
from unittest.mock import MagicMock, patch

from app.lib.exceptions import RebuilderExceptionDist
from app.lib.get import RebuilderDist, DebianRepository, QubesRepository, \
    DebianPackage, QubesPackage, getPackage

TEST_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)))


def test_dist_debian():
    dist = RebuilderDist("bullseye.amd64")

    assert dist.project == "debian"
    assert dist.distribution == "bullseye"
    assert dist.arch == "amd64"
    assert dist.package_sets == ["full"]
    assert isinstance(dist.repo, DebianRepository)


def test_dist_qubes():
    dist = RebuilderDist("qubes-4.1-vm-bullseye.amd64")

    assert dist.project == "qubesos"
    assert dist.distribution == "qubes-4.1-vm-bullseye"
    assert dist.arch == "amd64"
    assert dist.package_sets == ["full"]
    assert isinstance(dist.repo, QubesRepository)


def test_dist_debian_with_package_sets():
    dist = RebuilderDist("unstable+required+essential.all")

    assert dist.project == "debian"
    assert dist.distribution == "unstable"
    assert dist.arch == "all"
    assert dist.package_sets == ["required", "essential"]
    assert isinstance(dist.repo, DebianRepository)


def test_dist_unknown():
    with pytest.raises(RebuilderExceptionDist):
        RebuilderDist("toto.123456")


def test_package_debian():
    p = {
        'name': 'bash',
        'epoch': None,
        'version': '5.1-3+b1',
        'arch': 'amd64',
        'distribution': 'bullseye',
        'buildinfos': {
            "old": 'https://buildinfos.debian.net/buildinfo-pool'
                   '/b/bash/bash_5.1-3+b1_amd64.buildinfo'
        }
    }
    package = getPackage(p)
    assert isinstance(package, DebianPackage)


def test_package_qubesos():
    p = {
        'name': 'qubes-gpg-split',
        'epoch': None,
        'version': '2.0.53-1+deb11u1',
        'arch': 'amd64',
        'distribution': 'qubes-4.1-vm-bullseye',
        'buildinfos': {
            "old": 'https://deb.qubes-os.org/all-versions/r4.1/vm'
                   '/pool/main/q/qubes-gpg-split/qubes-gpg-split_2.0.53-1%2Bdeb11u1_amd64.buildinfo'
        }
    }
    package = getPackage(p)
    assert isinstance(package, QubesPackage)


def test_repo_debian(requests_mock):
    with open(f"{TEST_DIR}/data/buildinfo-pool_unstable_amd64.list", "r") as fd:
        requests_mock.get("https://buildinfos.debian.net/buildinfo-pool_unstable_amd64.list",
                          text=fd.read())
    with open(f"{TEST_DIR}/data/test.pkgset", "r") as fd:
        requests_mock.get("https://jenkins.debian.net/userContent/reproducible/debian/pkg-sets/"
                          "unstable/test.pkgset", text=fd.read())
    dist = RebuilderDist("unstable+test.amd64")
    assert set(dist.repo.get_package_names_in_debian_set("test")) == \
           {'apt', 'bash', 'coreutils', 'shadow', 'util-linux'}

    expected_packages_in_test = [
        DebianPackage(name="apt", version="2.3.9", arch="amd64", epoch=None,
                      distribution="unstable", buildinfos={"old": ""}),
        DebianPackage(name="bash", version="5.1-2+b3", arch="amd64", epoch=None,
                      distribution="unstable", buildinfos={"old": ""}),
        DebianPackage(name="coreutils", version="8.32-4+b1", arch="amd64", epoch=None,
                      distribution="unstable", buildinfos={"old": ""}),
        DebianPackage(name="shadow", version="4.8.1-1", arch="amd64", epoch=None,
                      distribution="unstable", buildinfos={"old": ""}),
        DebianPackage(name="util-linux", version="2.37.2-3", arch="amd64", epoch=None,
                      distribution="unstable", buildinfos={"old": ""})
    ]
    packages_in_test = sorted(dist.repo.get_packages_to_rebuild("test"), key=lambda x: x.name)
    assert packages_in_test == expected_packages_in_test


@patch("app.lib.get.subprocess.run")
def test_repo_qubesos_rsync(mock_run):
    mock_stdout = MagicMock()
    with open(f"{TEST_DIR}/data/rsync_result.txt", "r") as fd:
        mock_stdout.configure_mock(
            **{
                "stdout.decode.return_value": fd.read()
            }
        )
    mock_run.return_value = mock_stdout

    dist = RebuilderDist("qubes-4.1-vm-bullseye.amd64")
    buildinfos = dist.repo.get_rsync_files("rsync://ftp.qubes-os.org/qubes-mirror/repo/deb")
    expected_buildinfos = [
        'deb/r4.1/vm/pool/main/d/dnf/dnf_4.5.2-1+deb11u1_amd64.buildinfo',
        'deb/r4.1/vm/pool/main/q/qubes-artwork/qubes-artwork_4.1.10-1+deb11u1_amd64.buildinfo',
        'deb/r4.1/vm/pool/main/q/qubes-core-agent/qubes-core-agent_4.1.7-1+deb11u1_amd64.buildinfo',
        'deb/r4.1/vm/pool/main/q/qubes-core-qrexec/qubes-core-qrexec_4.1.9-1+deb11u1_amd64.buildinfo',
        'deb/r4.1/vm/pool/main/q/qubes-desktop-linux-common/qubes-desktop-linux-common_4.0.18-1+deb11u1_amd64.buildinfo'
    ]
    assert set(buildinfos) == set(expected_buildinfos)


def test_repo_qubesos():
    buildinfos = [
        'pool/main/d/dnf/dnf_4.5.2-1+deb11u1_amd64.buildinfo',
        'pool/main/q/qubes-artwork/qubes-artwork_4.1.10-1+deb11u1_amd64.buildinfo',
        'pool/main/q/qubes-core-agent/qubes-core-agent_4.1.7-1+deb11u1_amd64.buildinfo',
        'pool/main/q/qubes-core-qrexec/qubes-core-qrexec_4.1.9-1+deb11u1_amd64.buildinfo',
        'pool/main/q/qubes-desktop-linux-common/qubes-desktop-linux-common_4.0.18-1+deb11u1_amd64.buildinfo'
    ]

    def get_rsync_files(*args, **kwargs):
        return buildinfos

    QubesRepository.get_rsync_files = get_rsync_files

    dist = RebuilderDist("qubes-4.1-vm-bullseye.amd64")
    expected_packages_in_test = [
        QubesPackage(name="qubes-artwork", version="4.1.10-1+deb11u1", arch="amd64", epoch=None,
                     distribution="qubes-4.1-vm-bullseye", buildinfos={"old": ""}),
        QubesPackage(name="qubes-core-agent", version="4.1.7-1+deb11u1", arch="amd64", epoch=None,
                     distribution="qubes-4.1-vm-bullseye", buildinfos={"old": ""}),
        QubesPackage(name="qubes-core-qrexec", version="4.1.9-1+deb11u1", arch="amd64", epoch=None,
                     distribution="qubes-4.1-vm-bullseye", buildinfos={"old": ""}),
        QubesPackage(name="qubes-desktop-linux-common", version="4.0.18-1+deb11u1", arch="amd64",
                     epoch=None,
                     distribution="qubes-4.1-vm-bullseye", buildinfos={"old": ""})
    ]
    packages_in_test = sorted(dist.repo.get_packages_to_rebuild(), key=lambda x: x.name)
    assert packages_in_test == expected_packages_in_test

    dist = RebuilderDist("qubes-4.1-vm-bullseye.all")
    expected_packages_in_test = [
        QubesPackage(name="dnf", version="4.5.2-1+deb11u1", arch="all", epoch=None,
                     distribution="qubes-4.1-vm-bullseye", buildinfos={"old": ""}),
    ]
    packages_in_test = sorted(dist.repo.get_packages_to_rebuild(), key=lambda x: x.name)
    assert packages_in_test == expected_packages_in_test
