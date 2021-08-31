# debian/bullseye; amd64
FROM debian:bullseye
MAINTAINER Frédéric Pierret <frederic.pierret@qubes-os.org>
RUN apt-get update && apt-get -y upgrade && \
    apt-get install -y git rsync celery python3-requests python3-celery \
        python3-packaging python3-mongoengine python3-pip python3-apt python3-debian \
        python3-matplotlib python3-numpy python3-redis python3-jinja2 && \
    apt-get clean all
RUN mkdir /app
WORKDIR /app
