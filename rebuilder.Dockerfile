FROM rebuilder_base:latest
MAINTAINER Frédéric Pierret <frederic.pierret@qubes-os.org>

# REBUILDER
RUN apt-get update && apt-get install -y mmdebstrap in-toto python3-dateutil python3-rstr python3-setuptools \
    debian-keyring debian-archive-keyring debian-ports-archive-keyring && apt-get clean all
# Use upstream python-debian code
# This is needed for libs/rebuilder.py when looping over binpkg in get_binary()
RUN git clone https://salsa.debian.org/python-debian-team/python-debian /opt/python-debian
RUN cd /opt/python-debian && git checkout e28d7a5729b187cfd0ec95da25030224cd10021a && python3 setup.py build install
RUN git clone https://github.com/fepitre/debrebuild /opt/debrebuild
