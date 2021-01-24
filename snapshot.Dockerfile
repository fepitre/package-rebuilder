FROM rebuilder_base:latest
MAINTAINER Frédéric Pierret <frederic.pierret@qubes-os.org>

RUN apt-get update && \
    apt-get install -y python3-flask python3-flask-caching && \
    apt-get clean all

# TODO: It lacks signature/checksum verification!
RUN git clone https://github.com/fepitre/qubes-snapshot /app

EXPOSE 5000

ENV FLASK_DEBUG 1
ENV FLASK_APP snapshot.py

CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]
