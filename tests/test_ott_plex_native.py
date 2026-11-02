import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_plex_native", ROOT / "scripts/ott_plex_native.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def request(operation="discover", **fields):
    result = {"operation": operation, "timeout": 10, "controller_id": "ott-workflow-uuid", "command_id": 12,
              "config": {"plex_url": "http://pms.test:32400", "plex_token_file": str(ROOT / "test-token"),
                         "plex_client_id": "lg-client", "plex_server_id": "test-server"}}
    if operation in ("play", "status"):
        result["queue_id"] = 3810
    return dict(result, **fields)


def movie(key):
    return SimpleNamespace(ratingKey=key, type="movie")


class Fixture:
    def __init__(self, count=3):
        self.client = mock.Mock(machineIdentifier="lg-client", protocolCapabilities=["playback", "timeline", "playqueues"])
        self.client.timelines.return_value = [SimpleNamespace(type="video", state="playing", playQueueID=3810,
                                                             ratingKey=123, time=1500, machineIdentifier="test-server")]
        self.section = mock.Mock(title=adapter.COLLECTION, type="movie")
        self.section.search.return_value = [movie(i + 1) for i in range(count)]
        self.server = mock.Mock(machineIdentifier="test-server")
        self.server.clients.return_value = [self.client]
        self.server.library.section.return_value = self.section
        self.queue = SimpleNamespace(playQueueID=3810, playQueueTotalCount=min(count, 500), selectedItem=movie(123))
        self.queues = mock.Mock()
        self.queues.create.return_value = self.queue
        self.queues.get.return_value = self.queue

    def run(self, operation, **fields):
        return adapter.operate(request(operation, **fields), server_factory=lambda: self.server, queue_factory=self.queues)


class NativeAdapterTests(unittest.TestCase):
    def test_discover_pins_snapshot_to_pms_proxy(self):
        fixture = Fixture()
        result = fixture.run("discover")
        self.assertTrue(result["ready"])
        fixture.client.proxyThroughServer.assert_called_once_with(True, server=fixture.server)
        self.assertEqual(fixture.client._commandId, 11)
        fixture.client.playMedia.assert_not_called()

    def test_missing_or_incapable_client_is_not_ready(self):
        fixture = Fixture()
        fixture.server.clients.return_value = []
        self.assertFalse(fixture.run("discover")["ready"])
        fixture.server.clients.return_value = [fixture.client]
        fixture.client.protocolCapabilities = ["playback"]
        self.assertFalse(fixture.run("discover")["ready"])
        with self.assertRaisesRegex(adapter.AdapterError, "client_not_ready"):
            fixture.run("prepare")
        fixture.queues.create.assert_not_called()

    def test_mismatched_server_and_ambiguous_client_fail_closed(self):
        fixture = Fixture()
        fixture.server.machineIdentifier = "wrong-server"
        with self.assertRaisesRegex(adapter.AdapterError, "server_mismatch"):
            fixture.run("prepare")
        fixture.server.clients.assert_not_called()
        fixture.server.machineIdentifier = "test-server"
        fixture.server.clients.return_value = [fixture.client, fixture.client]
        with self.assertRaisesRegex(adapter.AdapterError, "ambiguous_client"):
            fixture.run("play")
        fixture.client.playMedia.assert_not_called()

    def test_prepare_limits_501_items_to_one_shuffled_500_item_queue(self):
        fixture = Fixture(501)
        result = fixture.run("prepare")
        self.assertEqual(result, {"queue_id": 3810, "count": 500, "first_id": "123", "truncated": True})
        fixture.section.search.assert_called_once_with(libtype="movie", maxresults=501)
        fixture.queues.create.assert_called_once_with(fixture.server, fixture.section.search.return_value[:500],
                                                      shuffle=True, includeRelated=False)
        fixture.client.playMedia.assert_not_called()
        fixture.queues.get.assert_not_called()

    def test_prepare_preserves_complete_small_library(self):
        fixture = Fixture(2)
        self.assertEqual(fixture.run("prepare")["count"], 2)
        self.assertFalse(fixture.run("prepare")["truncated"])

    def test_bad_library_items_do_not_create_queue(self):
        for items in ([], [movie(1), movie(1)], [movie(True)], [SimpleNamespace(ratingKey=1, type="show")]):
            with self.subTest(items=items):
                fixture = Fixture()
                fixture.section.search.return_value = items
                with self.assertRaises(adapter.AdapterError):
                    fixture.run("prepare")
                fixture.queues.create.assert_not_called()

    def test_exact_library_name_and_movie_type_required(self):
        for title, kind in (("other", "movie"), (adapter.COLLECTION, "show")):
            fixture = Fixture()
            fixture.section.title, fixture.section.type = title, kind
            with self.assertRaisesRegex(adapter.AdapterError, "library_mismatch"):
                fixture.run("prepare")
            fixture.queues.create.assert_not_called()

    def test_play_reuses_queue_and_proxies_before_exactly_one_command(self):
        fixture = Fixture()
        def check(*args, **kwargs):
            fixture.client.proxyThroughServer.assert_called_once_with(True, server=fixture.server)
            self.assertEqual(fixture.client._commandId, 11)
        fixture.client.playMedia.side_effect = check
        self.assertEqual(fixture.run("play"), {"accepted": True, "queue_id": 3810})
        fixture.queues.get.assert_called_once_with(fixture.server, 3810)
        fixture.queues.create.assert_not_called()
        fixture.client.playMedia.assert_called_once_with(fixture.queue, offset=0)

    def test_failed_play_is_not_repeated(self):
        fixture = Fixture()
        fixture.client.playMedia.side_effect = TimeoutError("secret URL")
        with self.assertRaises(TimeoutError):
            fixture.run("play")
        self.assertEqual(fixture.client.playMedia.call_count, 1)
        fixture.queues.create.assert_not_called()

    def test_invalid_queue_never_plays(self):
        for field, value in (("playQueueID", 3809), ("playQueueTotalCount", 501), ("playQueueTotalCount", True),
                             ("selectedItem", movie(None))):
            fixture = Fixture()
            setattr(fixture.queue, field, value)
            with self.assertRaisesRegex(adapter.AdapterError, "invalid_queue"):
                fixture.run("play")
            fixture.client.playMedia.assert_not_called()

    def test_status_uses_exact_native_queue_and_converts_position(self):
        fixture = Fixture()
        self.assertEqual(fixture.run("status"), {"state": "playing", "queue_id": 3810, "content_id": "123", "position": 1.5})
        fixture.client.timelines.assert_called_once_with(wait=0)
        fixture.client.playMedia.assert_not_called()

    def test_other_server_or_ambiguous_timeline_cannot_confirm_queue(self):
        for identifier in (None, "different-server"):
            fixture = Fixture()
            fixture.client.timelines.return_value[0].machineIdentifier = identifier
            self.assertEqual(fixture.run("status")["state"], "unknown")
            self.assertIsNone(fixture.run("status")["queue_id"])
        fixture = Fixture()
        fixture.client.timelines.return_value *= 2
        self.assertEqual(fixture.run("status")["state"], "unknown")

    def test_status_missing_queue_does_not_infer_requested_queue(self):
        fixture = Fixture()
        timeline = fixture.client.timelines.return_value[0]
        timeline.playQueueID, timeline.time, timeline.state = None, float("nan"), "error"
        result = fixture.run("status")
        self.assertIsNone(result["queue_id"])
        self.assertIsNone(result["position"])
        self.assertEqual(result["state"], "unknown")

    def test_bad_input_rejected_before_any_network(self):
        cases = ({"timeout": True}, {"timeout": float("inf")}, {"timeout": 46}, {"command_id": 0},
                 {"controller_id": "bad\nheader"}, {"operation": "arbitrary"}, {"queue_id": 12}, {"extra": "x"})
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(adapter.AdapterError):
                adapter.request_value(request(**fields))
        with self.assertRaises(adapter.AdapterError):
            adapter.request_value(request("play", queue_id=True))

    def test_origin_and_file_configuration_are_restricted(self):
        for url in ("http://user:secret@pms:32400", "http://pms:32400/prefix", "ftp://pms:32400", "http://pms",
                    "http://pms:32400?token=secret", "http://[::1]:32400", "http://pms:32400/#x"):
            config = request()["config"]
            config["plex_url"] = url
            with self.subTest(url=url), self.assertRaises(adapter.AdapterError):
                adapter.config_value(config)
        config = request()["config"]
        config["plex_token_file"] = "relative-token"
        with self.assertRaises(adapter.AdapterError):
            adapter.config_value(config)

    @unittest.skipIf(os.name == "nt", "Shortcut uses POSIX ownership and no-follow files")
    def test_token_must_be_private_owned_regular_non_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"
            token.write_text("private-test-token\n")
            token.chmod(0o600)
            self.assertEqual(adapter.read_token(str(token)), "private-test-token")
            link = Path(directory) / "symlink"
            link.symlink_to(token)
            with self.assertRaises(OSError):
                adapter.read_token(str(link))
            token.chmod(0o644)
            with self.assertRaises(adapter.AdapterError):
                adapter.read_token(str(token))

    def test_cli_never_prints_sensitive_exception(self):
        stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(request()).encode()))
        with mock.patch.object(adapter.sys, "stdin", stdin), mock.patch.object(adapter, "operate", side_effect=RuntimeError("token=SECRET")):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(adapter.main(), 1)
            self.assertEqual(out.getvalue(), "")
            self.assertNotIn("SECRET", err.getvalue())

    def test_cli_reports_only_sanitized_semantic_rejection_code(self):
        stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(request()).encode()))
        with mock.patch.object(adapter.sys, "stdin", stdin), \
                mock.patch.object(adapter, "operate", side_effect=adapter.PlayerRejected(400)):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(adapter.main(), 1)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("code 400", err.getvalue())
            self.assertIn("not repeated", err.getvalue())


class TransportTests(unittest.TestCase):
    def session(self, status=200, chunks=(b"<xml/>",), clock=lambda: 0):
        self.response = mock.Mock(status_code=status)
        self.response.content = b"".join(chunks)
        self.response.iter_content.return_value = iter(chunks)
        self.send = mock.Mock(return_value=self.response)
        sender = self.send
        class BaseSession:
            def request(self, method, url, **kwargs):
                return sender(method, url, **kwargs)
        return adapter.bounded_session(SimpleNamespace(Session=BaseSession), request()["config"], request(), 10, clock)

    def test_fixed_headers_no_environment_proxies_no_redirects(self):
        session = self.session()
        result = session.request("GET", "http://pms.test:32400/player/timeline/poll", headers={"X-Plex-Token": "private"})
        self.assertFalse(session.trust_env)
        kwargs = self.send.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["timeout"], 10)
        self.assertEqual(kwargs["headers"]["X-Plex-Client-Identifier"], "ott-workflow-uuid")
        self.assertEqual(kwargs["headers"]["X-Plex-Target-Client-Identifier"], "lg-client")
        self.assertEqual(result._content, b"<xml/>")
        self.response.close.assert_called_once()

    def test_untrusted_urls_never_reach_transport(self):
        session = self.session()
        for url in ("http://127.0.0.1:32400/player/x", "http://pms.test:32400.attacker/x", "https://pms.test:32400/x"):
            with self.assertRaisesRegex(adapter.AdapterError, "untrusted_endpoint"):
                session.request("GET", url)
        self.send.assert_not_called()

    def test_redirect_large_body_and_expired_deadline_fail_closed(self):
        for status, chunks, clock, code in ((302, (), lambda: 0, "redirect_rejected"),
                                          (200, (b"a" * 9,), lambda: 0, "response_too_large"),
                                          (200, (), lambda: 10, "deadline")):
            session = self.session(status, chunks, clock)
            with mock.patch.object(adapter, "MAX_RESPONSE", 8), self.assertRaisesRegex(adapter.AdapterError, code):
                session.request("GET", "http://pms.test:32400/x")

    def test_http_success_with_semantic_failure_is_rejected_once(self):
        session = self.session(chunks=(b'<Response code="400" status="secret-token"/>',))
        with self.assertRaises(adapter.PlayerRejected) as caught:
            session.request("GET", "http://pms.test:32400/player/playback/playMedia")
        self.assertEqual(caught.exception.code, 400)
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertEqual(self.send.call_count, 1)

    def test_semantic_success_and_timeline_are_not_rejected(self):
        for body in (b'<Response code="200" status="OK"/>', b'<MediaContainer/>'):
            session = self.session(chunks=(body,))
            session.request("GET", "http://pms.test:32400/player/timeline/poll")

    def test_malformed_response_code_is_not_accepted(self):
        session = self.session(chunks=(b'<Response code="null"/>',))
        with self.assertRaisesRegex(adapter.AdapterError, "invalid_player_response"):
            session.request("GET", "http://pms.test:32400/player/playback/playMedia")


@unittest.skipUnless(importlib.util.find_spec("plexapi") and importlib.util.find_spec("requests"),
                     "Install the pinned shortcut runtime for the real PlexAPI contract test")
class PlexAPIContractTests(unittest.TestCase):
    def test_real_sdk_reuses_queue_and_routes_loopback_client_through_pms(self):
        import requests
        calls = []

        def send(prepared, **kwargs):
            calls.append(prepared)
            url = urlsplit(prepared.url)
            self.assertEqual(url.netloc, "pms.test:32400")
            xml = {
                "/": '<MediaContainer machineIdentifier="test-server"/>',
                "/clients": '<MediaContainer><Server name="Fixture LG" product="Plex for LG" '
                            'host="127.0.0.1" port="32400" machineIdentifier="lg-client" '
                            'protocol="plex" protocolCapabilities="playback,timeline,playqueues"/></MediaContainer>',
                "/playQueues/3810": '<MediaContainer playQueueID="3810" playQueueTotalCount="2" '
                                    'playQueueSelectedItemOffset="0" playQueueSelectedItemID="7" size="2">'
                                    '<Video type="movie" title="First" ratingKey="123" key="/library/metadata/123" playQueueItemID="7"/>'
                                    '<Video type="movie" title="Second" ratingKey="456" key="/library/metadata/456" playQueueItemID="8"/>'
                                    '</MediaContainer>',
                "/security/token": '<MediaContainer token="test-delegation-token"/>',
                "/player/playback/playMedia": '<Response code="200" status="OK"/>',
                "/player/timeline/poll": '<MediaContainer><Timeline type="video" state="playing" '
                                         'machineIdentifier="test-server" playQueueID="3810" '
                                         'ratingKey="123" time="1200"/></MediaContainer>',
            }.get(url.path)
            self.assertIsNotNone(xml, url.path)
            response = requests.Response()
            response.status_code = 200
            response.url = prepared.url
            response.encoding = "utf-8"
            response._content = xml.encode()
            response._content_consumed = True
            return response

        with mock.patch("requests.sessions.Session.send", side_effect=send), \
                mock.patch.object(adapter, "read_token", return_value="fixture-secret"):
            self.assertTrue(adapter.operate(request())["ready"])
            self.assertTrue(adapter.operate(request("play", command_id=13))["accepted"])
            status = adapter.operate(request("status", command_id=14))
        self.assertEqual(status, {"state": "playing", "queue_id": 3810, "content_id": "123", "position": 1.2})
        player_calls = [call for call in calls if urlsplit(call.url).path.startswith("/player/")]
        self.assertEqual([urlsplit(call.url).path for call in player_calls],
                         ["/player/playback/playMedia", "/player/timeline/poll"])
        self.assertEqual([parse_qs(urlsplit(call.url).query)["commandID"] for call in player_calls], [["13"], ["14"]])
        self.assertIn("/playQueues/3810", parse_qs(urlsplit(player_calls[0].url).query)["containerKey"][0])
        self.assertFalse(any(call.method == "POST" and urlsplit(call.url).path == "/playQueues" for call in calls))
        for call in calls:
            self.assertEqual(call.headers["X-Plex-Client-Identifier"], "ott-workflow-uuid")
        for call in player_calls:
            self.assertEqual(call.headers["X-Plex-Target-Client-Identifier"], "lg-client")


if __name__ == "__main__":
    unittest.main()
