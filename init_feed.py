#!/usr/bin/python3

from app.tasks.rebuilder import get, _generate_results

print("Trigger qubes-r4.1-vm-bullseye...")
#get.delay("qubes-4.1-vm-bullseye.all")
get.delay("qubes-4.1-vm-bullseye.amd64")

#print("Trigger bullseye...")
#get.delay("bullseye+full.all")
#get.delay("bullseye+full.amd64")

print("Trigger unstable...")
get.delay("unstable+full.all")
get.delay("unstable+full.amd64")

print("Generate results...")
_generate_results.delay("qubesos")
_generate_results.delay("debian")

print("Done!")
