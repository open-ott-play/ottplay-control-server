FROM debian:bookworm-slim@sha256:a4672c0cb26fbdde88e38fa2dfb6c681942306680e41e4378b28770b6e79ee91
RUN nice -n 10 apt-get update && nice -n 10 apt-get install -y --no-install-recommends adb python3 python3-websocket ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY manager.py recovery.py /app/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp/adb-home
ENTRYPOINT ["python3", "/app/manager.py"]
HEALTHCHECK --interval=30s --timeout=20s --start-period=60s --retries=3 CMD ["python3", "/app/manager.py", "--config", "/config.json", "health"]
