#!/usr/bin/python3

from app.tasks.rebuilder import get

# print("Trigger r4.0/vm/bullseye")
# get.delay("4.0", "vm", "bullseye")

print("Trigger r4.1/vm/bullseye...")
get.delay("4.1", "vm", "bullseye")

print("Done!")
