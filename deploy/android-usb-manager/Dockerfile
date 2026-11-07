FROM python:3.15.2-slim-trixie@sha256:fd2aef5e0d8438208de9fe154ab5b331a2428fc0c9838d41611e611fd7a80d85
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
