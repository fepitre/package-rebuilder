#!/usr/bin/python3

from app.tasks.rebuilder import get

print("Trigger qubes-r4.1-vm-bullseye...")
get.delay("qubes-4.1-vm-bullseye", "all")
get.delay("qubes-4.1-vm-bullseye", "x86_64")

print("Trigger bullseye...")
# "x86_64" is aliased to "amd64"
get.delay("bullseye", "all")
get.delay("bullseye", "x86_64")

print("Done!")
