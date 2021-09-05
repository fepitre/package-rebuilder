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

from app.libs.logger import log
from app.libs.common import get_celery_queued_tasks, get_celery_unacked_tasks
from app.config.config import Config
from app.libs.exceptions import RebuilderExceptionDist, RebuilderException
from app.libs.getter import RebuilderDist, get_rebuilt_packages, BuildPackage

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


def func(pct, allvals):
    absolute = round(pct / 100. * np.sum(allvals))
    res = "{:.1f}%\n({:d})".format(pct, absolute)
    return res


def generate_results(app):
    rebuild_results = get_rebuilt_packages(app)
    running_rebuilds = [BuildPackage.from_dict(p) for p in get_celery_unacked_tasks(app)
                        if isinstance(p, dict)]
    try:
        for dist in Config['dist'].split():
            dist = RebuilderDist(dist)
            results_path = f"/rebuild/{dist.distribution}/results"
            os.makedirs(results_path, exist_ok=True)

            # Get results for given dist
            results = [x for x in rebuild_results
                       if x['dist'] == dist.name and x['arch'] == dist.arch]

            # Filter latest results
            latest_results = {}
            for r in results:
                if latest_results.get(r["name"], None):
                    if parse_version(r["version"]) <= parse_version(latest_results[r["name"]]["version"]):
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
                result = {"reproducible": [], "unreproducible": [], "failure": [], "running": [], "pending": [], "retry": []}
                packages_list[pkgset_name] = []
                for package in packages_to_rebuild:
                    if package in running_rebuilds:
                        package["badge"] = "https://img.shields.io/badge/-running-dodgerblue"
                        result["running"].append(package)
                    elif latest_results.get(package.name, {}):
                        pkg = latest_results[package.name]
                        if latest_results[package.name]["status"] == "reproducible":
                            pkg["badge"] = "https://img.shields.io/badge/-success-success"
                            if pkg["log"] and os.path.basename(pkg["log"]):
                                pkg["log"] = f'../log-ok/{os.path.basename(pkg["log"])}'
                            result["reproducible"].append(pkg)
                        elif latest_results[package.name]["status"] == "unreproducible":
                            pkg["badge"] = "https://img.shields.io/badge/-unreproducible-yellow"
                            if pkg["log"] and os.path.basename(pkg["log"]):
                                pkg["log"] = f'../log-ok-unreproducible/{os.path.basename(pkg["log"])}'
                            result["unreproducible"].append(pkg)
                        elif latest_results[package.name]["status"] == "failure":
                            pkg["badge"] = "https://img.shields.io/badge/-failure-red"
                            if pkg["log"] and os.path.basename(pkg["log"]):
                                pkg["log"] = f'../log-fail/{os.path.basename(pkg["log"])}'
                            result["failure"].append(pkg)
                        elif latest_results[package.name]["status"] == "retry":
                            pkg["log"] = f'../log-fail/{os.path.basename(pkg["log"])}'
                            pkg["badge"] = "https://img.shields.io/badge/-retry-orange"
                            pkg["status"] = "retry"
                            result["retry"].append(pkg)
                    else:
                        pkg = package
                        # On clean of FAILED tasks, previous info remains
                        pkg["log"] = ""
                        pkg["badge"] = "https://img.shields.io/badge/-pending-lightgrey"
                        result["pending"].append(pkg)

                # We simplify how we render HTML
                for packages in result.values():
                    packages_list[pkgset_name] += packages

                x = []
                legends = []
                explode = []
                colors = []
                if result["reproducible"]:
                    count = len(result["reproducible"])
                    x.append(count)
                    legends.append(f"Reproducible")
                    colors.append("forestgreen")
                    explode.append(0)
                if result["unreproducible"]:
                    count = len(result["unreproducible"])
                    x.append(count)
                    legends.append(f"Unreproducible")
                    colors.append("goldenrod")
                    explode.append(0)
                if result["failure"]:
                    count = len(result["failure"])
                    x.append(count)
                    legends.append(f"Failure")
                    colors.append("firebrick")
                    explode.append(0.1)
                if result["pending"]:
                    count = len(result["pending"])
                    x.append(count)
                    legends.append(f"Pending")
                    colors.append("grey")
                    explode.append(0.2)
                if result["retry"]:
                    count = len(result["retry"])
                    x.append(count)
                    legends.append(f"Retry")
                    colors.append("orangered")
                    explode.append(0.3)
                if result["running"]:
                    count = len(result["running"])
                    x.append(count)
                    legends.append(f"Running")
                    colors.append("dodgerblue")
                    explode.append(0.5)

                fig, ax = plt.subplots(figsize=(9, 6), subplot_kw=dict(aspect="equal"))
                wedges, texts, autotexts = ax.pie(
                    x, colors=colors, explode=explode,
                    labels=x,
                    autopct="%.1f%%",
                    shadow=False, startangle=270,
                    normalize=True,
                    labeldistance=1.1)
                ax.legend(wedges, legends, title="Status", loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))
                ax.set(aspect="equal", title=f"{dist.name}+{pkgset_name}.{dist.arch}")
                for idx, text in enumerate(texts):
                    text.set_color(colors[idx])
                fig.savefig(f"{results_path}/{dist.name}_{pkgset_name}.{dist.arch}.png", bbox_inches='tight')
                plt.close(fig)

                plots[pkgset_name] = f"{dist.name}_{pkgset_name}.{dist.arch}.png"

                with open(f"{results_path}/{dist}.json", "w") as fd:
                    fd.write(json.dumps(result, indent=2) + "\n")

            data = {"dist": f"{dist.distribution} {dist.name} ({dist.arch})", "packages": packages_list, "plots": plots}
            with open(f"{results_path}/{dist.name}.{dist.arch}.html", 'w') as fd:
                fd.write(HTML_TEMPLATE.render(**data))

    except Exception as e:
        raise RebuilderException("{}: failed to generate status.".format(str(e)))
