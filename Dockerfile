# debian/bullseye; amd64
FROM debian@sha256:3b19d4bb1d801f238bffedb4432021542217a25cf428b2cebe2ca49350e3c13d
MAINTAINER Frédéric Pierret <frederic.pierret@qubes-os.org>
RUN apt-get update && apt-get -y upgrade && \
    apt-get install -y git rsync celery python3-requests python3-celery \
        python3-packaging python3-mongoengine python3-pip python3-apt python3-debian && \
    apt-get clean all
RUN mkdir /app
WORKDIR /app
