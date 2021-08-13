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

DEFAULT_SCHEDULE = 1800
DEFAULT_MAX_RETRIES = 2

config = configparser.RawConfigParser(allow_no_value=False)
# TODO: put the configuration elsewhere
config_path = '{}/rebuilder.conf'.format(os.path.curdir)
if not os.path.exists(config_path):
    raise ValueError("Cannot find config file: {}".format(config_path))
config.read(config_path)

broker = config.get('DEFAULT', 'broker', fallback='redis://broker:6379/0')
backend = config.get('DEFAULT', 'backend', fallback='mongodb://backend:27017')

if 'CELERY_BROKER_URL' in os.environ:
    broker = os.environ['CELERY_BROKER_URL']

if 'CELERY_RESULT_BACKEND' in os.environ:
    broker = os.environ['CELERY_RESULT_BACKEND']

snapshot = config.get('DEFAULT', 'snapshot', fallback='http://debian.notset.fr/snapshot')

try:
    schedule = int(config.get('DEFAULT', 'schedule', fallback=DEFAULT_SCHEDULE))
except ValueError:
    schedule = DEFAULT_SCHEDULE

try:
    max_retries = int(config.get('DEFAULT', 'max_retries', fallback=DEFAULT_MAX_RETRIES))
except ValueError:
    max_retries = DEFAULT_MAX_RETRIES

sign_keyid = config.get('DEFAULT', 'in-toto-sign-key-fpr')
ssh_key = config.get('DEFAULT', 'repo-ssh-key')
remote_ssh_host = config.get('DEFAULT', 'repo-remote-ssh-host')
remote_ssh_basedir = config.get('DEFAULT', 'repo-remote-ssh-basedir')
dist = config.get('DEFAULT', 'dist', fallback=[])

Config = {
    'broker': broker,
    'backend': backend,
    'snapshot': snapshot,
    'schedule': schedule,
    'max_retries': max_retries,
    'sign_keyid': sign_keyid,
    'ssh_key': ssh_key,
    'remote_ssh_host': remote_ssh_host,
    'remote_ssh_basedir': remote_ssh_basedir,
    'dist': dist
}
