"""Exercise group fan-out through the real CLI and a loopback controller."""

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "ott.py"
BASE = json.loads((ROOT / "contracts" / "local-media-v1.json").read_text())
SESSION = json.loads((ROOT / "contracts" / "local-media-session-v1.json").read_text())
DEVICES = tuple(f"dev_t{number}" for number in range(1, 5))


class LoopbackController:
    """Hold T1 until peers act; a serial dispatcher cannot satisfy the barrier."""

    def __init__(self, blocked_action, *, wait_for_stdout=False, rejected_device=None):
        self.blocked_action = blocked_action
        self.wait_for_stdout = wait_for_stdout
        self.rejected_device = rejected_device
        self.lock = threading.Lock()
        self.requests = {}
        self.posts = []
        self.delivered = []
        self.errors = []
        self.barrier_results = []
        self.peer_mutations = threading.Event()
        self.peer_results = threading.Event()
        self.fast_stdout = threading.Event()
        self.first_response = threading.Event()
        self.output_before_first_response = False
        self.token = "fixture-admin-token-not-for-output"
        controller = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, body):
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                self.wfile.flush()

            def handle_request(self, method):
                try:
                    controller.handle(self, method)
                except (AssertionError, KeyError, ValueError, OSError) as error:
                    with controller.lock:
                        controller.errors.append(type(error).__name__ + ": " + str(error))
                    try:
                        self.reply(500, {"error": "fixture_request_failed"})
                    except OSError:
                        pass

            def do_POST(self):
                self.handle_request("POST")

            def do_GET(self):
                self.handle_request("GET")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        for event in (self.peer_mutations, self.peer_results, self.fast_stdout):
            event.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def handle(self, handler, method):
        assert handler.headers.get("Authorization") == "Bearer " + self.token
        parsed = urlsplit(handler.path)
        assert parsed.path == "/api/requests", handler.path
        query = parse_qs(parsed.query)
        device = query["device_id"][0]
        assert device in DEVICES, device
        if method == "POST":
            size = int(handler.headers["Content-Length"])
            assert 0 < size < 65536
            payload = json.loads(handler.rfile.read(size))
            assert payload["action"] in ("capabilities", "local_media")
            with self.lock:
                request_id = f"{len(self.requests) + 1:032x}"
                self.requests[request_id] = (device, payload)
                self.posts.append((device, payload["action"], payload["params"]))
                peers = {target for target, action, _ in self.posts
                         if target != DEVICES[0] and action == "local_media"}
                if peers == set(DEVICES[1:]):
                    self.peer_mutations.set()
            handler.reply(202, {"id": request_id, "receipt_wait_ms": "1000"})
            return

        request_id = query["id"][0]
        with self.lock:
            target, payload = self.requests[request_id]
        assert target == device
        action = payload["action"]
        if device == DEVICES[0] and action == self.blocked_action:
            gate = (self.fast_stdout if self.wait_for_stdout else
                    self.peer_mutations if action == "capabilities" else self.peer_results)
            # This bounds a failing serial implementation; passing does not
            # depend on a wall-clock speed comparison or scheduling order.
            completed = gate.wait(timeout=5)
            with self.lock:
                self.barrier_results.append(completed)
            self.first_response.set()

        runtime = "page-" + device.replace("_", "-")
        if action == "capabilities":
            data = {"version": 1, "player": {"version": "fixture", "platform": "tauri", "runtime": runtime},
                    "lifecycle": [], "input": [], "playback": [],
                    "local_media": copy.deepcopy(BASE["capability"]),
                    "local_media_session": copy.deepcopy(SESSION["capability"]),
                    "local_media_progressive": {"version": 1}}
        elif device == self.rejected_device:
            data = dict(SESSION["error_result"], runtime=runtime, error="source_unavailable")
        else:
            data = copy.deepcopy(SESSION["progressive_shuffle_result"])
            data.update(runtime=runtime, count=1)
            data["source"]["id"] = "source-" + device
        status = "rejected" if action == "local_media" and device == self.rejected_device else "ok"
        handler.reply(200, {"id": request_id, "status": status, "data": data})
        if action == "local_media":
            with self.lock:
                self.delivered.append(device)
                if set(DEVICES[1:]).issubset(self.delivered):
                    self.peer_results.set()


class ParallelPlayerGroupsCLITest(unittest.TestCase):
    def run_cli(self, controller, selector, *, json_output, source):
        with tempfile.TemporaryDirectory(prefix="ott-group-parallel-") as temporary:
            directory = Path(temporary)
            credentials = directory / "credentials.json"
            credentials.write_text(json.dumps({"admin_token": controller.token, "devices": []}))
            config = directory / "cli.json"
            config.write_text(json.dumps({"server": controller.url, "server_config": str(credentials),
                                          "players": {f"t{i}": device for i, device in enumerate(DEVICES, 1)},
                                          "groups": {"tt": [f"t{i}" for i in range(1, 5)]}}))
            # Ignore proxy and interpreter instrumentation inherited from the
            # developer shell; only this disposable loopback service is used.
            environment = {key: value for key, value in os.environ.items()
                           if not key.lower().endswith("_proxy")
                           and key not in ("PYTHONPATH", "PYTHONSTARTUP", "OTT_CONFIG")}
            environment.update(NO_PROXY="127.0.0.1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            words = [sys.executable, str(CLI), "-c", str(config), "-t", "12"]
            if json_output:
                words += ["--json", "--receipt"]
            words += [selector, "local", "shuffle", source]
            process = subprocess.Popen(words, cwd=temporary, env=environment, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8")
            stdout, stderr = [], []

            def read_stdout():
                for line in process.stdout:
                    stdout.append(line)
                    if line.startswith("t2: Local source:"):
                        controller.output_before_first_response = not controller.first_response.is_set()
                        controller.fast_stdout.set()

            def read_stderr():
                stderr.extend(process.stderr)

            readers = [threading.Thread(target=read_stdout, daemon=True),
                       threading.Thread(target=read_stderr, daemon=True)]
            for reader in readers:
                reader.start()
            try:
                process.wait(timeout=25)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for reader in readers:
                    reader.join(timeout=5)
                process.stdout.close()
                process.stderr.close()
            self.assertTrue(all(not reader.is_alive() for reader in readers), "CLI output readers did not finish")
            result = process.returncode, "".join(stdout), "".join(stderr)
            self.assertNotIn(controller.token, result[1] + result[2])
            self.assertEqual(controller.errors, [], "Unexpected loopback request")
            return result

    def assert_wire_requests(self, controller, source):
        for device in DEVICES:
            calls = [(action, params) for target, action, params in controller.posts if target == device]
            self.assertEqual(calls, [
                ("capabilities", SESSION["probe_request"]["params"]),
                ("local_media", {"op": "shuffle", "source": source, "runtime": "page-" + device.replace("_", "-"),
                                 "session": True, "progressive": True}),
            ], "Each player must receive exactly one probe and one mutation, in that order")

    def assert_receipts(self, controller, stderr):
        receipts = [json.loads(line) for line in stderr.splitlines()]
        self.assertEqual(len(receipts), 8)
        for receipt in receipts:
            device, payload = controller.requests[receipt["request_id"]]
            self.assertEqual(receipt["player"], device.removeprefix("dev_"))
            self.assertEqual(receipt["action"], payload["action"])

    def test_named_group_does_not_wait_for_first_capability_before_sending_to_peers(self):
        source = "/virtual/Movies with spaces/"
        with LoopbackController("capabilities") as controller:
            code, stdout, stderr = self.run_cli(controller, "tt", json_output=True, source=source)
            self.assertEqual(controller.barrier_results, [True], "Peers were blocked behind T1 capability discovery")
            self.assertEqual(code, 0, stdout + stderr)
            self.assert_wire_requests(controller, source)
            self.assert_receipts(controller, stderr)
            result = json.loads(stdout)
            self.assertEqual((result["succeeded"], result["failed"]), (4, 0))
            self.assertEqual([row["player"] for row in result["results"]], [f"t{i}" for i in range(1, 5)])

    def test_comma_group_dispatches_all_mutations_and_keeps_ordered_mixed_results(self):
        source = "/virtual/Movies with spaces/"
        with LoopbackController("local_media", rejected_device="dev_t2") as controller:
            code, stdout, stderr = self.run_cli(controller, "t1,t2,t3,t4", json_output=True, source=source)
            self.assertEqual(controller.barrier_results, [True], "Peers were blocked behind T1 mutation receipt")
            self.assertEqual(code, 1, stdout + stderr)
            self.assert_wire_requests(controller, source)
            self.assert_receipts(controller, stderr)
            result = json.loads(stdout)
            self.assertEqual((result["succeeded"], result["failed"]), (3, 1))
            self.assertEqual([row["player"] for row in result["results"]], [f"t{i}" for i in range(1, 5)])
            self.assertEqual([row["ok"] for row in result["results"]], [True, False, True, True])
            self.assertIn("not repeated", result["results"][1]["error"])
            self.assertEqual(controller.delivered[-1], DEVICES[0])

    def test_text_reports_ready_peer_while_first_player_is_still_waiting(self):
        with LoopbackController("local_media", wait_for_stdout=True) as controller:
            code, stdout, stderr = self.run_cli(controller, "tt", json_output=False, source="Movies")
            self.assertEqual(controller.barrier_results, [True], "T2 output was held until T1 finished")
            self.assertTrue(controller.output_before_first_response)
            self.assertEqual((code, stderr), (0, ""))
            self.assert_wire_requests(controller, "Movies")
            self.assertLess(stdout.index("t2: Local source:"), stdout.index("t1: Local source:"))
            for number in range(1, 5):
                self.assertEqual(stdout.count(f"t{number}: Local source: Movies; 1 item discovered so far; scanning continues."), 1)
                self.assertEqual(stdout.count(f"t{number}: Playback request accepted; playback has not been confirmed."), 1)


if __name__ == "__main__":
    unittest.main()
