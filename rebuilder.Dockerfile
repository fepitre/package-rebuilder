FROM rebuilder_base:latest
MAINTAINER Frédéric Pierret <frederic.pierret@qubes-os.org>

# REBUILDER
RUN apt-get update && apt-get install -y mmdebstrap in-toto python3-dateutil python3-rstr python3-setuptools && apt-get clean all
# Use python-debian code in python-debian#40
RUN git clone -b extend-buildinfo https://salsa.debian.org/fepitre/python-debian /opt/python-debian
RUN cd /opt/python-debian && python3 setup.py build install
RUN git clone https://github.com/fepitre/debrebuild /opt/debrebuild && \
    cd /opt/debrebuild && git checkout devel180221
