import json
import os
import shutil
import tempfile

from app.lib.exceptions import RebuilderExceptionAttest
from app.lib.get import getPackage
from app.lib.attest import process_attestation

TEST_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)))

GPG_SIGN_KEY_ID = "632F8C69E01B25C9E0C3ADF2F360C0D259FB650C"
GPG_SIGN_KEY_UNREPR_ID = "417490C2E134631C893D34F857D7E041A878DA99"


def test_attest_reproducible():
    with tempfile.TemporaryDirectory() as basedir:
        package = getPackage({
            'name': '0xffff',
            'epoch': None,
            'version': '0.8-1+b1',
            'arch': 'amd64',
            'distribution': 'bullseye',
            'buildinfos': {
                "old": 'http://buildinfos.fake.net/0/0xffff/0xffff_0.8-1+b1_amd64.buildinfo',
                "new": f"{basedir}/0xffff_0.8-1+b1_amd64.buildinfo"
            }
        })

        shutil.copy2(f"{TEST_DIR}/data/0xffff_0.8-1+b1_amd64.deb", basedir)
        shutil.copy2(f"{TEST_DIR}/data/0xffff_0.8-1+b1_amd64.buildinfo", basedir)

        os.environ["GNUPGHOME"] = f"{basedir}/gnupg"
        shutil.copytree(f"{TEST_DIR}/gnupg", f"{basedir}/gnupg")

        package.artifacts = basedir
        process_attestation(
            package=package,
            output=basedir,
            gpg_sign_keyid=GPG_SIGN_KEY_ID,
            files=["0xffff_0.8-1+b1_amd64.deb"],
            reproducible=True,
            rebuild_dir=f"{basedir}/rebuild"
        )

        output_dir = f"{basedir}/rebuild/debian/sources/0xffff/0.8-1+b1"
        assert os.path.exists(f"{output_dir}/rebuild.632f8c69.amd64.link")
        assert os.path.exists(f"{output_dir}/rebuild.632f8c69.link")
        assert os.path.islink(f"{output_dir}/metadata")


def test_attest_unreproducible():
    with tempfile.TemporaryDirectory() as basedir:
        os.environ["GNUPGHOME"] = f"{basedir}/gnupg"
        shutil.copytree(f"{TEST_DIR}/gnupg", f"{basedir}/gnupg")

        # amd64
        package = getPackage({
            'name': 'bash',
            'epoch': None,
            'version': '5.1-2+b3',
            'arch': 'amd64',
            'distribution': 'bullseye',
            'buildinfos': {
                "old": 'http://buildinfos.fake.net/b/bash/bash_5.1-2+b3_amd64.buildinfo',
                "new": f"{basedir}/bash_5.1-2+b3_amd64.buildinfo"
            },
            "artifacts": basedir
        })
        shutil.copy2(f"{TEST_DIR}/data/bash_5.1-2+b3_amd64.deb", basedir)
        shutil.copy2(f"{TEST_DIR}/data/bash-static_5.1-2+b3_amd64.deb", basedir)
        shutil.copy2(f"{TEST_DIR}/data/bash_5.1-2+b3_amd64.buildinfo", basedir)

        package.artifacts = basedir
        process_attestation(
            package=package,
            gpg_sign_keyid=GPG_SIGN_KEY_ID,
            files=["bash_5.1-2+b3_amd64.deb"],
            reproducible=True,
            rebuild_dir=f"{basedir}/rebuild"
        )
        process_attestation(
            package=package,
            gpg_sign_keyid=GPG_SIGN_KEY_UNREPR_ID,
            files=["bash-static_5.1-2+b3_amd64.deb"],
            reproducible=False,
            rebuild_dir=f"{basedir}/rebuild"
        )

        output_repr_dir = f"{basedir}/rebuild/debian/sources/bash/5.1-2+b3"
        assert os.path.exists(f"{output_repr_dir}/rebuild.632f8c69.amd64.link")
        assert os.path.exists(f"{output_repr_dir}/rebuild.632f8c69.link")
        assert os.path.islink(f"{output_repr_dir}/metadata")

        with open(f"{output_repr_dir}/metadata") as fd:
            content = json.loads(fd.read())
            assert content["signatures"][0]["keyid"] == GPG_SIGN_KEY_ID.lower()
            assert set(content["signed"]["products"].keys()) == {"bash_5.1-2+b3_amd64.deb"}

        output_unrepr_dir = f"{basedir}/rebuild/debian/unreproducible/sources/bash/5.1-2+b3"
        assert os.path.exists(f"{output_unrepr_dir}/rebuild.417490c2.amd64.link")
        assert os.path.exists(f"{output_unrepr_dir}/rebuild.417490c2.link")
        assert os.path.islink(f"{output_unrepr_dir}/metadata")

        with open(f"{output_unrepr_dir}/metadata") as fd:
            content = json.loads(fd.read())
            assert content["signatures"][0]["keyid"] == GPG_SIGN_KEY_UNREPR_ID.lower()
            assert set(content["signed"]["products"].keys()) == {"bash-static_5.1-2+b3_amd64.deb"}

        assert package.files["reproducible"] == ["bash"]
        assert package.files["unreproducible"] == ["bash-static"]

        # all
        package = getPackage({
            'name': 'bash',
            'epoch': None,
            'version': '5.1-2+b3',
            'arch': 'all',
            'distribution': 'bullseye',
            'buildinfos': {
                "old": 'http://buildinfos.fake.net/b/bash/bash_5.1-2+b3_all.buildinfo',
                "new": f"{basedir}/bash_5.1-2+b3_all.buildinfo"
            }
        })

        shutil.copy2(f"{TEST_DIR}/data/bash-doc_5.1-2+b3_all.deb", basedir)
        shutil.copy2(f"{TEST_DIR}/data/bash_5.1-2+b3_all.buildinfo", basedir)

        package.artifacts = basedir
        process_attestation(
            package=package,
            output=basedir,
            gpg_sign_keyid=GPG_SIGN_KEY_ID,
            files=["bash-doc_5.1-2+b3_all.deb"],
            reproducible=True,
            rebuild_dir=f"{basedir}/rebuild"
        )

        output_repr_dir = f"{basedir}/rebuild/debian/sources/bash/5.1-2+b3"
        assert os.path.exists(f"{output_repr_dir}/rebuild.632f8c69.all.link")
        assert os.path.exists(f"{output_repr_dir}/rebuild.632f8c69.link")
        assert os.path.islink(f"{output_repr_dir}/metadata")

        with open(f"{output_repr_dir}/metadata") as fd:
            content = json.loads(fd.read())
            assert content["signatures"][0]["keyid"] == GPG_SIGN_KEY_ID.lower()
            assert set(content["signed"]["products"].keys()) == {"bash_5.1-2+b3_amd64.deb",
                                                                 "bash-doc_5.1-2+b3_all.deb"}
