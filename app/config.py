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

import configparser
import os

DEFAULT_CONFIG = {
    "broker": os.environ.get('CELERY_BROKER_URL', "redis://broker:6379/0"),
    "backend": os.environ.get('CELERY_RESULT_BACKEND', "mongodb://backend:27017"),
    "schedule_get": 1800,
    "schedule_generate_results": 300,
    "max_retries": 2,
    "snapshot": "http://snapshot.notset.fr"
}

# Currently supported project
SUPPORTED_PROJECTS = ["qubesos", "debian", "fedora"]

# Filter allowed options in sections for projects (e.g. 'common', 'qubesos', 'debian', etc.)
SECTION_OPTIONS = [
    "schedule_get", "snapshot", "in-toto-sign-key-fpr", "in-toto-sign-key-unreproducible-fpr",
    "repo-ssh-key", "repo-remote-ssh-host", "repo-remote-ssh-basedir", "dist",
    "schedule_generate_results"
]


config = configparser.RawConfigParser(allow_no_value=False)


config_path = os.environ.get("PACKAGE_REBUILDER_CONF", f"{os.path.curdir}/rebuilder.conf")
if not os.path.exists(config_path):
    raise ValueError(f"Cannot find config file: {config_path}")
config.read(config_path)

Config = {
    "celery": {
        "broker": config.get("common", "broker", fallback=DEFAULT_CONFIG["broker"]),
        "backend": config.get("common", "backend", fallback=DEFAULT_CONFIG["backend"]),
        "max_retries": config.get("common", "max_retries", fallback=DEFAULT_CONFIG["max_retries"]),
    },
    "common": {
        "schedule_get": config.get("common", "schedule_get", fallback=DEFAULT_CONFIG["schedule_get"]),
        "schedule_generate_results": config.get("common", "schedule_generate_results", fallback=DEFAULT_CONFIG["schedule_generate_results"]),
        "snapshot": config.get("common", "snapshot", fallback=DEFAULT_CONFIG["snapshot"]),
    },
    "project": {}
}

if "common" in config.sections():
    for option in SECTION_OPTIONS:
        config_option = config.get("common", option, fallback=None)
        if config_option:
            if option == "dist":
                # fixme: allow dist in common like it was mostly before this refactor?
                continue
            if option in ("schedule_get", "schedule_generate_results"):
                config_option = int(config_option)
            Config["common"][option] = config_option

for project in SUPPORTED_PROJECTS:
    if project not in config.sections():
        continue
    Config["project"][project] = {}
    for option in SECTION_OPTIONS:
        config_option = config.get(project, option,
                                   fallback=Config["common"].get(option, None))
        if config_option:
            Config["project"][project].setdefault(option, {})
            if option == "dist":
                config_option = config_option.replace(' ', '\n').splitlines()
            if option in ("schedule_get", "schedule_generate_results"):
                config_option = int(config_option)
            Config["project"][project][option] = config_option
