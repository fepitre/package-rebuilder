#!/usr/bin/python3

from app.config.config import Config
from app.tasks.rebuilder import get, _generate_results, _metadata_to_db

# print("Generate DB results...")
# result = {"rebuild": []}
# for project in ["qubesos", "debian"]:
#     for dist in Config["project"][project]["dist"]:
#         _metadata_to_db.delay(dist, unreproducible=False)
#         _metadata_to_db.delay(dist, unreproducible=True)

# print("Trigger qubes-r4.1-vm-bullseye...")
# get.delay("qubes-4.1-vm-bullseye.all")
# get.delay("qubes-4.1-vm-bullseye.amd64")

print("Trigger bullseye...")
get.delay("bullseye+essential.amd64")
get.delay("bullseye+essential.all")
get.delay("bullseye+required.amd64")
get.delay("bullseye+required.all")
get.delay("bullseye+build-essential.amd64")
get.delay("bullseye+build-essential.all")
get.delay("bullseye+gnome.amd64")
get.delay("bullseye+gnome.all")
# get.delay("bullseye+full.all")
# get.delay("bullseye+full.amd64")

# print("Trigger unstable...")
# get.delay("unstable+full.all")
# get.delay("unstable+full.amd64")

# print("Generate results...")
# _generate_results.delay("qubesos")
# _generate_results.delay("debian")

print("Done!")
