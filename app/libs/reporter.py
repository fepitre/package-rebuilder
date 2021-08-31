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
import requests
import numpy as np
import matplotlib.pyplot as plt

from packaging.version import parse as parse_version
from jinja2 import Template

from app.libs.logger import log
from app.config.config import Config
from app.libs.exceptions import RebuilderExceptionDist, RebuilderException
from app.libs.getter import RebuilderDist, get_rebuilt_packages

HTML_TEMPLATE = Template("""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="" xml:lang="">
<head>
  <title>{{dist}} rebuild status</title>
  <style>
  body { font-family: sans-serif; }
  td, th { border: solid 2px #dcdcdc; padding: 4px; }
  table { border-collapse: collapse; }
  </style>
</head>
<body>

<h1 id="dist">{{dist}}</h1>

{%- for package_set, packages in summary.items() -%}
<h3 id="{{package_set}}">{{package_set}}</h3>
<tbody>
<table>
{%- for pkg in packages %}
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
    return "{:.1f}%\n({:d})".format(pct, absolute)


def generate_results(app):
    html_summary = {}
    rebuild_results = get_rebuilt_packages(app)
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
            packages_list = dist.repo.get_buildpackages(dist.arch)

            # Filter results per status on every package sets
            for pkgset_name in dist.package_sets:
                # Get packages in a given set
                if dist.distribution == "debian":
                    packages_in_set = dist.repo.get_packages_set(pkgset_name)
                else:
                    packages_in_set = latest_results.keys()

                # Prepare the result data
                result = {"reproducible": [], "unreproducible": [], "failure": [], "pending": []}
                html_summary[pkgset_name] = []
                for pkg_name in packages_in_set:
                    if pkg_name not in packages_list.keys():
                        continue
                    if latest_results.get(pkg_name, {}) in packages_list[pkg_name]:
                        pkg = latest_results[pkg_name]
                        if latest_results[pkg_name]["status"] == "reproducible":
                            pkg["badge"] = "https://img.shields.io/badge/-success-success"
                            if pkg["log"]:
                                pkg["log"] = f'../log-ok/{os.path.basename(pkg["log"])}'
                            result["reproducible"].append(pkg)
                        elif latest_results[pkg_name]["status"] == "unreproducible":
                            pkg["badge"] = "https://img.shields.io/badge/-unreproducible-yellow"
                            if pkg["log"]:
                                pkg["log"] = f'../log-ok-unreproducible/{os.path.basename(pkg["log"])}'
                            result["unreproducible"].append(pkg)
                        elif latest_results[pkg_name]["status"] == "failure":
                            pkg["badge"] = "https://img.shields.io/badge/-failure-red"
                            if pkg["log"]:
                                pkg["log"] = f'../log-fail/{os.path.basename(pkg["log"])}'
                            result["failure"].append(pkg)
                        elif latest_results[pkg_name]["status"] == "retry":
                            pkg["badge"] = "https://img.shields.io/badge/-pending-lightgrey"
                            pkg["status"] = "pending"
                            result["pending"].append(pkg)
                    else:
                        pkg = packages_list[pkg_name][0]
                        pkg["badge"] = "https://img.shields.io/badge/-pending-lightgrey"
                        result["pending"].append(pkg)

                # We simplify how we render HTML
                for packages in result.values():
                    html_summary[pkgset_name] += packages

                x = []
                legends = []
                explode = []
                colors = []
                if result["reproducible"]:
                    count = len(result["reproducible"])
                    x.append(count)
                    legends.append(f"Reproducible")
                    colors.append("green")
                    explode.append(0)
                if result["unreproducible"]:
                    count = len(result["unreproducible"])
                    x.append(count)
                    legends.append(f"Unreproducible")
                    colors.append("orange")
                    explode.append(0)
                if result["failure"]:
                    count = len(result["failure"])
                    x.append(count)
                    legends.append(f"Failure")
                    colors.append("red")
                    explode.append(0)
                if result["pending"]:
                    count = len(result["pending"])
                    x.append(count)
                    legends.append(f"Pending")
                    colors.append("grey")
                    explode.append(0)

                fig, ax = plt.subplots(figsize=(9, 6), subplot_kw=dict(aspect="equal"))
                wedges, texts, autotexts = ax.pie(x, colors=colors, explode=explode, autopct=lambda pct: func(pct, x), shadow=True, startangle=90, normalize=True)
                ax.legend(wedges, legends, title="Status", loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))
                ax.set(aspect="equal", title=f"{dist.name}+{pkgset_name}.{dist.arch}")
                fig.savefig(f"{results_path}/{dist.name}_{pkgset_name}.{dist.arch}.png")
                plt.close(fig)

                # with open(f"{results_path}/{dist}_db.json", "w") as fd:
                #     fd.write(json.dumps(latest_results, indent=2) + "\n")

            data = {"dist": f"{dist.distribution} {dist.name} ({dist.arch})", "summary": html_summary}
            with open(f"{results_path}/{dist.name}.{dist.arch}.html", 'w') as fd:
                fd.write(HTML_TEMPLATE.render(**data))

    except (RebuilderExceptionDist, FileNotFoundError, ValueError) as e:
        raise RebuilderException("{}: failed to generate status.".format(str(e)))
