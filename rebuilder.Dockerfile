FROM rebuilder_base:latest
MAINTAINER Frédéric Pierret <frederic.pierret@qubes-os.org>

# REBUILDER
RUN apt-get update && apt-get install -y mmdebstrap in-toto python3-dateutil && apt-get clean all
RUN git clone https://github.com/fepitre/debrebuild /opt/debrebuild && \
    cd /opt/debrebuild && git checkout d96829b9e97771d0f5755e59f867f21baf599878
