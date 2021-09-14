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

import celery
from app.config.config import Config
from app.libs.logger import log

app = celery.Celery("PackageRebuilder")

celery_app_conf = {
    "broker_url": Config["celery"]["broker"],
    "result_backend": Config["celery"]["backend"],
    "include": [
        "app.tasks.rebuilder",
    ],
    "enable_utc": True,
    "timezone": "UTC",
    "task_routes": {
        "app.tasks.rebuilder.get": {"queue": "get"},
        "app.tasks.rebuilder.rebuild": {"queue": "rebuild"},
        "app.tasks.rebuilder.attest": {"queue": "attest"},
        "app.tasks.rebuilder.report": {"queue": "report"},
        "app.tasks.rebuilder.upload": {"queue": "upload"},
        "app.tasks.rebuilder._generate_results": {"queue": "report"},
        "app.tasks.rebuilder._metadata_to_db": {"queue": "get"},
    }
}

app.conf.update(**celery_app_conf)

log.debug(Config)

if __name__ == "__main__":
    app.start()
