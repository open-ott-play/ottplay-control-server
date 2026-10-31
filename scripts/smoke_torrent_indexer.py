#!/usr/bin/env python3
"""Exercise a real controller and CLI with a synthetic loopback player only."""
import argparse
from collections import Counter
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

RUNTIME = "indexer-smoke-runtime"
KEY = "private-indexer-smoke-key"
ENDPOINT = "http://127.0.0.1:9696/private-indexer-smoke/api"
REF, SOURCE, TIMEOUT, REJECT = (letter * 32 for letter in "abcd")


class SmokeError(Exception):
    pass


def check(condition, message):
    if not condition:
        raise SmokeError(message)


def private_free(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return KEY not in value and ENDPOINT not in value


class Fixture:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.requests, self.sources, self.errors = {}, {}, []
        self.advertise, self.runtime_checked = True, False
        self.thread = threading.Thread(target=self.poll, daemon=True)

    def call(self, path, token=None, payload=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=2) as response:
                return response.status, json.loads(response.read(2 * 1024 * 1024 + 1))
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            return code, None

    def counts(self):
        with self.lock:
            return Counter(r["params"]["op"] for r in self.requests.values() if r["action"] == "torrent_indexer")

    def poll(self):
        while not self.stop.is_set():
            try:
                code, envelope = self.call("/api/webhook/commands?delivery=ack", "b" * 32)
                check(code == 200, "player poll failed")
                for request in envelope["requests"]:
                    with self.lock:
                        if request["id"] in self.requests:
                            continue
                        self.requests[request["id"]] = request
                    self.respond(request)
            except Exception:
                self.errors.append("synthetic player failed")
                return
            self.stop.wait(1)

    def respond(self, request):
        params, status = request["params"], "ok"
        if request["action"] == "capabilities":
            data = {"version": 1, "player": {"runtime": RUNTIME, "platform": "tauri", "version": "1.1.62"},
                    "lifecycle": [], "input": [], "playback": []}
            if self.advertise:
                data["torrent_indexer"] = {"version": 1}
        else:
            check(request["action"] == "torrent_indexer", "unexpected action")
            check(params["runtime"] == RUNTIME and params["version"] == 1, "CLI runtime binding missing")
            op = params["op"]
            data = {"version": 1, "runtime": RUNTIME, "op": op, "schema": 1}
            if op == "list":
                data["sources"] = list(self.sources.values())
            elif op == "configure":
                check(params["apiKey"] == KEY and params["endpoint"] == ENDPOINT, "configuration did not reach fixture")
                source = {"id": params["sourceId"], "label": params["label"]}
                self.sources[source["id"]] = source
                data["source"] = source
            elif op == "remove":
                self.sources.pop(params["sourceId"], None)
                data["removed"] = True
            elif op == "caps":
                data.update(sourceId=params["sourceId"], searchAvailable=True, maxLimit=100,
                            categories=[{"id": 2000, "name": "Movies", "parentId": None}])
                if not self.runtime_checked:
                    code, _ = self.call("/api/responses", "b" * 32,
                                        {"id": request["id"], "status": "ok", "data": {**data, "runtime": "different"}})
                    check(code == 400, "controller accepted wrong runtime")
                    self.runtime_checked = True
            elif op == "search":
                check(params["query"] == "Big Buck Bunny" and params["categoryIds"] == [2000]
                      and params["offset"] == 0 and params["limit"] == 2, "search parameters changed")
                data.update(sourceId=params["sourceId"], nextOffset=None, total=1,
                            items=[{"resultRef": REF, "title": "Big Buck Bunny", "size": 12345, "seeders": 5,
                                    "publishedAt": "2008-04-10T00:00:00Z", "categoryIds": [2000], "languages": ["en"]}])
            elif op == "prepare":
                if params["resultRef"] == TIMEOUT:
                    return  # Retained delivery must not produce another logical mutation.
                if params["resultRef"] == REJECT:
                    status = "rejected"
                    data = {"version": 1, "runtime": RUNTIME, "op": op,
                            "error": "Torrent indexer request failed.", "reason": "torznab_reference_missing"}
                else:
                    check(params["resultRef"] == REF, "unexpected reference")
                    data.update(sourceId=params["sourceId"], sourceRef=SOURCE)
            else:
                raise SmokeError("unexpected operation")
        code, _ = self.call("/api/responses", "b" * 32, {"id": request["id"], "status": status, "data": data})
        check(code == 200, "controller rejected fixture response")


def smoke(controller, cli):
    with tempfile.TemporaryDirectory(prefix="ott-indexer-smoke-") as directory:
        root = Path(directory)
        private = root / "private"
        private.mkdir(mode=0o700)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        fixture = Fixture("http://127.0.0.1:" + str(port))
        config = {"listen": "127.0.0.1:" + str(port), "admin_token": "a" * 32,
                  "devices": [{"id": "indexer_smoke", "token": "b" * 32}],
                  "journal": {"path": str(private / "operations.json"), "max_records": 128, "retention_seconds": 86400}}
        server_config, cli_config, key_file = root / "server.json", root / "cli.json", private / "key"
        server_config.write_text(json.dumps(config))
        cli_config.write_text(json.dumps({"server": fixture.base, "server_config": str(server_config),
                                          "players": {"one": "indexer_smoke"}}))
        key_file.write_text(KEY + "\n")
        for path in (server_config, cli_config, key_file):
            path.chmod(0o600)
        with (root / "controller.log").open("wb") as log:
            server = subprocess.Popen([str(controller), "serve", "--config", str(server_config)], stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 8
                while True:
                    check(server.poll() is None, "controller exited during startup")
                    try:
                        code, _ = fixture.call("/readyz")
                        if code == 200:
                            break
                    except (OSError, ValueError):
                        pass
                    check(time.monotonic() < deadline, "controller readiness timed out")
                    time.sleep(0.1)
                fixture.thread.start()
                cases = []

                def run(name, words, expected=0, timeout=15):
                    result = subprocess.run([sys.executable, "-B", str(cli), "-c", str(cli_config), "-t", str(timeout),
                                             "-j", "one", "torrent", "indexer", *words],
                                            capture_output=True, text=True, timeout=timeout + 5)
                    check(private_free(result.stdout + result.stderr), "CLI output contained private configuration")
                    check(not fixture.errors, "synthetic player failed")
                    check(result.returncode == expected, name + ": unexpected CLI exit status")
                    cases.append(name)
                    return json.loads(result.stdout)

                check(run("empty list", ["list"])["sources"] == [], "initial list not empty")
                run("configure", ["configure", "home", "--label", "Local fixture", "--endpoint", ENDPOINT,
                                  "--api-key-file", str(key_file)])
                check(len(run("configured list", ["list"])["sources"]) == 1, "source not listed")
                run("caps", ["caps", "home"])
                result = run("search", ["search", "home", "Big Buck Bunny", "--category", "2000", "--limit", "2"])
                check(result["items"][0]["resultRef"] == REF, "search handle changed")
                result = run("prepare", ["prepare", "home", REF])
                check(result["source"] == "ottplay-local-torrent:home/" + SOURCE, "prepared source not opaque")
                run("rejection", ["prepare", "home", REJECT], expected=1)
                before = fixture.counts()["prepare"]
                result = run("uncertain mutation", ["prepare", "home", TIMEOUT], expected=2, timeout=6)
                check(result["status"] == "unknown" and result["replay_allowed"] is False, "uncertain mutation misreported")
                check(fixture.counts()["prepare"] == before + 1, "uncertain mutation repeated")
                run("remove", ["remove", "home"])
                before = fixture.counts()
                fixture.advertise = False
                run("missing capability", ["remove", "home"], expected=1)
                check(fixture.counts() == before, "unadvertised action dispatched")
                fixture.advertise = True
                # Populate journal separately: CLI deliberately does not replay mutations.
                params = {"version": 1, "runtime": RUNTIME, "op": "configure", "sourceId": "journal",
                          "label": "Journal fixture", "endpoint": ENDPOINT, "apiKey": KEY}
                code, receipt = fixture.call("/api/requests?device_id=indexer_smoke", "a" * 32,
                                              {"action": "torrent_indexer", "params": params,
                                               "idempotency_key": "indexer-smoke-journal-0001"})
                check(code == 202, "journal request not accepted")
                deadline = time.monotonic() + 6
                while True:
                    code, data = fixture.call("/api/requests?device_id=indexer_smoke&id=" + receipt["id"], "a" * 32)
                    if code == 200:
                        check(private_free(json.dumps(data)), "receipt contained private configuration")
                        break
                    check(code == 202 and time.monotonic() < deadline, "journal receipt timed out")
                    time.sleep(0.5)
                journal = (private / "operations.json").read_bytes()
                check(bool(json.loads(journal)["records"]), "journal check had no persisted record")
                check(private_free(journal), "journal contained private configuration")
                check(fixture.runtime_checked and not fixture.errors, "runtime check failed")
                counts = fixture.counts()
                check(set(counts) == {"list", "configure", "remove", "caps", "search", "prepare"}, "operation missing")
                summary = {"status": "PASS", "cases": cases, "operations": dict(counts), "runtime_mismatch_rejected": True,
                           "uncertain_mutation_sent_once": True, "private_output_and_journal_clean": True,
                           "live_players_contacted": False}
            finally:
                fixture.stop.set()
                if fixture.thread.is_alive():
                    fixture.thread.join(timeout=3)
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
        check(private_free((root / "controller.log").read_bytes()), "controller logs contained private configuration")
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    args = parser.parse_args()
    try:
        check(args.controller.is_file() and args.cli.is_file(), "controller and CLI files required")
        print(json.dumps(smoke(args.controller.resolve(), args.cli.resolve()), sort_keys=True))
        return 0
    except Exception as error:
        # Network and subprocess exception text can include argv or private payloads.
        print("Indexer smoke failed: " + (str(error) if isinstance(error, SmokeError) else type(error).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
