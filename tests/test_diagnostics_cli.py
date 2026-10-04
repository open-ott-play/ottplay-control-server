import contextlib
import email.message
import io
import http.server
import json
import os
from pathlib import Path
import tempfile
import shutil
import ssl
import subprocess
import threading
import time
import unittest
from unittest import mock
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli"))
import diagnostics as diag

TOKEN = "synthetic_test_credential_" + "x" * 32
EPOCH = "test_epoch"


def valid_reply(kind, **values):
    data = {"diagnostics_protocol": 2, "server_epoch": EPOCH}
    shapes = {
        "runtimes": {"runtimes": []},
        "start": {"session_id": "session", "request_id": "request", "state": "start_pending",
                  "control_revision": 1, "idempotency_retention_ms": 900000},
        "stop": {"session_id": "session", "request_id": "request", "state": "stop_pending",
                 "control_revision": 2, "idempotency_retention_ms": 900000},
        "status": {"session_id": "session", "device_id": "device", "runtime_id": "runtime",
                   "state": "active", "device_stop_confirmed": False, "lease_remaining_ms": 1000,
                   "control_revision": 1, "dropped_total": 0},
        "events": {"events": [], "next_seq": 0, "truncated_before_seq": 1, "dropped_total": 0},
        "revoke": {"status": "revoked", "device_stop_confirmed": False},
        "repair": {"repair_id": "repair-one", "state": "pending", "idempotency_retention_ms": 900000},
        "repair_status": {"repair_id": "repair-one", "device_id": "device", "runtime_id": "runtime",
                          "action": "restart_stream", "state": "applied", "lease_remaining_ms": 1000},
    }
    return {**data, **shapes[kind], **values}


def route_reply(path, method):
    if method == "POST" and path.endswith("/repairs"):
        return valid_reply("repair")
    if method == "GET" and "/repairs/" in path:
        return valid_reply("repair_status")
    if method == "DELETE":
        return valid_reply("revoke")
    if method == "POST":
        return valid_reply("stop" if path.endswith("/stop") else "start")
    if "/runtimes?" in path:
        return valid_reply("runtimes")
    if "/events?" in path:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        return valid_reply("events", next_seq=int(query.get("after_seq", [0])[0]))
    return valid_reply("status")


class Response:
    def __init__(self, data=None, raw=None, status=200, content_type="application/json"):
        self.data = raw if raw is not None else json.dumps(data or {"diagnostics_protocol": 2, "server_epoch": EPOCH}).encode()
        self.status = status
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.data[:size]


class Opener:
    def __init__(self, response=None, error=None, delay=0):
        self.response = response
        self.error = error
        self.delay = delay
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.response or Response(route_reply(request.full_url, request.method),
                                         status=202 if request.method == "POST" else 200)


class DiagnosticsTests(unittest.TestCase):
    def client(self, opener=None, **kwargs):
        return diag.DiagnosticsClient("https://controller.example/ott-control", TOKEN, opener=opener or Opener(), **kwargs)

    def test_targeted_routes_and_body(self):
        opener = Opener()
        client = self.client(opener)
        client.runtimes("living-room")
        self.assertEqual(opener.requests[-1].full_url, "https://controller.example/ott-control/api/v2/diagnostics/runtimes?device_id=living-room")
        client.start("living-room", "runtime", "consent", 600000, "operation", EPOCH)
        request = opener.requests[-1]
        body = json.loads(request.data)
        self.assertEqual(body, {"device_id": "living-room", "runtime_id": "runtime", "consent_epoch": "consent",
                                "lease_ms": 600000, "idempotency_key": "operation", "server_epoch": EPOCH, "profile": "standard"})
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, request.full_url)
        self.assertNotIn(TOKEN, request.data.decode())
        client.events("session", 23, 16)
        self.assertTrue(opener.requests[-1].full_url.endswith("/sessions/session/events?after_seq=23&limit=16"))
        client.stop("session", "stop_key", EPOCH)
        self.assertEqual(json.loads(opener.requests[-1].data)["reason"], "operator")
        client.revoke("runtime", EPOCH)
        self.assertEqual(opener.requests[-1].method, "DELETE")
        self.assertEqual(opener.requests[-1].get_header("X-ott-diagnostics-epoch"), EPOCH)
        self.assertIsNone(opener.requests[-1].data)

    def test_exact_runtime_repair_routes_and_status(self):
        opener = Opener()
        client = self.client(opener)
        result = client.repair("device", "runtime", "consent", "restart_stream", 30000, "repair-key", EPOCH)
        request = opener.requests[-1]
        self.assertEqual(request.full_url, "https://controller.example/ott-control/api/v2/diagnostics/repairs")
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {
            "device_id": "device", "runtime_id": "runtime", "consent_epoch": "consent",
            "action": "restart_stream", "deadline_ms": 30000,
            "idempotency_key": "repair-key", "server_epoch": EPOCH,
        })
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, request.data.decode())
        self.assertEqual(result["state"], "pending")
        self.assertEqual(client.repair_status(result["repair_id"])["state"], "applied")
        self.assertEqual(opener.requests[-1].method, "GET")
        self.assertTrue(opener.requests[-1].full_url.endswith("/repairs/repair-one"))
        for state in ("pending", "accepted", "rejected", "unsupported", "expired", "revoked"):
            data = valid_reply("repair_status", action="reload_player", state=state)
            self.assertEqual(self.client(Opener(Response(data))).repair_status("repair-one"), data)

    def test_repair_validation_before_network_and_unknown_mutation_outcome(self):
        opener = Opener()
        client = self.client(opener)
        valid = {"device_id": "device", "runtime_id": "runtime", "consent_epoch": "consent",
                 "action": "restart_stream", "deadline_ms": 1000,
                 "idempotency_key": "repair-key", "server_epoch": EPOCH}
        invalid = [{"action": value} for value in ("eval", "restart_player", "", None, [], True)]
        invalid += [{"deadline_ms": value} for value in (999, 30001, True, 1000.0, "1000")]
        invalid += [{"device_id": "../other"}, {"runtime_id": ""}, {"consent_epoch": ""},
                    {"idempotency_key": ""}, {"server_epoch": "x\r\nheader"}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(diag.DiagnosticError):
                client.repair(**{**valid, **change})
        with self.assertRaises(diag.DiagnosticError):
            client.repair_status("../another")
        self.assertEqual(opener.requests, [])
        failed = Opener(error=OSError(TOKEN))
        with self.assertRaises(diag.DiagnosticError) as error:
            self.client(failed).repair(**valid)
        self.assertTrue(error.exception.unknown)
        self.assertEqual(len(failed.requests), 1)
        self.assertNotIn(TOKEN, json.dumps(error.exception.public()))

    def test_repair_strict_reply_contract_and_scope_failures(self):
        valid = {"device_id": "device", "runtime_id": "runtime", "consent_epoch": "consent",
                 "action": "restart_stream", "deadline_ms": 1000,
                 "idempotency_key": "repair-key", "server_epoch": EPOCH}
        receipt = valid_reply("repair")
        cases = [{key: value for key, value in receipt.items() if key != missing}
                 for missing in ("repair_id", "state", "idempotency_retention_ms")]
        cases += [{**receipt, "state": "applied"}, {**receipt, "raw": TOKEN},
                  {**receipt, "idempotency_retention_ms": True}]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(diag.DiagnosticError) as error:
                self.client(Opener(Response(data, status=202))).repair(**valid)
            self.assertTrue(error.exception.unknown)
            self.assertNotIn(TOKEN, json.dumps(error.exception.public()))
        states = [{"repair_id": "other"}, {"action": "eval"}, {"state": "recovered"},
                  {"state": "accepted", "action": "restart_stream"},
                  {"state": "applied", "action": "reload_player"},
                  {"lease_remaining_ms": 30001}, {"lease_remaining_ms": True},
                  {"extra": TOKEN}]
        for change in states:
            with self.subTest(change=change), self.assertRaises(diag.DiagnosticError) as error:
                self.client(Opener(Response(valid_reply("repair_status", **change)))).repair_status("repair-one")
            self.assertFalse(error.exception.unknown)
            self.assertNotIn(TOKEN, json.dumps(error.exception.public()))
        for status, code in ((403, "credential_role_denied"), (404, "not_found"),
                             (403, "capability_required"), (409, "repair_busy")):
            body = {"diagnostics_protocol": 2, "server_epoch": EPOCH, "error": {"code": code}}
            response = urllib.error.HTTPError("https://controller.example", status, TOKEN,
                                               {"Content-Type": "application/json"},
                                               io.BytesIO(json.dumps(body).encode()))
            opener = Opener(error=response)
            with self.assertRaises(diag.DiagnosticError) as error:
                self.client(opener).repair(**valid)
            self.assertEqual(error.exception.public()["error"]["server_code"], code)
            self.assertFalse(error.exception.unknown)
            self.assertEqual(len(opener.requests), 1)

    def test_repair_capability_and_cli_commands(self):
        data = valid_reply("runtimes", runtimes=[{
            "runtime_id": "runtime", "instance_id": "instance", "boot_id": "boot",
            "last_seen_age_ms": 0, "consent": {"granted": True, "epoch": "consent"},
            "capabilities": ["playback", "network", "input", "epg", "repairs"],
        }])
        self.assertEqual(self.client(Opener(Response(data))).runtimes("device"), data)
        for command in (["repair", "--device", "device", "--runtime", "runtime", "--consent-epoch", "consent",
                         "--action", "reload_player", "--server-epoch", EPOCH, "--idempotency-key", "repair-key"],
                        ["repair-status", "--repair", "repair-one"]):
            output = io.StringIO()
            opener = Opener()
            with mock.patch.dict(os.environ, {"OTT_TEST": TOKEN}), mock.patch.object(diag.urllib.request, "build_opener", return_value=opener), contextlib.redirect_stdout(output):
                code = diag.main(["--server", "https://controller.example", "--token-env", "OTT_TEST", *command])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["repair_id"], "repair-one")
            self.assertNotIn(TOKEN, output.getvalue())
            if command[0] == "repair":
                self.assertEqual(json.loads(opener.requests[0].data)["deadline_ms"], 10000)

    def test_mutations_never_retry_unknown_response(self):
        opener = Opener(error=OSError(TOKEN))
        with self.assertRaises(diag.DiagnosticError) as caught:
            self.client(opener).start("device", "runtime", "consent", 1000, "key", EPOCH)
        self.assertTrue(caught.exception.unknown)
        self.assertEqual(len(opener.requests), 1)
        self.assertNotIn(TOKEN, json.dumps(caught.exception.public()))

    def test_epoch_change_is_unknown_without_retry(self):
        opener = Opener(Response(valid_reply("stop", server_epoch="new_epoch"), status=202))
        with self.assertRaises(diag.DiagnosticError) as caught:
            self.client(opener).stop("session", "key", EPOCH)
        self.assertEqual(caught.exception.code, "epoch_changed")
        self.assertTrue(caught.exception.unknown)
        self.assertEqual(len(opener.requests), 1)

    def test_input_validation_before_network(self):
        opener = Opener()
        client = self.client(opener)
        calls = [lambda: client.runtimes("../device"), lambda: client.status("a/b"),
                 lambda: client.start("d", "r", "c", True, "k", EPOCH),
                 lambda: client.start("d", "r", "c", 600001, "k", EPOCH),
                 lambda: client.start("d", "r", "c", 1000, "", EPOCH),
                 lambda: client.events("s", -1), lambda: client.events("s", 0, 33),
                 lambda: client.events("s", 9007199254740992),
                 lambda: client.revoke("r", "bad\r\nheader")]
        for call in calls:
            with self.subTest(call=call), self.assertRaises(diag.DiagnosticError):
                call()
        self.assertEqual(opener.requests, [])

    def test_https_and_fixed_base_validation(self):
        for url in ("http://controller.example", "https://user:secret@example.com", "https://example.com/?token=x",
                    "https://example.com/#fragment", "https://example.com/a/../b", "https://example.com/%2e%2e/",
                    "https://example.com//path", "https://example.com:bad", "https://example.com\n"):
            with self.subTest(url=url), self.assertRaises(diag.DiagnosticError):
                diag.DiagnosticsClient(url, TOKEN)

    def test_redirect_handler_never_reissues_credentials(self):
        request = urllib.request.Request("https://controller.example", headers={"Authorization": "Bearer " + TOKEN})
        handler = diag.NoRedirect()
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code), self.assertRaises(diag.DiagnosticError):
                handler.redirect_request(request, None, code, "reason", {}, "https://evil.example")

    def test_bounded_total_timeout_and_worker_backpressure(self):
        opener = Opener(delay=0.08)
        client = self.client(opener, timeout=0.01)
        started = time.monotonic()
        with self.assertRaises(diag.DiagnosticError) as caught:
            client.stop("s", "k", EPOCH)
        self.assertLess(time.monotonic() - started, 0.07)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertTrue(caught.exception.unknown)
        with self.assertRaises(diag.DiagnosticError) as busy:
            client.status("s")
        self.assertEqual(busy.exception.code, "request_in_progress")
        self.assertEqual(len(opener.requests), 1)
        time.sleep(0.08)

    def test_strict_bounded_response_and_no_raw_errors(self):
        cases = [Response(raw=b"x" * (diag.MAX_REPLY + 1)), Response(raw=b'{"a":1,"a":2}'),
                 Response(raw=b'{"diagnostics_protocol":2,"server_epoch":"e","events":[NaN]}'),
                 Response(raw=b'{"diagnostics_protocol":2,"server_epoch":"e"} trailing'),
                 Response(raw=b'\xff'), Response(content_type="text/html"),
                 Response({"diagnostics_protocol": 1, "server_epoch": EPOCH})]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(diag.DiagnosticError):
                self.client(Opener(response)).status("session")
        error = urllib.error.HTTPError("https://controller.example/" + TOKEN, 403, TOKEN, {}, io.BytesIO(TOKEN.encode()))
        with self.assertRaises(diag.DiagnosticError) as caught:
            self.client(Opener(error=error)).status("session")
        self.assertNotIn(TOKEN, json.dumps(caught.exception.public()))
        self.assertFalse(caught.exception.unknown)

    def test_output_projection_and_token_redaction(self):
        data = {"diagnostics_protocol": 2, "server_epoch": EPOCH, "token": TOKEN, "message": TOKEN,
                "runtimes": [{"runtime_id": "runtime", "instance_id": TOKEN, "boot_id": "boot",
                              "last_seen_age_ms": 0, "consent": {"granted": False}, "capabilities": [],
                              "authorization": TOKEN}],
                "events": [{"seq": 1, "kind": "playback", "code": "sample", "elapsed_ms": 1,
                            "metrics": {"currentTime": 2, "raw": TOKEN}}]}
        result = self.client(Opener(Response(data))).runtimes("device")
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual(result["runtimes"][0]["instance_id"], "[redacted]")
        self.assertNotIn("raw", result["events"][0]["metrics"])

    def test_utf8_only_protocol_and_typed_metrics(self):
        envelope = {"diagnostics_protocol": 2, "server_epoch": EPOCH}
        for encoding in ("utf-16", "utf-32", "utf-8-sig"):
            with self.subTest(encoding=encoding), self.assertRaises(diag.DiagnosticError):
                self.client(Opener(Response(raw=json.dumps(envelope).encode(encoding)))).status("session")
        for value in (2.0, True, "2"):
            with self.subTest(protocol=value), self.assertRaises(diag.DiagnosticError):
                self.client(Opener(Response({**envelope, "diagnostics_protocol": value}))).status("session")
        for metrics in ({"currentTime": "secret"}, {"currentTime": True}, {"currentTime": -1},
                        {"paused": 1}, {"enabled": "true"}, {"height": []}):
            data = valid_reply("events", events=[{"seq": 1, "event": {"elapsed_ms": 0,
                               "kind": "playback", "code": "sample", "metrics": metrics}}], next_seq=1)
            with self.subTest(metrics=metrics), self.assertRaises(diag.DiagnosticError):
                self.client(Opener(Response(data))).events("session")
        data = valid_reply("events", events=[{"seq": 1, "event": {"elapsed_ms": 0,
                           "kind": "playback", "code": "sample",
                           "metrics": {"currentTime": 1.5, "paused": False}}}], next_seq=1)
        self.assertEqual(self.client(Opener(Response(data))).events("session")["events"], data["events"])

    @unittest.skipUnless(shutil.which("openssl"), "local TLS fixture requires openssl")
    def test_real_tls_routes_verification_and_redirect_refusal(self):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def do_DELETE(self):
                self.respond()

            def respond(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                requests.append((self.command, self.path, self.headers.get("Authorization"), body))
                if self.path.startswith("/redirect/"):
                    self.send_response(302)
                    self.send_header("Location", "/must-not-follow")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = json.dumps(route_reply(self.path, self.command)).encode()
                self.send_response(202 if self.command == "POST" else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        with tempfile.TemporaryDirectory() as directory:
            cert = str(Path(directory) / "cert.pem")
            key = str(Path(directory) / "key.pem")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-days", "2", "-keyout", key, "-out", cert, "-subj", "/CN=localhost",
                            "-addext", "subjectAltName=DNS:localhost"],
                           check=True, capture_output=True, timeout=15)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = "https://localhost:" + str(server.server_port)
                verified = ssl.create_default_context(cafile=cert)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), diag.NoRedirect(),
                                                     urllib.request.HTTPSHandler(context=verified))
                client = diag.DiagnosticsClient(base + "/ok", TOKEN, opener=opener)
                self.assertEqual(client.runtimes("device")["runtimes"], [])
                client.start("device", "runtime", "consent", 1000, "start-key", EPOCH)
                client.stop("session", "stop-key", EPOCH)
                client.revoke("runtime", EPOCH)
                client.repair("device", "runtime", "consent", "restart_stream", 1000, "repair-key", EPOCH)
                client.repair_status("repair-one")
                self.assertEqual([request[0] for request in requests], ["GET", "POST", "POST", "DELETE", "POST", "GET"])
                self.assertTrue(all(request[2] == "Bearer " + TOKEN for request in requests))
                redirect = diag.DiagnosticsClient(base + "/redirect", TOKEN, opener=opener)
                with self.assertRaises(diag.DiagnosticError) as error:
                    redirect.runtimes("device")
                self.assertEqual(error.exception.code, "redirect_refused")
                self.assertEqual(len(requests), 7)
                # Without the explicit synthetic CA the normal TLS verifier fails.
                with self.assertRaises(diag.DiagnosticError) as error:
                    diag.DiagnosticsClient(base, TOKEN).runtimes("device")
                self.assertEqual(error.exception.code, "transport_error")
                self.assertEqual(len(requests), 7)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)

    def test_action_reply_contract_and_event_payload_preservation(self):
        event = {"elapsed_ms": 1000, "kind": "playback", "code": "sample",
                 "metrics": {"currentTime": 1, "paused": False}}
        page = valid_reply("events", events=[{"seq": 1, "event": event}], next_seq=1)
        result = self.client(Opener(Response(page))).events("session")
        self.assertEqual(result, page)
        receipt = valid_reply("start")
        result = self.client(Opener(Response(receipt, status=202))).start("d", "r", "c", 1000, "key", EPOCH)
        self.assertEqual(result["idempotency_retention_ms"], 900000)
        for field in ("session_id", "request_id", "state", "control_revision", "idempotency_retention_ms"):
            malformed = dict(receipt)
            del malformed[field]
            with self.subTest(field=field), self.assertRaises(diag.DiagnosticError) as error:
                self.client(Opener(Response(malformed, status=202))).start("d", "r", "c", 1000, "key", EPOCH)
            self.assertEqual(error.exception.code, "invalid_response")
            self.assertTrue(error.exception.unknown)
        cases = [("status", valid_reply("status", session_id="other")),
                 ("stop", valid_reply("stop", session_id="other")),
                 ("revoke", valid_reply("revoke", device_stop_confirmed=True)),
                 ("events", {**page, "next_seq": 2}),
                 ("events", {**page, "events": [{"seq": 1, **event}]}),
                 ("events", {**page, "events": [{"seq": 1, "event": event}, {"seq": 1, "event": event}]}),
                 ("runtimes", valid_reply("runtimes", runtimes=[{}]))]
        for kind, data in cases:
            client = self.client(Opener(Response(data, status=202 if kind == "stop" else 200)))
            with self.subTest(kind=kind, data=data), self.assertRaises(diag.DiagnosticError):
                if kind == "stop":
                    client.stop("session", "key", EPOCH)
                elif kind == "revoke":
                    client.revoke("runtime", EPOCH)
                else:
                    getattr(client, kind)("session")

    def test_typed_server_errors_and_epoch_change_on_rejection(self):
        for code, epoch, expected in (("consent_required", EPOCH, "http_error"),
                                      ("server_epoch_mismatch", "new_epoch", "epoch_changed"),
                                      (TOKEN, EPOCH, "http_error")):
            payload = {"diagnostics_protocol": 2, "server_epoch": epoch,
                       "error": {"code": code, "message": TOKEN}, "raw": TOKEN}
            response = urllib.error.HTTPError("https://controller.example", 409, TOKEN,
                                               {"Content-Type": "application/json"},
                                               io.BytesIO(json.dumps(payload).encode()))
            with self.subTest(code=code), self.assertRaises(diag.DiagnosticError) as caught:
                self.client(Opener(error=response)).stop("session", "key", EPOCH)
            public = caught.exception.public()
            self.assertEqual(public["error"]["code"], expected)
            self.assertEqual(public["error"]["unknown_outcome"], epoch != EPOCH)
            self.assertNotIn(TOKEN, json.dumps(public))
            if code == TOKEN:
                self.assertNotIn("server_code", public["error"])
            else:
                self.assertEqual(public["error"]["server_code"], code)

    def test_environment_and_private_file_credentials(self):
        with mock.patch.dict(os.environ, {"OTT_DIAGNOSTIC_TEST_TOKEN": TOKEN}, clear=True):
            self.assertEqual(diag.load_token(env="OTT_DIAGNOSTIC_TEST_TOKEN"), TOKEN)
            for options in ({"env": "MISSING"}, {"env": TOKEN}, {"env": "BAD=NAME"}, {}):
                with self.assertRaises(diag.DiagnosticError):
                    diag.load_token(**options)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text(TOKEN + "\n")
            path.chmod(0o600)
            if os.name == "posix":
                self.assertEqual(diag.load_token(path=str(path)), TOKEN)
            else:
                with self.assertRaises(diag.DiagnosticError) as error:
                    diag.load_token(path=str(path))
                self.assertEqual(error.exception.code, "token_file_unsupported")
                self.assertIn("--token-env", str(error.exception))
            path.chmod(0o644)
            with self.assertRaises(diag.DiagnosticError):
                diag.load_token(path=str(path))
            if os.name == "posix":
                path.chmod(0o600)
                link = Path(directory) / "link"
                link.symlink_to(path)
                with self.assertRaises(diag.DiagnosticError):
                    diag.load_token(path=str(link))

    def test_non_posix_credentials_fail_closed_before_file_access(self):
        with mock.patch.object(diag.os, "name", "nt"), mock.patch.object(diag.os, "open") as opened:
            with self.assertRaises(diag.DiagnosticError) as error:
                diag.load_token(path="synthetic-private-token-file")
            self.assertEqual(error.exception.code, "token_file_unsupported")
            self.assertIn("--token-env", str(error.exception))
            opened.assert_not_called()
            with mock.patch.dict(os.environ, {"OTT_WINDOWS_TEST": TOKEN}, clear=True):
                self.assertEqual(diag.load_token(env="OTT_WINDOWS_TEST"), TOKEN)
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    code = diag.main(["--server", "https://controller.invalid", "--token-file",
                                      "synthetic-private-token-file", "runtimes", "--device", "device"])
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "token_file_unsupported")
                self.assertNotIn(TOKEN, stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())
            opened.assert_not_called()

    def test_cli_never_echoes_invalid_argument(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = diag.main(["--token", TOKEN])
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, stderr.getvalue())
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "invalid_arguments")

    def test_ott_dispatch_without_legacy_config_and_through_symlink(self):
        script = Path(__file__).resolve().parents[1] / "cli" / "ott.py"
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "ott"
            link.symlink_to(script)
            env = {**os.environ, "OTT_CONFIG": str(Path(directory) / "missing-legacy-config.json")}
            env.pop("OTT_DIAGNOSTICS_MISSING_TEST", None)
            for entry in (script, link):
                with self.subTest(entry=entry):
                    result = subprocess.run([sys.executable, str(entry), "diagnostics", "--help"],
                                            cwd=directory, env=env, capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("--token-env", result.stdout)
                    self.assertIn("runtimes", result.stdout)
                    self.assertEqual(result.stderr, "")
                    result = subprocess.run([sys.executable, str(entry), "diagnostics", "--server",
                                             "https://controller.invalid", "--token-env",
                                             "OTT_DIAGNOSTICS_MISSING_TEST", "runtimes", "--device", "device"],
                                            cwd=directory, env=env, capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_token")
                    self.assertNotIn("missing-legacy-config", result.stderr)

    def test_cli_json_with_mocked_transport(self):
        output = io.StringIO()
        opener = Opener(Response({"diagnostics_protocol": 2, "server_epoch": EPOCH, "runtimes": []}))
        with mock.patch.dict(os.environ, {"OTT_TEST": TOKEN}), mock.patch.object(diag.urllib.request, "build_opener", return_value=opener), contextlib.redirect_stdout(output):
            code = diag.main(["--server", "https://controller.example", "--token-env", "OTT_TEST", "runtimes", "--device", "device"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["runtimes"], [])


if __name__ == "__main__":
    unittest.main()
