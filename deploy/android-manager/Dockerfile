FROM python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
RUN nice -n 10 apt-get update && nice -n 10 apt-get upgrade -y \
    && nice -n 10 apt-get install -y --no-install-recommends adb ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
WORKDIR /app
COPY manager.py recovery.py /app/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp/adb-home
ENTRYPOINT ["python3", "/app/manager.py"]
HEALTHCHECK --interval=30s --timeout=20s --start-period=60s --retries=3 CMD ["python3", "/app/manager.py", "--config", "/config.json", "health"]
