"""Original HTTP bytes share the normal client's transport guards; no sockets."""
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ott_raw_transport", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class Response:
    status = 200

    def __init__(self, raw, advertised=None):
        self.raw = raw
        self.length = len(raw) if advertised is None else advertised

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read1(self, size):
        chunk, self.raw = self.raw[:size], self.raw[size:]
        self.length -= len(chunk)
        return chunk


class RawTransportTests(unittest.TestCase):
    def client(self, response):
        client = object.__new__(ott.Client)
        client.timeout = 2
        client.server = "https://fixture.invalid/control"
        client.token = "fixture-token"
        client.opener = mock.Mock()
        client.opener.open.return_value = response
        return client

    def test_original_bytes_and_default_parsed_api_are_separate(self):
        raw = ' { "status" : "ok", "data": { "name":"Пример" } } \n'.encode()
        client = self.client(Response(raw))
        self.assertEqual(client.api_raw("/api/requests?id=fixture"), (200, raw))
        self.assertEqual(self.client(Response(raw)).api("/api/requests?id=fixture"), (200, json.loads(raw)))
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer fixture-token")

    def test_raw_api_does_not_hide_duplicate_keys_from_protocol_validator(self):
        raw = b'{"status":"ok","status":"other"}'
        self.assertEqual(self.client(Response(raw)).api_raw("/api/requests"), (200, raw))
        with self.assertRaises(ott.Error):
            self.client(Response(raw)).api("/api/requests")

    def test_global_limit_and_truncation_are_identical(self):
        for method in ("api", "api_raw"):
            with self.subTest(method=method):
                with self.assertRaisesRegex(ott.Error, "size limit"):
                    getattr(self.client(Response(b" " * (2 * 1024 * 1024 + 1))), method)("/api/requests")
                with self.assertRaises(ott.TransportError):
                    getattr(self.client(Response(b"{}", advertised=12)), method)("/api/requests")

    def test_post_serialization_and_no_automatic_retry(self):
        client = self.client(Response(b'{"status":"queued"}'))
        payload = {"action": "favorites_transfer", "params": {"runtime": "fixture-runtime", "op": "status"}}
        self.assertEqual(client.api_raw("/api/requests", payload)[0], 200)
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(client.opener.open.call_args.args[0].data, json.dumps(payload, ensure_ascii=False).encode())
        client.opener.open.side_effect = OSError("private connection details")
        with self.assertRaises(ott.TransportError) as caught:
            client.api_raw("/api/requests", payload)
        self.assertEqual(client.opener.open.call_count, 2)
        self.assertNotIn("private connection", str(caught.exception))

    def test_raw_worker_cannot_publish_late_success_after_deadline(self):
        released, finished = threading.Event(), threading.Event()
        client = self.client(Response(b"{}"))
        client.timeout = 0.02
        def slow_open(*_, **__):
            try:
                released.wait(1)
                return Response(b"{}")
            finally:
                finished.set()
        client.opener.open.side_effect = slow_open
        try:
            with self.assertRaises(ott.TransportError):
                client.api_raw("/api/requests")
        finally:
            released.set()
            self.assertTrue(finished.wait(1))
        self.assertEqual(client.opener.open.call_count, 1)
