FROM python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
RUN nice -n 10 apt-get update && nice -n 10 apt-get upgrade -y \
    && nice -n 10 apt-get install -y --no-install-recommends adb ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
# Runtime does not install Python packages. Keep the qualified wheel but remove
# the package installer from the deployed environment.
RUN --network=none python3 -m pip uninstall -y pip
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/root
ENTRYPOINT ["adb"]
CMD ["-L", "tcp:5037", "nodaemon", "server"]
HEALTHCHECK NONE
