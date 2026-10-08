import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("registered_checks", ROOT / "scripts" / "check_registered_players.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
PRIVATE = "https://provider.example/private-credential/stream.m3u8"


def catalogue(empty=False, archive=True):
    return {"catalog": "private-receipt", "archive": {"version": 1, "revision": "private-revision"} if archive else None,
            "channels": [] if empty else [{"id": "fixture-channel", "number": 1, "name": "Fixture Channel",
                                           "tvgId": "fixture-guide", "tvgName": "", "shift": 0, "archiveHours": 144}]}


class FakeClient:
    def __init__(self, devices=("first",), aliases=None):
        self.config = {"server": "https://control.example", "epg": {"source": "epg-one", "url": "https://epg.example/epg/v1"},
                       "players": {} if aliases is None else aliases}
        self.timeout = 45
        self.rows = [{"id": device, "pending": 0, "last_seen": None} for device in devices]
        self.calls = []
        self.errors = {}
        self.empty = set()

    def api(self, path, payload=None, timeout=None):
        if path != "/api/devices" or payload is not None:
            raise AssertionError("Unexpected API mutation")
        return 200, {"devices": self.rows}

    def call(self, device, action, params):
        self.calls.append((device, action, params, self.timeout))
        if (device, action) in self.errors:
            raise self.errors[(device, action)]
        if action == "epg_catalog":
            return catalogue(device in self.empty)
        if action == "channels":
            return {"channels": [{key: row[key] for key in ("id", "number", "name")}
                                 for row in catalogue(device in self.empty)["channels"]]}
        if action == "resolve_archive":
            return {"resolved": True, "url": PRIVATE}
        raise AssertionError("Unexpected player mutation")


def current(client, device, settings, query):
    if query != "":
        raise AssertionError("Current-only acceptance must not launch archive fallback")
    snapshot = client.call(device, "epg_catalog", {})
    count = len(snapshot["channels"])
    return {"checked": count, "total": count, "partial": False, "programs": []}, {}


class RegisteredPlayersTest(unittest.TestCase):
    def test_inventory_includes_unique_unaliased_devices_and_every_alias(self):
        client = FakeClient(("first", "first", "second", "unaliased"),
                            {"living room": "first", "First\x1b": "first", "Детская": "second", "stale": "removed"})
        with patch.object(checks.ott, "server_programs", side_effect=current):
            report, status = checks.run_checks(client, client.config, workers=3, probe_timeout=7)
        self.assertEqual(status, 3)
        self.assertEqual((report["registered"], report["aliases"], report["unaliased"]), (3, 3, 1))
        self.assertEqual(report["stale_aliases"], [{"alias": "stale", "id": "removed"}])
        self.assertEqual([row["id"] for row in report["players"]], ["first", "second", "unaliased"])
        self.assertEqual(report["players"][0]["aliases"], ["First", "living room"])
        self.assertEqual(sum(call[0] == "first" and call[1] == "channels" for call in client.calls), 1)
        self.assertEqual([call[3] for call in client.calls if call[1] == "channels"], [7, 7, 7])
        self.assertEqual(sum(call[1] == "epg_catalog" and call[3] == 45 for call in client.calls), 3)
        self.assertEqual(client.timeout, 45)
        self.assertEqual(report["summary"]["passed"], 3)
        self.assertNotIn("private", json.dumps(report))

    def test_unsupported_unresponsive_and_empty_are_distinct_without_status_gate(self):
        client = FakeClient(("working", "legacy", "sleepy", "empty"))
        client.errors[("legacy", "epg_catalog")] = checks.ott.PlayerUnsupported(PRIVATE)
        client.errors[("sleepy", "epg_catalog")] = checks.ott.Error("The player did not respond. " + PRIVATE)
        client.empty.add("empty")
        with patch.object(checks.ott, "server_programs", side_effect=current):
            report, status = checks.run_checks(client, client.config)
        by_id = {row["id"]: row for row in report["players"]}
        self.assertEqual(status, 3)
        self.assertEqual(by_id["legacy"]["catalogue"]["status"], "unsupported")
        self.assertEqual(by_id["legacy"]["channels"]["status"], "ok")
        self.assertEqual(by_id["sleepy"]["catalogue"]["status"], "unresponsive")
        self.assertEqual(by_id["sleepy"]["channels"]["status"], "not_checked")
        self.assertEqual(by_id["empty"]["catalogue"]["status"], "empty_catalog")
        self.assertEqual(by_id["working"]["current"]["status"], "ok")
        self.assertTrue(all(call[1] in checks.READ_ACTIONS for call in client.calls))
        self.assertNotIn("private-credential", json.dumps(report))
        self.assertNotIn("offline", json.dumps(report))

    def test_read_only_boundary_rejects_every_mutating_command_and_cross_device_call(self):
        client = FakeClient()
        readonly = checks.ReadOnlyClient(client, "first")
        for action in ("play", "play_catalog", "play_archive_catalog", "restart", "profile", "provider_settings", "lifecycle"):
            with self.subTest(action=action), self.assertRaises(checks.ReadOnlyViolation):
                readonly.call("first", action, {})
        with self.assertRaises(checks.ReadOnlyViolation):
            readonly.call("second", "epg_catalog", {})
        with self.assertRaises(checks.ReadOnlyViolation):
            readonly.api("/api/devices", {})
        self.assertEqual(client.calls, [])

    def test_archive_coverage_distinguishes_cached_verified_live_and_missing_results(self):
        for mode, resolve, expected in (("archive", True, "ok"), ("archive", False, "ok"),
                                        ("live", False, "live_match"), (None, False, "no_matches")):
            client = FakeClient()

            def search(readonly, device, settings, snapshot, query, refresh, catalog_expired):
                self.assertEqual(query, "fixture archive")
                self.assertFalse(refresh)
                if resolve:
                    readonly.call(device, "resolve_archive", {"catalog": snapshot["catalog"]})
                return {"programs": [] if mode is None else [{"mode": mode}]}, {}

            with self.subTest(mode=mode, resolve=resolve), \
                    patch.object(checks.ott, "server_programs", side_effect=current), \
                    patch.object(checks.history, "search_archives", side_effect=search):
                report, status = checks.run_checks(client, client.config, archive_query="fixture archive")
            archive = report["players"][0]["archive"]
            self.assertEqual(archive["status"], expected)
            self.assertEqual(status, 0 if mode == "archive" else 3)
            self.assertEqual(archive["archive_resolution_requests"], int(resolve))
            self.assertEqual(archive["cached_availability_only"], mode == "archive" and not resolve)
            self.assertNotIn("private", json.dumps(report))

    def test_shared_helper_cannot_escape_read_only_scope_and_partial_epg_fails(self):
        client = FakeClient()
        for result in (lambda *_: ({"partial": True, "checked": 1, "total": 2, "programs": []}, {}),
                       lambda readonly, device, *_: readonly.call(device, "play", {})):
            with patch.object(checks.ott, "server_programs", side_effect=result):
                report, status = checks.run_checks(client, client.config)
            self.assertEqual(status, 1)
            self.assertIn(report["players"][0]["current"]["status"], ("invalid_response", "read_only_violation"))
        self.assertTrue(all(call[1] != "play" for call in client.calls))

    def test_empty_registry_and_missing_epg_are_incomplete_not_success(self):
        client = FakeClient(())
        report, status = checks.run_checks(client, client.config)
        self.assertEqual((status, report["registered"]), (3, 0))
        client = FakeClient()
        del client.config["epg"]
        report, status = checks.run_checks(client, client.config, archive_query="fixture")
        self.assertEqual(status, 3)
        self.assertEqual(report["players"][0]["current"]["status"], "epg_not_configured")

    def test_real_controller_and_epg_http_contract_without_playback(self):
        actions, public_payloads, receipts = [], [], {}

        class Server(BaseHTTPRequestHandler):
            def reply(self, data):
                body = json.dumps(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == "/control/api/devices":
                    self.reply({"devices": [{"id": "fixture-device", "pending": 0, "last_seen": None}]})
                else:
                    receipt = parse_qs(parsed.query)["id"][0]
                    self.reply({"id": receipt, "status": "ok", "data": receipts[receipt]})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/epg/v1/current":
                    public_payloads.append((payload, self.headers.get("Authorization")))
                    now = time.time()
                    self.reply({"version": 1, "source": "epg-one", "generation": "fixture-generation",
                                "fetchedAt": int(now * 1000), "asOf": now, "stale": False,
                                "checked": len(payload["channels"]), "total": len(payload["channels"]),
                                "programs": [{"id": "fixture-channel", "title": "Три кота", "start": now - 60, "end": now + 60}]})
                else:
                    action = payload["action"]
                    actions.append(action)
                    receipt = "%032x" % len(actions)
                    receipts[receipt] = catalogue() if action == "epg_catalog" else {
                        "channels": [{"id": "fixture-channel", "number": 1, "name": "Fixture Channel"}]}
                    self.reply({"id": receipt})

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                credentials, config, output = root / "credentials.json", root / "cli.json", root / "report.json"
                checks.ott.write_private(credentials, {"admin_token": "fixture-secret-token", "devices": []})
                base = f"http://127.0.0.1:{server.server_port}"
                checks.ott.write_private(config, {"server": base + "/control", "server_config": str(credentials),
                                                 "players": {"fixture alias": "fixture-device"},
                                                 "epg": {"url": base + "/epg/v1", "source": "epg-one"}})
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    status = checks.main(["--config", str(config), "--output", str(output),
                                          "--probe-timeout", "3", "--timeout", "5"])
                report = json.loads(stdout.getvalue())
                self.assertEqual(status, 0, report)
                self.assertEqual(report["players"][0]["current"]["checked"], 1)
                self.assertEqual(report["players"][0]["current"]["matches"], 1)
                self.assertEqual(report["players"][0]["archive"]["status"], "not_requested")
                self.assertEqual(actions, ["epg_catalog", "channels", "epg_catalog"])
                self.assertEqual(len(public_payloads), 1)
                self.assertIsNone(public_payloads[0][1])
                self.assertNotIn("catalog", json.dumps(public_payloads))
                self.assertNotIn("fixture-secret-token", stdout.getvalue())
                self.assertNotIn("http://", stdout.getvalue())
                self.assertEqual(json.loads(output.read_text()), report)
                if os.name == "posix":
                    self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
