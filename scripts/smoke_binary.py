#!/usr/bin/env python3
"""Exercise a built binary over real localhost HTTP without logging credentials."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

binary = str(Path(sys.argv[1]).resolve(strict=True))
subprocess.run([binary, "version"], check=True)
with tempfile.TemporaryDirectory() as temp:
    config_path = Path(temp) / "config.json"
    subprocess.run([binary, "init", "--config", str(config_path), "--device-id", "smoke"], check=True)
    config = json.loads(config_path.read_text())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([binary, "serve", "--config", str(config_path), "--listen", f"127.0.0.1:{port}"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    def request(path, token=None, payload=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise RuntimeError("Server exited during startup")
            try:
                request("/healthz")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        else:
            raise RuntimeError("Server did not become healthy")
        subprocess.run([binary, "healthcheck", "--url", base + "/readyz"], check=True)
        admin = config["admin_token"]
        token = config["devices"][0]["token"]
        request("/api/webhook/commands?device_id=smoke", admin, {"command": "set_volume", "volume": 35})
        commands = request("/api/webhook/commands?delivery=ack", token)["commands"]
        assert len(commands) == 1 and commands[0]["volume"] == 35
        assert request("/api/webhook/commands?delivery=ack", token)["commands"] == commands
        request("/api/webhook/commands/ack", token, {"ids": [commands[0]["id"]]})
        assert request("/api/webhook/commands?delivery=ack", token)["commands"] == []
        assert request("/api/devices", admin)["devices"][0]["pending"] == 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=12)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
print("Built binary command delivery and acknowledgement passed")
