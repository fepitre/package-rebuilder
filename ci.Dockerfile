FROM rebuilder_base:latest
MAINTAINER Frédéric Pierret <frederic.pierret@qubes-os.org>

RUN apt-get update && apt-get install -y in-toto python3-dateutil python3-rstr python3-setuptools \
    python3-httpx python3-tenacity debian-keyring debian-archive-keyring debian-ports-archive-keyring \
    python3-requests-mock python3-pytest python3-pytest-mock python3-pytest-cov  && apt-get clean all

RUN git clone https://salsa.debian.org/python-debian-team/python-debian /opt/python-debian
RUN cd /opt/python-debian && git checkout e28d7a5729b187cfd0ec95da25030224cd10021a && python3 setup.py build install
