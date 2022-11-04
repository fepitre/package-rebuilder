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

import yaml
import os
from pathlib import Path

from app.exceptions import RebuilderException

DEFAULT_CONFIG = {
    "broker": os.environ.get("CELERY_BROKER_URL", "redis://broker:6379/0"),
    "backend": os.environ.get("CELERY_RESULT_BACKEND", "mongodb://backend:27017"),
    "schedule_get": 1800,
    "schedule_generate_results": 300,
    "max_retries": 2,
    "snapshot": "https://snapshot.notset.fr",
}

# Currently supported project
SUPPORTED_PROJECTS = ["qubesos", "debian", "fedora"]

# Filter allowed options
SECTION_OPTIONS = [
    "schedule_get",
    "snapshot",
    "in-toto-sign-key-fpr",
    "in-toto-sign-key-unreproducible-fpr",
    "repo-ssh-key",
    "repo-remote-ssh-host",
    "repo-remote-ssh-basedir",
    "dist",
    "schedule_generate_results",
]


class RebuilderConfiguration(dict):
    def __init__(self, conf_file):
        if isinstance(conf_file, str):
            conf_file = Path(conf_file).resolve()

        if not conf_file.exists():
            raise RebuilderException(f"Cannot find {conf_file}.")

        try:
            conf = yaml.safe_load(conf_file.read_text())
        except (yaml.YAMLError, OSError) as e:
            raise RebuilderException(f"Failed to load configuration: {str(e)}")

        final_conf = {
            "celery": {
                "broker": conf.get("celery", {}).get(
                    "broker", DEFAULT_CONFIG["broker"]
                ),
                "backend": conf.get("celery", {}).get(
                    "backend", DEFAULT_CONFIG["backend"]
                ),
                "max_retries": conf.get("celery", {}).get(
                    "max_retries", DEFAULT_CONFIG["max_retries"]
                ),
            },
            "default": {
                "schedule_get": conf.get("default", {}).get(
                    "schedule_get", DEFAULT_CONFIG["schedule_get"]
                ),
                "schedule_generate_results": conf.get("default", {}).get(
                    "schedule_generate_results",
                    DEFAULT_CONFIG["schedule_generate_results"],
                ),
                "snapshot": conf.get("default", {}).get(
                    "snapshot", DEFAULT_CONFIG["snapshot"]
                ),
            },
            "project": {},
        }

        for project in SUPPORTED_PROJECTS:
            if project not in conf:
                continue
            final_conf["project"].setdefault(project, {})
            for option in SECTION_OPTIONS:
                value = conf[project].get(
                    option, conf.get("default", {}).get(option, None)
                )
                if value:
                    conf["project"][project].setdefault(option, {})
                    if option in ("schedule_get", "schedule_generate_results"):
                        value = int(value)
                    conf["project"][project][option] = value

        super().__init__(**final_conf)


config_path = (
    Path(os.environ.get("PACKAGE_REBUILDER_CONF", f"{os.path.curdir}/rebuilder.conf"))
    .expanduser()
    .resolve()
)

if not config_path.exists():
    raise ValueError(f"Cannot find config file: {config_path}")

config = RebuilderConfiguration(config_path)
