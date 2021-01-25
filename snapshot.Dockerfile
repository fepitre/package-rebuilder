FROM rebuilder_base:latest
MAINTAINER Frédéric Pierret <frederic.pierret@qubes-os.org>

RUN apt-get update && \
    apt-get install -y python3-flask python3-flask-caching && \
    apt-get clean all

RUN git clone https://github.com/fepitre/qubes-snapshot /app && \
    cd /app && git checkout bfa0c7cfcbb3fca450b0c36b9bdf0978c864eee3

EXPOSE 5000

ENV FLASK_DEBUG 1
ENV FLASK_APP snapshot.py

CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]
