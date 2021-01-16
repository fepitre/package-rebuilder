FROM debian:bullseye
RUN apt-get update && apt-get -y upgrade && \
    apt-get install -y git rsync celery python3-requests python3-celery \
        python3-packaging python3-mongoengine python3-pip python3-apt python3-debian && \
    apt-get clean all
RUN mkdir /app
WORKDIR /app
