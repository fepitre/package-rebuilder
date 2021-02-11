DEBIAN = {
    "buster": "10",
    "bullseye": "11",
    "bookworm": "12",
    "sid": "12"
}

DEBIAN_ARCHES = {
    "x86_64": "amd64",
}


def is_qubes(dist):
    return dist.startswith("qubes")


def is_fedora(dist):
    return dist.startswith("fedora") or dist.startswith("fc")


def is_debian(dist):
    return DEBIAN.get(dist, None) is not None
