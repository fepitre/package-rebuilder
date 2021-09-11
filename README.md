PackageRebuilder
===

Note: This software is in a very active development and is subject to major changes due to in progress work
on Qubes, Debian and Fedora side and also dependent tools `debrebuild` and `rpmreproduce`. It may not work as
expected until a proper stable release is made. For more information, don't hesitate to contact me.

## Architecture

The current design of `PackageRebuilder` is based on individual tasks orchestrated with the help of `celery` engine
(see https://docs.celeryproject.org/en/stable/). `celery` uses a `broker` to receive and distribute task in specific queues for
which services `getter`, `rebuilder`, `uploader` and `attester` are connected to. All the task results are stored in `backend`.

```
                         .-----------.     .-----------.
                         | reporter  |    .| getter    |
                         '-----.-----'   / '-----------'
        .---------.            |        /
        | broker  |----.       |       '      .-----------.
        '---------'     '------'-------. .----| uploader  |
                        |              |'     '-----------'
                        | orchestrator |
                        |              |.     .-----------.
        .---------.     .--------------. '----| attester  |
        | backend |----'               '      '-----------'
        '---------'                     \
                                         \ .-----------.
                                          '| rebuilder |
                                           '-----------'
```

The chosen `broker` here for `celery` is `redis` and the `backend` is `mongodb` as database engine.

Each service is doing only tasks defined in separate queues defined as follows:

| SERVICE | TASK/QUEUE |
|-----------|-----------|
| getter | get |
| rebuilder | rebuild |
| attester | attest |
| reporter | report |
| uploader | upload |

> TODO: improve documentation and add schema about typical build chain.

The `getter` service is responsible to get the latest `buildinfo` on `Qubes OS` or `Debian` (soon `Fedora`) repositories
and to add new `rebuild` tasks. Once a `rebuilder` has finished it adds a new `attest` task for `attester`. Then, `attester`
will collect rebuild artifacts and generate `in-toto` metadata. Finally, `upload` task is triggered once metadata are generated.
Either on success or failure, a task result containing useful information about the build is created in `backend`.
Notably, it contains all the information about a build, its status and the number of retries. In practice, you will
only scale `rebuilder` service.

There exist two side services for `celery` which are `beat` and `flower`. The former holds the periodic task scheduling
and the latter is useful for monitoring `celery` (see [flower](https://flower.readthedocs.io/en/latest/)). Notably, one
can setup [graphana](https://flower.readthedocs.io/en/latest/prometheus-integration.html#example-grafana-dashboard)
integration. You can access `flower` interface at `http://localhost:5556`.

`uploader` service is also responsible to export all the rebuild task results and to generate some graphical stats
(e.g. [results](http://debian.notset.fr/rebuild/results/)).

## PackageRebuilder: the machinery

This sections helps to configure a `PackageRebuilder` on any environment (not Qubes specific) assuming it satisfies
the following dependencies requirements. The current setup is done using `Docker` for which it can be replaced using
a more classic or Qubes configuration with virtual machines for each service. This will be detailed in a near future.
The use of `celery` allows extending and interacting with the `broker` easily. For example, one can add webhook trigger
with or in place of periodic scheduling.

> TODO: Add webhook setup to trigger `get` task.

### Installation

We recommend installing `PackageRebuilder` in a Debian or CentOS based distribution which still supports `Docker`.
In a future, we plan to test it with `podman`. Here we give installation steps for a Debian distribution.

On the hosting machine, most of the commands as to be run as `root`.

Ensure to have `docker` and `docker-compose` installed:
```
$ apt install docker docker-compose
```

Enable and start `docker`:
```
$ systemctl enable docker
$ systemctl start docker
```

Clone `package-rebuilder` repository into `/opt` as `rebuilder`:
```
$ git clone https://github.com/fepitre/package-rebuilder /opt/rebuilder
```

Copy rebuilder `systemd` service and reload:
```
$ cp /opt/rebuilder/rebuilder.service /usr/lib/systemd/system
$ systemctl daemon-reload
```

Create the following folders:
```
$ mkdir -p /var/lib/rebuilder/{rebuild,broker,backend,ssh,gnupg}
```

The previously created folders are mounted differently into containers to store or share persistent data
(see `/opt/rebuilder/docker-compose.yml`). In `gnupg` folder you need to provide a GPG keyring containing private key used
to sign `in-toto` metadata. In `ssh` folder you need to add a private SSH key allowed to push on a remote host
destination. Then, you need to edit the configuration file `/opt/rebuilder/rebuilder.conf` which will be injected
into `docker` images having all the needed configuration information.

For example, here is the one used by `notset-rebuilder`:
```ini
[DEFAULT]
broker = redis://broker:6379/0
backend = mongodb://backend:27017
snapshot = http://snapshot.notset.fr

# Scheduled task period for fetching latest buildinfo files
schedule = 1800

# GPG key fingerprint (container keyring: /root/.gnupg)
in-toto-sign-key-fpr = 8DEB0BEF1D99FEB8B9A90FB192EF6D6141641E5C

# SSH private key name (container path: /root/.ssh/id_rsa)
repo-ssh-key = id_rsa

# RSYNC SSH destination
repo-remote-ssh-host = rebuilder@mirror.notset.fr
repo-remote-ssh-basedir = /data/rebuilder

dist = qubes-4.1-vm-bullseye.amd64 qubes-4.1-vm-bullseye.all unstable+essential+required+build-essential+build-essential-depends.amd64 unstable+essential+required+build-essential+build-essential-depends.all
```
In the current `docker` setup, you only need to adjust `in-toto-sign-key-fpr`, `repo-ssh-key`, `repo-remote-ssh-host`
and `repo-remote-ssh-basedir` values. Here `schedule` value is the time in second for which `get` task will be
run periodically. Default here is `30 minutes`. Please note that values for `broker` and `backend` variables refers
to `docker` containers `broker` and `backend` defined in `docker-compose.yml`.

You can now enable and start the service:
```
$ systemctl enable rebuilder
$ systemctl start rebuilder
```

Please note that the first start of `rebuilder` service can take few minutes. In background, it creates all
needed docker images.

Once it's started, it will fetch for new buildinfo files in Qubes repositories periodically every `30 minutes`
(`schedule` value in `/opt/rebuilder/rebuilder.conf`). You can manually trigger the `get`. For that, you need to install `python3-celery`, `python3-pymongo`, `python3-packaging`
and `rsync` on the `rebuilder` machine then, in `/opt/rebuilder`, run:
```
$ CELERY_BROKER_URL="redis://localhost:6379/0" ./init_feed.py
```

> TODO: Add initial feed if `rebuild` queue is empty.

## Check rebuild proofs before installing packages: apt-transport-in-toto

In this section, we give configuration steps for `bullseye` based qube in order to use rebuild proofs before installing
packages thank to `apt-transport-in-toto`. For more details on `in-toto`, its `apt` transport setup and configurations
options, we refer to upstream projects https://in-toto.readthedocs.io and https://github.com/in-toto/apt-transport-in-toto.

### Installation

For dependencies, you need to install:
```
$ apt install apt-transport-in-toto in-toto python3-securesystemslib python3-pathspec python3-iso8601 python3-cryptography
```

### Configure `apt-transport-in-toto`

The APT configuration file `/etc/apt/apt.conf.d/intoto`:
```
APT::Intoto {
  LogLevel {"20"};
  Rebuilders {
    "https://debian.notset.fr/rebuild/";
    "https://qubes.notset.fr/rebuild/deb/r4.1/vm/";
  };
  GPGHomedir {"/var/lib/intoto/gnupg"};
  Layout {"/var/lib/intoto/root.layout"};
  Keyids {
    "9fa64b92f95e706bf28e2ca6484010b5cdc576e2";
  };
  NoFail {"true"}
};
```

This file uses `GPGHomedir` to refer to a GPG keyring containing all the public keys used for signing `in-toto` 
metadata, i.e. the rebuilders keys and the public key used to sign the `root.layout` file. 
The `Keyids` contains public keys ids used for signing `root.layout` only. The `root.layout` file contains 
information like expiration time for `root.layout` validity, rebuilders keys to use and also how the `in-toto` engine 
has to verify the metadata with respect to received metadata.

Create the following directory:
```
$ mkdir -p /var/lib/intoto/gnupg
$ chmod 700 /var/lib/intoto/gnupg
```

and add the `root.layout` file in `/var/lib/intoto` where we provide the one associated for `notset-rebuilder`:
```json
{
 "signatures": [
  {
   "keyid": "9fa64b92f95e706bf28e2ca6484010b5cdc576e2",
   "other_headers": "04000108001d1621049fa64b92f95e706bf28e2ca6484010b5cdc576e205026008aba9",
   "signature": "ac067f95f977cf4c660040cc3493ef0e9a8173938f0860179706d6f19a1864fc3ac1840fbfe486b0bd794c5d210b3f22c2f09f27addc5d8e56d1b7cdda9ed6339d1a0f79c4ac505c675f02a553e4e695f7b6c88def64c079bd0ab8684d05505cd2ec5c3e701192f8021f6f1956875720ecfd8c1bb2a073e23a60e22d11b79e0463a9409bb46643dffd09d604ea5994543d279b94c592fe1d251f33908ee50db0d02b33eb1ea9f57d82a6d0b46df5e39f97fa8564b66f2e5d094ba56aaf94ffd5224bc3a764129c336c279a5dac5d87c6d190a7ded21dfb586c79d679c3996fd6f4a0c61bb4488ea040a6e2365f8e16c91b8f1deb3d2e03d5304e91b8251d4719938c50ca18dbf489bb0d9145184e4432b5f83eb10f571eb4c7f0dd68211f9300a6cbfb3a19ba4ff13a3cb49b4a2f03cc0fe024bffed4154fc091964cc514692cd641845af116ef2c3137e581b7f699b76a43ffc3818d434931bdf7f1f5841dda767b3ab073cc5cb6eef8d642c512e1e3fd28b6d5e58065f21f50145d00118c5f345a273921fc6040d3454ba5a3c1900e07ca5ef38316e84c4e87124a85a83e2e7988dc0c07f804333beddab74db4293bf7753b1fac06e7470d86687a0236ab7fc69b73f4c1d429f93b0a6a373b43d3f17bad375f210d285cef329cd61321af551f7ef939ae7bc96019dfb06f4c12cb6f163cc6826b9fa3ac2f7171aaf54fe866"
  }
 ],
 "signed": {
  "_type": "layout",
  "expires": "2023-01-20T00:00:00Z",
  "inspect": [
   {
    "_type": "inspection",
    "expected_materials": [
     [
      "MATCH",
      "*.deb",
      "WITH",
      "PRODUCTS",
      "FROM",
      "rebuild"
     ],
     [
      "DISALLOW",
      "*.deb"
     ]
    ],
    "expected_products": [],
    "name": "verify-reprobuilds",
    "run": [
     "/usr/bin/true"
    ]
   }
  ],
  "keys": {
   "8deb0bef1d99feb8b9a90fb192ef6d6141641e5c": {
    "creation_time": 1611149279,
    "hashes": [
     "pgp+SHA2"
    ],
    "keyid": "8deb0bef1d99feb8b9a90fb192ef6d6141641e5c",
    "keyval": {
     "private": "",
     "public": {
      "e": "010001",
      "n": "e3eed2ff783b0c5e23ed4910d22357bfbada1987ca160773865a7ef9e2fe3696cbde40b15e25f728ef03345902ce16318683987e167b5aacdc0d6eee559093148195192fffe0f37b5d7fa8ea2f1941ea04a761dee508b6c4223bb7af18a9fcae5ad7981fd3cd96f16d5397088d4569aac7a4124fc1361d6905af3ab4f8818508776c749aadb4658e080f6c7e572e3bfca4bf497e31106a28c53345468a50294f79ffb2e2d98ffcb3b5b4a9d9f366a0bc359806fd60664ac2a6a389fdcd6806c2de689669c34ad6a876a20ff80b89e6d536bbcab8eb385beaca07fb8aad8282493d9f597bea74f916602dffa0943ccbc736de530e905e0e1164c2e273253b38d4f1edaf4bc95100c865da0eb64f58ff33f21fc77e5495e8d4698c2dad7085a4f4f3ec06aa129ca3bdcc68340fedfc785b1fbb9ee4d4d9d89ce7ac866ba90f5263cc12760d264008b4ae545c9e7ed0086f0a4104d3c388d22a54dbeeace29174ceb175f4f12f7831583dfabf2f19c9f73f667b08986957d36434e6cc9c13407360c7e00769aeb027708687b663a01a30db799605ead9ce93ca34b397948f5f8c8cfb382e7bcdf4b81526bb136664ff538d859c84250314642b8ec6da11e9abb576ef6f274d4abe9a1d5b4e1cc1064ab59276ab8f161c4903e9f0b44747db3f243b023b93b79ab52873de4bcfd4d884ac3ce112a9c9576b7092a15c84233eef69e7"
     }
    },
    "method": "pgp+rsa-pkcsv1.5",
    "type": "rsa",
    "validity_period": 63072000
   },
   "9fa64b92f95e706bf28e2ca6484010b5cdc576e2": {
    "creation_time": 1545907057,
    "hashes": [
     "pgp+SHA2"
    ],
    "keyid": "9fa64b92f95e706bf28e2ca6484010b5cdc576e2",
    "keyval": {
     "private": "",
     "public": {
      "e": "010001",
      "n": "dc7f268e91eb9ffa0f7a4b589eab4e6d27cbd31ac42b35743944313b7df6a02f4f84a5f20fdf1ef7c794340f2dab51543ea0dd431dcb9e34bef446d36cf41167a16d0a667b246d3e27109f7040241adb695068893d90235575d8761c92a58ed77cbeda2f8bdeec5888edcf1c7943073699dd0363d00784d0b82aa2885b7b523c2d7b52c23c7c04a83401c8b58b729f66ceebba7ca4222f96da01ad89c4943d4fca9e48196883dbf603df6e623e5dbea875645125a66fdda1451535f5183e51db3c6d51ea873e1c3aa204ee3a57818cd337f92af2cc41321d8ead0fdb4b12da71c68d02921549955201ab1525f76c70d0cbf5f8e41c444afc08924e5787aec80b7f83cbc39928a8fb3fe89108548fadd29ca0f8f6bcbb01687984cfacb4a5c347fb6d74a69557eff20ecced15a69374f9408a5e1c0d23de511935b6115f3908f751c2b78d17d9b4dbda34ca82f07d8d08c46aba64e9ebaf1acd8883f538325532de91b912177611b9fc473df5257a5a03f18f689e438f3574abd9a46241832d823727f27eb36bb42abd15b302da20ddd6267ee42840f07dce2cda468061f6e513fa74cd58ab11506e9bbdb73051f80f1853acea1682d6b69f24473a88b4cf411585803e0fcea614f58a6fdae85afb6798596cb208069aa6c910cbed2db627952b77bf7da77d45e1d766903f053f2c2188ceae2814d56b369dda58f9d26a32cfa5"
     }
    },
    "method": "pgp+rsa-pkcsv1.5",
    "subkeys": {
     "77aff41dc7843d47d2a952956e8aaec1ab9505d4": {
      "creation_time": 1545907057,
      "hashes": [
       "pgp+SHA2"
      ],
      "keyid": "77aff41dc7843d47d2a952956e8aaec1ab9505d4",
      "keyval": {
       "private": "",
       "public": {
        "e": "010001",
        "n": "c03c9772172e0a798a6e1804d6c1608d1def17355a81a65574c95953e694a20b203750adf8265197f5824e8ff1653191a087ad6f6e22bea624639bf9726f5dbcc293239e83a6aa96d22dbadaf0d3fc32ee4564c72ff0ec6b488aff447ab685e160d63d7540e306c1eeaf3c2f75f2aa739a26988f6aa9298fd162c1bc57ef7e7692c453be7e9a91d4ec694409f3e61d86e7b22b262ffe3ec47fbc046e7aca0d1415b7eb358a272101529f993fc3a8bb8edeccfbe66659f115e6708fdaabf37980c600be816d654e8ee0e1acab0020a9e8982cb2f8c8dc54ff79e4f55d376f8e94f267834ee82ad42be5a34268da6ffe2e202ced4849c75f0f09ba81d7c1d9983fee34c9acac349163c597dac77cdedd2c0476f2edee4fc46189c8dd77b8606250d675b22684d2ff67b88bdde6b21fb4fda8054d539f3b3215af211132135823db672cd1ed66eaa305a5b1581b4ef4f4dd94c25a401fe3dddf96d38bdab6a35a47f75c41607e90befaf0b2e5581f0b9cdf15cb45545759763cc6f3454a56352c1f00e3a24eb55fd9a1d603ca1c5519b796a7cdb471bd2cd5febcf57097a85600cd37c28adf8479ba183913790868a85f34becf44bd2213db754bafaacef1bc2db8f1b33b9e6f3861d81533b1d6c9b3ada51a56a727301acef722f6f1c8b13d68f7bf670e0f4ca5af6c995c162a287cbc13404d57bd6a2614f0fd2cbac19793548d"
       }
      },
      "method": "pgp+rsa-pkcsv1.5",
      "type": "rsa"
     }
    },
    "type": "rsa"
   }
  },
  "readme": "",
  "steps": [
   {
    "_type": "step",
    "expected_command": [],
    "expected_materials": [],
    "expected_products": [
     [
      "CREATE",
      "*.deb"
     ],
     [
      "DISALLOW",
      "*.deb"
     ]
    ],
    "name": "rebuild",
    "pubkeys": [
     "8deb0bef1d99feb8b9a90fb192ef6d6141641e5c"
    ],
    "threshold": 1
   }
  ]
 }
}
```

In this file, GPG fingerprint `8deb0bef1d99feb8b9a90fb192ef6d6141641e5c` corresponds to a rebuilder signing key 
(notset-rebuilder) and `9fa64b92f95e706bf28e2ca6484010b5cdc576e2` corresponds to the key used to sign the 
`root.layout` file. In this setup example, you can the keys by using links [9FA64B92F95E706BF28E2CA6484010B5CDC576E2.asc](https://raw.githubusercontent.com/QubesOS/qubes-builder/master/keys/9FA64B92F95E706BF28E2CA6484010B5CDC576E2.asc) 
and [8DEB0BEF1D99FEB8B9A90FB192EF6D6141641E5C.asc](https://qubes.notset.fr/rebuild/8DEB0BEF1D99FEB8B9A90FB192EF6D6141641E5C.asc).
Once you have keys locally and verified them:
```
$ GNUPGHOME=/var/lib/intoto/gnupg gpg --import 9FA64B92F95E706BF28E2CA6484010B5CDC576E2.asc 8DEB0BEF1D99FEB8B9A90FB192EF6D6141641E5C.asc
```

### Enable the transport

You need to enable `in-toto` transport for Qubes and Debian repositories. In `/etc/apt/sources.list.d/qubes-r4.list` you need
to replace `https` by `intoto`. So up to commented entries, you should have:
```
# Main qubes updates repository
deb [arch=amd64] intoto://deb.qubes-os.org/r4.1/vm bullseye main

# Qubes updates candidates repository
deb [arch=amd64] intoto://deb.qubes-os.org/r4.1/vm bullseye-testing main
```

Doing the same for Debian, in `/etc/apt/sources.list` you obtain for a selected mirror:
```
deb intoto://ftp.fr.debian.org/debian bullseye main contrib non-free
deb intoto://ftp.fr.debian.org/debian-security bullseye-security main contrib non-free
```

Please note that there is currently an issue in replacing `intoto` for `https` mirrors only
(see https://github.com/in-toto/apt-transport-in-toto/issues/34). Ensure to have a supported `http` mirror selected.

### DYO `root.layout`
If doing on your own the `root.layout` file, `signatures` section and `keys` are empty. Before signing the
whole file, you need to provide `keys` sections. For that you can use the following command:
```
$ python3 <<EOL
import json

from securesystemslib.gpg.functions import *

keys = securesystemslib.gpg.functions.export_pubkeys(
  [
    "8deb0bef1d99feb8b9a90fb192ef6d6141641e5c", 
    "9fa64b92f95e706bf28e2ca6484010b5cdc576e2"
    ], homedir="/var/lib/intoto/gnupg")

print(json.dumps(keys, indent=1))
EOL
```

Then, the `root.layout` can be signed using `in-toto` tools:
```
$ GNUPG=qubes-gpg-client-wrapper in-toto-sign --verbose --gpg 9fa64b92f95e706bf28e2ca6484010b5cdc576e2 -f root.layout
```

If any syntax error has been made, the file won't be signed. Behind the scenes, `in-toto` tools use `securesystemslib` 
for GPG operations and now supports passing `GNUPG` gpg client to use. By default, it uses `gpg2/gpg`. 
Please note that if `expiration` is not given in original `root.layout` before signing process then, it uses one 
month as default expiration time. Also, pay attention that we have provided currently only one rebuilder 
in `/etc/apt/apt.conf.d/intoto` for which we can only set `threshold` to `1` in `root.layout`. 
See upstream documentation about that.
