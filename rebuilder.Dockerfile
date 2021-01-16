FROM rebuilder_base:latest

# REBUILDER
RUN apt-get update && apt-get install -y mmdebstrap in-toto python3-dateutil && apt-get clean all

# TODO: It lacks signature/checksum verification!
# TODO: We need packages for debrebuild and gemato
RUN cd /opt && git clone https://github.com/fepitre/debrebuild
RUN pip3 install gemato