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

import os
import json
import hashlib
import time
import numpy as np
import matplotlib.pyplot as plt

from jinja2 import Template

from app.config import Config
from app.lib.exceptions import RebuilderException, RebuilderExceptionReport
from app.lib.get import Package, RebuilderDist, getPackage
# from app.lib.tool import get_rebuild_packages, get_celery_active_tasks

HTML_TEMPLATE = Template("""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="" xml:lang="">
<head>
    <title>{{dist}} rebuild status</title>
    <style>
        body { font-family: sans-serif; }
        td { border: solid 2px darkgrey; padding: 2px; width: 80%}
        th { border: solid 2px darkgrey; padding: 2px; }
        td+td { width: auto; }
        table { border-collapse: collapse; width: 40%; table-layout: fixed; }
    </style>
</head>
<body>
    <h1 id="dist">{{dist}}</h1>
    <table>
        {%- for package_set in results.keys() -%}<img src="{{plots[package_set]}}"/></a>{{ '<br>' if loop.index % 2 == 0 }}{%- endfor %}
    </table>
    {%- for package_set, status in results.items() -%}
        <h3 id="{{package_set}}">{{package_set}}</h3>
        <tbody>
            <table>
            {%- for s, packages in status.items() %}
                {%- for pkg in packages %}
                    <tr><td>{{pkg['name']}}-{{pkg['version']}}</td><td align="center"><a href="{{pkg['log']}}"><img src="{{pkg['badge']}}" alt="{{pkg['status']}}"/></a></td></tr>
                {%- endfor %}
                {{ '<tr><td></td><td></td></tr>' if not loop.last }}
            {%- endfor %}
            </table>
        </tbody>
    {%- endfor %}
</body>
</html>
""")

BADGES = {
    "reproducible": "https://img.shields.io/badge/-success-success",
    "unreproducible": "https://img.shields.io/badge/-unreproducible-yellow",
    "failure": "https://img.shields.io/badge/-failure-red",
    "retry": "https://img.shields.io/badge/-retry-orange",
    "pending": "https://img.shields.io/badge/-pending-lightgrey",
    "running": "https://img.shields.io/badge/-running-dodgerblue"
}

COLORS = {
    "reproducible": "forestgreen",
    "unreproducible": "goldenrod",
    "failure": "firebrick",
    "retry": "orangered",
    "pending": "grey",
    "running": "dodgerblue"
}

EXPLODE = {
    "reproducible": 0,
    "unreproducible": 0,
    "failure": 0.1,
    "pending": 0.2,
    "retry": 0.3,
    "running": 0.5
}


def func(pct, allvals):
    absolute = round(pct / 100. * np.sum(allvals))
    res = "{:.1f}%\n({:d})".format(pct, absolute)
    return res


def generate_plots(result, distribution, pkgset_name, arch, results_path):
    x = []
    legends = []
    explode = []
    colors = []
    for status in ["reproducible", "unreproducible", "failure", "retry",
                   "running", "pending"]:
        if not result.get(status, None):
            result.pop(status, None)
            continue
        count = len(result[status])
        x.append(count)
        legends.append(status)
        colors.append(COLORS[status])
        explode.append(EXPLODE[status])

    fig, ax = plt.subplots(figsize=(9, 6), subplot_kw=dict(aspect="equal"))
    wedges, texts, autotexts = ax.pie(
        x, colors=colors, explode=explode,
        labels=x,
        autopct="%.1f%%",
        shadow=False, startangle=270,
        normalize=True,
        labeldistance=1.1)
    ax.legend(wedges, legends, title="Status", loc="center left",
              bbox_to_anchor=(1, 0, 0.5, 1))
    ax.set(aspect="equal", title=f"{distribution}+{pkgset_name}.{arch}")
    for idx, text in enumerate(texts):
        text.set_color(colors[idx])
    fig.savefig(f"{results_path}/{distribution}_{pkgset_name}.{arch}.png")
    plt.close(fig)


def generate_results(app, project):
    running_rebuilds = [getPackage(p)
                        for p in get_celery_active_tasks(app, "app.tasks.rebuilder.rebuild")
                        if isinstance(p, dict)]
    try:
        results = {}
        results_path = f"/var/lib/rebuilder/rebuild/{project}/results"
        os.makedirs(results_path, exist_ok=True)
        for dist in Config["project"][project]["dist"]:
            dist = RebuilderDist(dist)
            rebuild_results = get_rebuild_packages(app, dist)
            results.setdefault(dist.distribution, {})
            results[dist.distribution].setdefault(dist.arch, {})

            # Get BuildPackages that go into rebuild
            dist.repo.get_packages()

            plots = {}
            # Filter results per status on every package sets
            for pkgset_name in dist.package_sets:
                packages_to_rebuild = dist.repo.get_packages_to_rebuild(pkgset_name)

                # Prepare the result data
                result = {
                    "reproducible": [],
                    "unreproducible": [],
                    "failure": [],
                    "running": [],
                    "pending": [],
                    "retry": []
                }
                for package in packages_to_rebuild:
                    if package in running_rebuilds:
                        package["badge"] = BADGES["running"]
                        result["running"].append(package.to_dict())
                    elif rebuild_results.get(str(package), {}):
                        pkg = rebuild_results[str(package)]
                        if pkg.status in ("reproducible", "unreproducible", "failure", "retry"):
                            pkg["badge"] = BADGES[pkg.status]
                            # fixme: temporary fixup
                            pkg.log = pkg.log.replace("/var/lib/rebuilder/rebuild/", "/") \
                                .replace("/rebuild/", "/")
                            if pkg.diffoscope:
                                pkg.diffoscope = pkg.diffoscope. \
                                    replace("/var/lib/rebuilder/rebuild/", "/"). \
                                    replace("/rebuild/", "/")
                            if pkg.metadata and pkg.metadata.get("reproducible", None):
                                pkg.metadata["reproducible"] = \
                                    pkg.metadata["reproducible"].replace(
                                        "/var/lib/rebuilder/rebuild/", "/").replace(
                                        "/rebuild/", "/")
                            if pkg.metadata and pkg.metadata.get("unreproducible", None):
                                pkg.metadata["unreproducible"] = \
                                    pkg.metadata["unreproducible"].replace(
                                        "/var/lib/rebuilder/rebuild/", "/").replace(
                                        "/rebuild/", "/")
                            result[pkg.status].append(pkg.to_dict())
                    else:
                        pkg = package
                        pkg["badge"] = BADGES["pending"]
                        result["pending"].append(pkg.to_dict())

                generate_plots(result, dist.distribution, pkgset_name, dist.arch, results_path)

                plots[pkgset_name] = f"{dist.distribution}_{pkgset_name}.{dist.arch}.png"
                results[dist.distribution][dist.arch][pkgset_name] = result

            data = {
                "dist": f"{project} {dist.distribution} ({dist.arch})",
                "results": results[dist.distribution][dist.arch],
                "plots": plots
            }
            with open(f"{results_path}/{dist.distribution}.{dist.arch}.html", 'w') as fd:
                fd.write(HTML_TEMPLATE.render(**data))

        # all arches
        for dist in results.keys():
            sum_arches = "+".join(results[dist].keys())
            all_arches = {}
            plots = {}
            for arch in results[dist].keys():
                for ps in results[dist][arch].keys():
                    all_arches.setdefault(ps, {})
                    for s in results[dist][arch][ps].keys():
                        all_arches[ps].setdefault(s, [])
                        all_arches[ps][s] += results[dist][arch][ps][s]
            results[dist][sum_arches] = all_arches

            for ps in results[dist][sum_arches].keys():
                generate_plots(results[dist][sum_arches][ps], dist, ps, sum_arches, results_path)
                plots[ps] = f"{dist}_{ps}.{sum_arches}.png"
            # data = {
            #     "dist": f"{project} {dist} ({sum_arches})",
            #     "results": results[dist][sum_arches],
            #     "plots": plots
            # }
            # with open(f"{results_path}/{dist}.{sum_arches}.html", 'w') as fd:
            #     fd.write(HTML_TEMPLATE.render(**data))

        with open(f"{results_path}/{project}.json", "w") as fd:
            fd.write(json.dumps(results))

    except Exception as e:
        raise RebuilderException(f"Failed to generate status: {str(e)}")


class RebuilderDB:
    def __init__(self, conn, project, **kwargs):
        self.conn = conn
        self.project = project
        self.db = self.conn["rebuilder"]
        self.collection = self.db[project]

    @staticmethod
    def _gen_id(patterns):
        return hashlib.md5(''.join(patterns).encode()).hexdigest()

    def _clear(self, col, do=False):
        if do:
            self.db[col].drop()

    def _get(self, col, query):
        results = []
        collection = self.db[col]
        queried_results = list(collection.find(query))
        for result in queried_results:
            results.append(result)
        return results

    def _insert(self, col, data, provided_id=None):
        collection = self.db[col]
        if provided_id:
            data["_id"] = provided_id
        inserted_id = collection.insert_one(data).inserted_id
        return inserted_id

    def _update(self, col, data, provided_id):
        collection = self.db[col]
        updated = collection.update_one({"_id": provided_id}, {"$set": data})
        return provided_id, updated.modified_count == 0

    def _delete(self, col, input_id):
        collection = self.db[col]
        deleted = collection.delete_one({"_id": input_id})
        return deleted.deleted_count == 0

    def _dump(self, col, limit=None, with_id=True):
        collection = self.db[col]
        cursor = collection.find()
        data = list(cursor)
        dumped_data = []
        for num, doc in enumerate(data):
            if not with_id:
                del doc["_id"]
            # doc["_id"] = str(doc["_id"])
            dumped_data.append(doc)
            if limit and limit == num:
                break
        return dumped_data

    def _dump_to_json(self, col, limit=None, dst=None):
        if not dst:
            dst = f"rebuilderdb-dump-{time.time()}.json"
        try:
            with open(dst, 'w') as dst_fd:
                dst_fd.write(json.dumps(self._dump(col=col, limit=limit)))
        except Exception as e:
            raise RebuilderExceptionReport from e

    def _load(self, col, src):
        collection = self.db[col]
        with open(src, 'r') as src_fd:
            data = json.loads(src_fd.read())
        try:
            collection.insert_many(data)
        except Exception as e:
            raise RebuilderExceptionReport from e

    def dump_buildrecords(self, limit=None, with_id=False):
        dump = self._dump(self.project, limit=limit, with_id=with_id)
        parsed_dump = sorted([getPackage(p) for p in dump], key=lambda x: str(x))
        return parsed_dump

    def dump_buildrecords_to_json(self, limit=None, dst=None):
        self._dump_to_json(self.project, limit=limit, dst=dst)

    def dump_buildrecords_as_dict(self, limit=None, with_id=False):
        return {str(p): p for p in self.dump_buildrecords(limit=limit, with_id=with_id)}

    def insert_buildrecord(self, package):
        buildrecord = dict(package)
        buildrecord_id = self._insert(self.project, buildrecord, self._gen_id([str(package)]))
        return buildrecord_id

    def delete_buildrecord(self, package):
        buildrecord_id = self._gen_id([str(package)])
        return self._delete(self.project, buildrecord_id)

    def get_buildrecord_by_id(self, buildrecord_id):
        result = {}
        queried_result = self._get(self.project, {"_id": buildrecord_id})
        return queried_result[0] if queried_result else result

    def get_buildrecord(self, package):
        buildrecord_id = self._gen_id([str(package)])
        buildrecord = self.get_buildrecord_by_id(buildrecord_id)
        if buildrecord:
            try:
                del buildrecord["_id"]
                buildrecord = Package.from_dict(buildrecord)
            except KeyError as e:
                raise RebuilderExceptionReport(str(e))
        return buildrecord

    def update_buildrecord(self, package):
        self.delete_buildrecord(package)
        return self.insert_buildrecord(package)
