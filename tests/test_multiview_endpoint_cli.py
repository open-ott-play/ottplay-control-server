"""Real CLI/Client HTTP over loopback; synthetic controller/native, never a TV.

Run: python3 -B tests/test_multiview_endpoint_cli.py -v
This qualifies subprocess dispatch and receipt handling, not Go-controller or
native/backend execution. No snapshot claims playback; mutations say requested.
"""
import copy
from collections import Counter
from contextlib import contextmanager
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
CONTRACT = json.loads((ROOT / "contracts" / "multiview-v1.json").read_text(encoding="utf-8"))
DEVICE = "synthetic-lg"
AUTH = "synthetic-loopback-authentication-0001"
PRIVATE_ERROR = "synthetic-private-native-error-do-not-print"
CASES = (
    ("status_bound", ("status",)),
    ("sources", ("sources", "1")),
    ("sources_filtered", ("sources", "1", "--provider", "m3u", "--profile", "profile-home",
                          "--search", "News", "--cursor", "cursor_1")),
    ("layout", ("layout", "grid4")),
    ("layout_perimeter", ("layout", "perimeter")),
    ("play", ("window", "1", "play", "source_ticket_1")),
    ("focus", ("focus", "1")),
    ("audio", ("audio", "1")),
    ("audio_follow", ("audio", "follows-focus")),
    ("audio_muted", ("audio", "off")),
    ("stop", ("window", "1", "stop")),
    ("off", ("off",)),
    ("off_restore", ("off", "restore_previous")),
)


def canonical(value):
    """Exact decoded fixture comparison: key order immaterial, types preserved."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def idle_status(zero=False):
    data = copy.deepcopy(CONTRACT["empty_status"] if zero else CONTRACT["results"]["status_bound"])
    for row in data["snapshot"]["slots"]:
        row.update(state="empty", source=None, error=None)
    return data


def requested_result(name):
    data = copy.deepcopy(CONTRACT["results"][name])
    if data["op"] == "status":
        return idle_status()
    if data["op"] == "sources":
        for row in data["items"]:
            row["label"] = "Synthetic source choice"
    else:
        data["outcome"] = "requested"
    return data


class SyntheticNative:
    """Replaceable seam: {action, params} -> {status, data}; no external IO.

    Expected requests come from the canonical contract, never CLI validators.
    This scripts receipts and does not simulate allocation or playback effects.
    """
    def __init__(self, steps):
        self.steps = copy.deepcopy(steps)
        self.calls = []

    def dispatch(self, request):
        index = len(self.calls)
        self.calls.append(copy.deepcopy(request))
        if index >= len(self.steps):
            raise AssertionError("unexpected native redispatch")
        expected, response = self.steps[index]
        if canonical(request) != canonical(expected):
            raise AssertionError(f"native request {index} differs from canonical fixture")
        data = response["data"]
        if response["status"] == "ok" and "snapshot" in data:
            assert all(row["state"] in ("empty", "stopped") and row["source"] is None
                       for row in data["snapshot"]["slots"])
        if response["status"] == "ok" and data.get("op") not in (None, "status", "sources"):
            assert data["outcome"] == "requested"
        return copy.deepcopy(response)


class LoopbackController:
    """HTTP enqueue/receipt shim, not an implementation of the Go controller."""
    def __init__(self, native, *, pending_operation=None):
        self.native = native
        self.pending_operation = pending_operation
        self.posts, self.gets, self.errors = [], [], []
        self.receipts, self.get_counts = {}, Counter()
        controller = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_args):
                pass

            def reply(self, status, data):
                encoded = canonical(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(encoded)

            def handle_request(self, submit):
                try:
                    assert self.headers.get("Authorization") == "Bearer " + AUTH
                    target = urlsplit(self.path)
                    assert target.path == "/api/requests" and not target.fragment
                    query = parse_qs(target.query, keep_blank_values=True)
                    assert query.get("device_id") == [DEVICE]
                    assert all(len(values) == 1 for values in query.values())
                    if submit:
                        assert set(query) == {"device_id"}
                        length = int(self.headers["Content-Length"])
                        assert 0 < length <= CONTRACT["limits"]["request_envelope_bytes"]
                        original = json.loads(self.rfile.read(length).decode("utf-8"))
                        assert set(original) == {"action", "params"}
                        projected = copy.deepcopy(original)
                        if original["action"] == "capabilities":
                            assert canonical(original) == canonical(CONTRACT["probe"])
                            projected["params"] = {}
                        else:
                            assert original["action"] == CONTRACT["action"]
                        identity = f"{len(controller.posts) + 1:032x}"
                        controller.posts.append((identity, copy.deepcopy(original)))
                        result = controller.native.dispatch(projected)
                        controller.receipts[identity] = {"id": identity, **result}
                        self.reply(202, {"status": "queued", "id": identity, "receipt_wait_ms": "1000"})
                    else:
                        assert {"device_id", "id"} <= set(query) <= {"device_id", "id", "wait_ms"}
                        if "wait_ms" in query:
                            assert 1 <= int(query["wait_ms"][0]) <= 1000
                        identity = query["id"][0]
                        assert identity in controller.receipts
                        controller.gets.append(identity)
                        controller.get_counts[identity] += 1
                        receipt = controller.receipts[identity]
                        if (controller.pending_operation is not None
                                and receipt["data"].get("op") == controller.pending_operation
                                and controller.get_counts[identity] == 1):
                            self.reply(202, {"status": "pending"})
                        else:
                            self.reply(200, receipt)
                except (AssertionError, KeyError, TypeError, ValueError, UnicodeError) as error:
                    controller.errors.append(str(error) or type(error).__name__)
                    self.reply(400, {"error": "Synthetic endpoint request mismatch"})

            def do_POST(self):
                self.handle_request(True)

            def do_GET(self):
                self.handle_request(False)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01),
                                       name="ott-multiview-loopback")

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise AssertionError("loopback server was not reaped")


@contextmanager
def endpoint(steps, *, pending_operation=None):
    native = SyntheticNative(steps)
    controller = LoopbackController(native, pending_operation=pending_operation)
    try:
        controller.thread.start()
    except BaseException:
        controller.server.server_close()
        raise
    try:
        with tempfile.TemporaryDirectory(prefix="ott-multiview-endpoint-") as temporary:
            directory = Path(temporary)
            credential_path, config_path = directory / "credentials.json", directory / "cli.json"
            credentials = {"admin_token": AUTH, "devices": [{"id": DEVICE}]}
            config = {"server": controller.url, "server_config": str(credential_path),
                      "players": {"fixture": DEVICE}}
            for path, value in ((credential_path, credentials), (config_path, config)):
                path.write_text(canonical(value), encoding="utf-8")
                path.chmod(0o600)
            yield controller, config_path
    finally:
        controller.close()


def scenario(name, *, zero=False, terminal_status="ok", malformed=False, request_override=None):
    caps = {"version": 1, "player": {"version": "fixture", "platform": "webos-native",
                                    "runtime": CONTRACT["results"]["status_bound"]["runtime"]},
            "lifecycle": [], "input": [], "playback": [],
            **copy.deepcopy(CONTRACT["zero_capacity_capability" if zero else "capability"])}
    steps = [({"action": "capabilities", "params": {}}, {"status": "ok", "data": caps})]
    if zero and name != "status_bound":
        return steps
    steps.append((copy.deepcopy(CONTRACT["requests"]["status_bound"]),
                  {"status": "ok", "data": idle_status(zero)}))
    if name == "status_bound":
        return steps
    request = copy.deepcopy(request_override or CONTRACT["requests"][name])
    data = requested_result(name) if terminal_status == "ok" else copy.deepcopy(CONTRACT["rejections"][name])
    if request_override is not None:
        data["binding"] = {key: value for key, value in request["params"].items()
                           if key not in ("version", "runtime", "op")}
    if terminal_status == "unsupported":
        data["reason"] = "unsupported"
    if malformed:
        data = {"error": PRIVATE_ERROR}
    steps.append((request, {"status": terminal_status, "data": data}))
    return steps


class MultiviewEndpointCliTest(unittest.TestCase):
    def run_cli(self, words, steps, *, json_output=True, pending_operation=None, success=True):
        with endpoint(steps, pending_operation=pending_operation) as (controller, config_path):
            command = [sys.executable, "-I", "-X", "utf8", "-B", str(ROOT / "cli" / "ott.py"),
                       "-c", str(config_path), "-t", "4"]
            command += (["-j"] if json_output else []) + ["fixture", "mv", *words]
            env = {key: value for key, value in os.environ.items()
                   if key.casefold() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
            env.update(NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
            process = subprocess.Popen(command, cwd=config_path.parent, env=env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="strict")
            try:
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
                    self.fail("synthetic CLI subprocess exceeded deadline")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            self.assertEqual(controller.errors, [], "fake endpoint assertion failed")
            self.assertEqual(process.returncode, 0 if success else 1, stderr)
            self.assertEqual(len(controller.native.calls), len(steps), stderr)
            self.assertEqual(len(controller.posts), len(steps), "an operation was enqueued again")
            self.assertEqual(len(set(identity for identity, _ in controller.posts)), len(steps))
            self.assertEqual(set(controller.gets), {identity for identity, _ in controller.posts})
            self.assertEqual(controller.posts[0][1], CONTRACT["probe"])
            self.assertEqual(controller.native.calls[0], {"action": "capabilities", "params": {}})
            for index, ((identity, body), (expected, _response)) in enumerate(zip(controller.posts, steps)):
                if index:
                    self.assertEqual(canonical(body), canonical(expected))
                expected_reads = 2 if body["params"].get("op") == pending_operation and pending_operation else 1
                self.assertEqual(controller.get_counts[identity], expected_reads)
            self.assertNotIn(AUTH, stdout + stderr)
            self.assertNotIn(PRIVATE_ERROR, stdout + stderr)
            if not success:
                self.assertEqual(stdout, "", "failure was printed as success")
            return stdout, stderr, controller

    def test_all_operations_and_variants_cross_real_cli_http(self):
        for name, words in CASES:
            with self.subTest(name=name):
                steps = scenario(name)
                stdout, stderr, _ = self.run_cli(words, steps)
                self.assertEqual(stderr, "")
                self.assertEqual(json.loads(stdout), steps[-1][1]["data"])

    def test_human_window_four_uses_wire_slot_three_and_its_binding(self):
        request = copy.deepcopy(CONTRACT["requests"]["focus"])
        row = CONTRACT["results"]["status_bound"]["snapshot"]["slots"][3]
        request["params"].update({key: row[key] for key in CONTRACT["slot_binding_fields"]})
        stdout, _, _ = self.run_cli(("focus", "4"), scenario("focus", request_override=request))
        self.assertEqual(json.loads(stdout)["binding"]["slot_id"], 3)

    def test_unicode_search_survives_subprocess_and_http_without_global_switch(self):
        request = copy.deepcopy(CONTRACT["requests"]["sources_filtered"])
        request["params"]["query"] = "Три кота"
        words = ("sources", "1", "--provider", "m3u", "--profile", "profile-home",
                 "--search", "Три кота", "--cursor", "cursor_1")
        stdout, _, controller = self.run_cli(words, scenario("sources_filtered", request_override=request))
        self.assertEqual(json.loads(stdout)["binding"]["query"], "Три кота")
        self.assertEqual([request["action"] for _, request in controller.posts],
                         ["capabilities", "multiview", "multiview"])

    def test_capability_version_types_stay_distinct_at_raw_http_boundary(self):
        for field in ("protocol", "marker", "player"):
            with self.subTest(field=field):
                steps = scenario("status_bound")[:1]
                data = steps[0][1]["data"]
                if field == "protocol":
                    data["version"] = "1"
                elif field == "marker":
                    data["multiview"]["version"] = "1"
                else:
                    data["player"]["version"] = 1
                _, stderr, controller = self.run_cli(("status",), steps, success=False)
                self.assertTrue(stderr)
                self.assertEqual(len(controller.posts), 1)

    def test_zero_capacity_allows_status_and_stops_other_ops_before_dispatch(self):
        stdout, _, _ = self.run_cli(("status",), scenario("status_bound", zero=True))
        self.assertEqual(json.loads(stdout), CONTRACT["empty_status"])
        for name, words in CASES:
            if name == "status_bound":
                continue
            with self.subTest(name=name):
                _, stderr, controller = self.run_cli(words, scenario(name, zero=True), success=False)
                self.assertIn("not advertised", stderr)
                self.assertEqual(len(controller.posts), 1)

    def test_typed_negative_mutations_are_terminal_without_reenqueue(self):
        for name, words in CASES:
            if name not in ("layout", "play", "focus", "audio", "stop", "off"):
                continue
            for status in ("rejected", "unsupported"):
                with self.subTest(name=name, status=status):
                    _, stderr, _ = self.run_cli(words, scenario(name, terminal_status=status), success=False)
                    self.assertIn("Multi-view request was not accepted", stderr)
                    self.assertIn("No request was repeated", stderr)

    def test_malformed_native_negative_cannot_become_success_or_leak_error(self):
        for status in ("rejected", "unsupported"):
            with self.subTest(status=status):
                _, stderr, _ = self.run_cli(("focus", "1"),
                                           scenario("focus", terminal_status=status, malformed=True), success=False)
                self.assertIn("invalid request receipt", stderr)

    def test_pending_receipt_repeats_get_only(self):
        stdout, _, controller = self.run_cli(("focus", "1"), scenario("focus"), pending_operation="focus")
        self.assertEqual(json.loads(stdout)["outcome"], "requested")
        self.assertEqual(len(controller.posts), 3)
        self.assertEqual(len(controller.gets), 4)

    def test_text_mutation_receipt_disclaims_visible_playback(self):
        stdout, stderr, _ = self.run_cli(("focus", "1"), scenario("focus"), json_output=False)
        self.assertEqual(stderr, "")
        self.assertIn("focus: requested", stdout)
        self.assertIn("Visible playback has not been confirmed", stdout)
        self.assertNotIn("applied", stdout)


if __name__ == "__main__":
    unittest.main()
