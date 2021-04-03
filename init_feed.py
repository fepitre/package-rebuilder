#!/usr/bin/python3

from app.tasks.rebuilder import get

print("Trigger qubes-r4.1-vm-bullseye...")
get.delay("qubes-4.1-vm-bullseye.all")
get.delay("qubes-4.1-vm-bullseye.amd64")

print("Trigger bullseye...")
get.delay("bullseye+essential+required+build-essential.all")
get.delay("bullseye+essential+required+build-essential.amd64")

print("Trigger unstable...")
get.delay("unstable+essential+required+build-essential.all")
get.delay("unstable+essential+required+build-essential.amd64")

print("Done!")
