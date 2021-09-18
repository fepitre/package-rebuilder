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
import numpy as np
import matplotlib.pyplot as plt

from packaging.version import parse as parse_version
from jinja2 import Template

from app.libs.common import get_celery_active_tasks
from app.config.config import Config
from app.libs.exceptions import RebuilderException
from app.libs.getter import RebuilderDist, get_rebuild_packages, getPackage

HTML_TEMPLATE = Template("""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="" xml:lang="">
<head>
  <title>{{dist}} rebuild status</title>
  <style>
  body { font-family: sans-serif; }
  td, th { border: solid 2px darkgrey; padding: 2px; }
  table { border-collapse: collapse; width: 50%;}
  </style>
</head>
<body>
<h1 id="dist">{{dist}}</h1>
<table>
{%- for package_set in packages.keys() -%}<img src="{{plots[package_set]}}"/></a>{{ '<br>' if loop.index % 2 == 0 }}{%- endfor %}
</table>
{%- for package_set, pkg_list in packages.items() -%}
<h3 id="{{package_set}}">{{package_set}}</h3>
<tbody>
<table>
{%- for pkg in pkg_list %}
<tr><td>{{pkg['name']}}-{{pkg['version']}}</td><td align="center"><a href="{{pkg['log']}}"><img src="{{pkg['badge']}}" alt="{{pkg['status']}}"/></a></td></tr>
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


def generate_results(app, distribution):
    rebuild_results = get_rebuild_packages(app)
    running_rebuilds = [getPackage(p)
                        for p in get_celery_active_tasks(app, "app.tasks.rebuilder.rebuild")
                        if isinstance(p, dict)]
    try:
        for dist in Config["project"][distribution]["dist"]:
            dist = RebuilderDist(dist)
            results_path = f"/rebuild/{dist.project}/results"
            os.makedirs(results_path, exist_ok=True)

            # Get results for given dist
            results = [x for x in rebuild_results.values()
                       if x['distribution'] == dist.distribution and x['arch'] == dist.arch]

            # Filter latest results
            latest_results = {}
            for r in results:
                if latest_results.get(r["name"], None):
                    if parse_version(r["version"]) \
                            <= parse_version(latest_results[r["name"]]["version"]):
                        continue
                latest_results[r["name"]] = r

            # Get BuildPackages that go into rebuild
            dist.repo.get_packages()

            packages_list = {}
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
                packages_list[pkgset_name] = []
                for package in packages_to_rebuild:
                    if package in running_rebuilds:
                        package["badge"] = BADGES["running"]
                        result["running"].append(package)
                    elif latest_results.get(package.name, {}):
                        pkg = getPackage(latest_results[package.name])
                        if pkg.status in ("reproducible", "unreproducible", "failure", "retry"):
                            if pkg.log and os.path.basename(pkg.log):
                                pkg.log = f'../logs/{os.path.basename(pkg.log)}'
                            pkg["badge"] = BADGES[pkg.status]
                            result[pkg.status].append(dict(pkg))
                    else:
                        pkg = package
                        pkg["badge"] = BADGES["pending"]
                        result["pending"].append(dict(pkg))

                # We simplify how we render HTML
                for packages in result.values():
                    packages_list[pkgset_name] += packages

                x = []
                legends = []
                explode = []
                colors = []
                for status in ["reproducible", "unreproducible", "failure", "retry", "running", "pending"]:
                    if not result[status]:
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
                ax.set(aspect="equal", title=f"{dist.distribution}+{pkgset_name}.{dist.arch}")
                for idx, text in enumerate(texts):
                    text.set_color(colors[idx])
                fig.savefig(f"{results_path}/{dist.distribution}_{pkgset_name}.{dist.arch}.png",
                            bbox_inches='tight')
                plt.close(fig)

                plots[pkgset_name] = f"{dist.distribution}_{pkgset_name}.{dist.arch}.png"

                with open(f"{results_path}/{dist}.json", "w") as fd:
                    fd.write(json.dumps(result, indent=2) + "\n")

            data = {
                "dist": f"{dist.project} {dist.distribution} ({dist.arch})",
                "packages": packages_list,
                "plots": plots
            }
            with open(f"{results_path}/{dist.distribution}.{dist.arch}.html", 'w') as fd:
                fd.write(HTML_TEMPLATE.render(**data))

    except Exception as e:
        raise RebuilderException(f"Failed to generate status: {str(e)}")
