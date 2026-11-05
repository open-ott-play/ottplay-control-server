"""Favorite RPC receipts retain safe action identity without leaking payloads."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "ott_favorites_receipts", Path(__file__).resolve().parents[1] / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

FAVORITES_ACTIONS = (
    "favorites_transfer", "favorites_global", "favorites_global_items",
    "favorites_global_recovery",
)
REQUEST_ID = "a" * 32
PRIVATE = "fixture-private-favorites-token"
PRIVATE_URL = "https://fixture.invalid/private?token=" + PRIVATE


class FavoritesReceiptCLITest(unittest.TestCase):
    def setUp(self):
        self.network = patch("socket.socket", side_effect=AssertionError("network is forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def client(self, responses):
        client = object.__new__(ott.Client)
        client.timeout = 3
        client.api = Mock(side_effect=responses)
        client.on_receipt = ott.print_request_receipt
        return client

    def queued(self):
        return 202, {"id": REQUEST_ID, "status": "queued", "receipt_wait_ms": 1000}

    def params(self):
        # The transport treats the domain payload as opaque. Its diagnostic
        # projection must never copy private request or response fields.
        return {"runtime": "native-fixture", "op": "current", "private_input": PRIVATE_URL}

    def assert_receipt(self, stream, action, status, request_id=REQUEST_ID):
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {
            "action": action, "request_id": request_id, "status": status,
        })
        self.assertNotIn(PRIVATE, stream.getvalue())
        self.assertNotIn(PRIVATE_URL, stream.getvalue())

    def assert_one_submission(self, client, action, *, reads=1):
        calls = client.api.call_args_list
        self.assertEqual(len(calls), 1 + reads)
        self.assertEqual(calls[0].args[1], {"action": action, "params": self.params()})
        for call in calls[1:]:
            self.assertEqual(len(call.args), 1)
            self.assertIn("&id=" + REQUEST_ID, call.args[0])

    def test_success_retains_each_favorites_action_and_omits_private_data(self):
        for action in FAVORITES_ACTIONS:
            with self.subTest(action=action):
                data = {"private_output": PRIVATE_URL}
                client = self.client([self.queued(), (200, {
                    "id": REQUEST_ID, "status": "ok", "data": data,
                })])
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    result = client.call_receipt("fixture-native", action, self.params())
                self.assertEqual(result, {"request_id": REQUEST_ID, "status": "ok", "data": data})
                self.assert_receipt(output, action, "ok")
                self.assert_one_submission(client, action)

    def test_terminal_refusals_preserve_favorites_action(self):
        for action in FAVORITES_ACTIONS:
            for status, error_type in (("rejected", ott.PlayerRejected),
                                       ("unsupported", ott.PlayerUnsupported)):
                with self.subTest(action=action, status=status):
                    client = self.client([self.queued(), (200, {
                        "id": REQUEST_ID, "status": status,
                        "data": {"error": "fixture refusal", "private_output": PRIVATE_URL},
                    })])
                    output = io.StringIO()
                    with contextlib.redirect_stderr(output), self.assertRaises(error_type):
                        client.call_receipt("fixture-native", action, self.params())
                    self.assert_receipt(output, action, status)
                    self.assert_one_submission(client, action)

    def test_missing_receipt_keeps_request_identity_and_never_resubmits(self):
        for action in FAVORITES_ACTIONS:
            with self.subTest(action=action):
                client = self.client([self.queued(), ott.HTTPError(404)])
                output = io.StringIO()
                with contextlib.redirect_stderr(output), self.assertRaises(ott.RequestError) as caught:
                    client.call_receipt("fixture-native", action, self.params())
                self.assertEqual(caught.exception.action, action)
                self.assertEqual(caught.exception.reason, "receipt_missing")
                self.assertEqual(caught.exception.request_id, REQUEST_ID)
                self.assert_receipt(output, action, "unknown")
                self.assert_one_submission(client, action)

    def test_lost_enqueue_reply_preserves_action_without_inventing_request_id(self):
        for action in FAVORITES_ACTIONS:
            with self.subTest(action=action):
                client = self.client([ott.TransportError(PRIVATE_URL, "socket")])
                output = io.StringIO()
                with contextlib.redirect_stderr(output), self.assertRaises(ott.RequestError) as caught:
                    client.call_receipt("fixture-native", action, self.params())
                self.assertEqual(caught.exception.action, action)
                self.assertEqual(caught.exception.stage, "enqueue")
                self.assertIsNone(caught.exception.request_id)
                self.assertNotIn(PRIVATE, str(caught.exception))
                self.assert_receipt(output, action, "unknown", request_id=None)
                self.assert_one_submission(client, action, reads=0)

    def test_unrecognized_action_stays_redacted_instead_of_using_prefix_matching(self):
        action = "favorites_global_" + PRIVATE
        client = self.client([self.queued(), (200, {
            "id": REQUEST_ID, "status": "ok", "data": {},
        })])
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            client.call_receipt("fixture-native", action, self.params())
        self.assert_receipt(output, "unknown", "ok")
        self.assert_one_submission(client, action)


def load_fixture(name):
    """Reuse the existing synthetic controllers and their real wire contracts."""
    spec = importlib.util.spec_from_file_location(
        "receipt_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FavoritesTransportReceiptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transfer = load_fixture("test_favorites_remote")
        cls.global_favorites = load_fixture("test_global_favorites_remote")
        cls.items = load_fixture("test_global_favorites_items")
        cls.recovery = load_fixture("test_global_favorites_recovery")
        cls.commands = load_fixture("test_favorites_commands")

    def setUp(self):
        network = patch("socket.socket", side_effect=AssertionError("network is forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def rpc_case(self, action):
        if action == "favorites_transfer":
            fixture = self.transfer
            clock = fixture.Clock()
            api = fixture.MemoryAPI(clock)
            peer = fixture.remote.RemoteFavorites(
                api, "device-1", runtime=fixture.RUNTIME, limits=fixture.LIMITS,
                receipt_retention_seconds=86400,
                scope_validator=fixture.document.validate_scope_document,
                save_context=lambda _: True, monotonic=clock, sleep=clock.sleep)
            operation = lambda: peer.snapshot(copy.deepcopy(fixture.FIXTURE["scope"]))
        elif action == "favorites_global":
            fixture = self.global_favorites
            clock = fixture.Clock()
            api = fixture.MemoryAPI(clock)
            peer = fixture.remote.RemoteGlobalFavorites(
                api, "device-1", runtime="fixture-runtime", monotonic=clock, sleep=clock.sleep)
            operation = peer.describe
        elif action == "favorites_global_items":
            fixture = self.items
            clock = fixture.Clock()
            api = fixture.API(clock)
            peer = fixture.items.ItemsPeer(
                api, "device", runtime=fixture.RUNTIME, monotonic=clock, sleep=clock.sleep)
            operation = peer.current
        else:
            self.assertEqual(action, "favorites_global_recovery")
            fixture = self.recovery
            clock = fixture.Clock()
            api = fixture.API(clock)
            peer = fixture.recovery.RecoveryPeer(
                api, "fixture-device", runtime=fixture.RUNTIME, monotonic=clock, sleep=clock.sleep)
            operation = lambda: peer.inspect(fixture.OPERATION)
        api.on_receipt = ott.print_request_receipt
        return api, operation

    def assert_receipt(self, output, action, status, request_id="0" * 31 + "1"):
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {
            "action": action, "request_id": request_id, "status": status,
        })
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertNotIn(PRIVATE_URL, output.getvalue())

    def assert_single_post(self, api):
        methods = [call["method"] if isinstance(call, dict) else call[1] for call in api.calls]
        self.assertEqual(methods.count("POST"), 1)

    def replace_reply(self, api, transform):
        original = api.api_raw

        def reply(*args, **kwargs):
            status, raw = original(*args, **kwargs)
            body = json.loads(raw)
            transform(body)
            return status, json.dumps(body).encode()

        api.api_raw = reply

    def test_each_real_favorites_transport_emits_one_bound_success_receipt(self):
        for action in FAVORITES_ACTIONS:
            with self.subTest(action=action):
                api, operation = self.rpc_case(action)
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    self.assertIsNotNone(operation())
                self.assert_receipt(output, action, "ok")
                self.assert_single_post(api)

    def test_only_validated_terminal_refusals_are_reported_as_terminal(self):
        for status in ("rejected", "unsupported"):
            with self.subTest(status=status):
                api, operation = self.rpc_case("favorites_global_items")

                def refusal(body):
                    body["status"] = status
                    body["data"] = {
                        "version": 1, "runtime": self.items.RUNTIME, "op": "current",
                        "subject": "global", "error": "unsupported" if status == "unsupported" else "context_changed",
                    }

                self.replace_reply(api, refusal)
                output = io.StringIO()
                with contextlib.redirect_stderr(output), self.assertRaises(self.transfer.remote.RemoteError) as caught:
                    operation()
                self.assertEqual(caught.exception.code, "player_" + status)
                self.assert_receipt(output, "favorites_global_items", status)
                self.assert_single_post(api)

    def test_untrusted_status_with_invalid_context_stays_unknown(self):
        for status in ("ok", "rejected", "unsupported", PRIVATE_URL):
            with self.subTest(status=status):
                api, operation = self.rpc_case("favorites_global_items")

                def malformed(body):
                    body["status"] = status
                    body["data"]["runtime"] = PRIVATE_URL

                self.replace_reply(api, malformed)
                output = io.StringIO()
                with contextlib.redirect_stderr(output), self.assertRaises(self.transfer.remote.RemoteError) as caught:
                    operation()
                self.assertEqual(caught.exception.code, "invalid_response")
                self.assertNotIn(PRIVATE, str(caught.exception))
                self.assert_receipt(output, "favorites_global_items", "unknown")
                self.assert_single_post(api)

    def test_lost_enqueue_emits_unknown_without_request_id_or_replay(self):
        api, operation = self.rpc_case("favorites_global_items")
        api.api = Mock(side_effect=OSError(PRIVATE_URL))
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(self.transfer.remote.RemoteError) as caught:
            operation()
        self.assertEqual(caught.exception.code, "transport")
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assert_receipt(output, "favorites_global_items", "unknown", request_id=None)
        self.assertEqual(api.api.call_count, 1)
        self.assertEqual(api.calls, [])

    def test_callback_failure_does_not_change_success_or_cause_replay(self):
        api, operation = self.rpc_case("favorites_global_items")
        api.on_receipt = Mock(side_effect=RuntimeError(PRIVATE_URL))
        self.assertIsNotNone(operation())
        self.assertEqual(api.on_receipt.call_count, 1)
        self.assert_single_post(api)

    def capability_case(self):
        fixture = self.commands
        clock = fixture.fixture.Clock()
        directory = self.enterContext(tempfile.TemporaryDirectory())
        api = fixture.CurrentAPI(clock, Path(directory))
        api.on_receipt = ott.print_request_receipt
        target = fixture.commands._Target(
            fixture.ott, api, Path(directory) / "cli.json", "one", 45,
            clock, clock.sleep, lambda: fixture.fixture.OPERATION)
        return api, target.read_capabilities

    def test_capability_discovery_emits_receipt_after_metadata_validation(self):
        api, operation = self.capability_case()
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            controls, _ = operation()
        self.assertEqual(controls["player"]["runtime"], self.commands.fixture.RUNTIME)
        self.assert_receipt(output, "capabilities", "ok")
        self.assert_single_post(api)

    def test_invalid_capability_metadata_does_not_become_an_ok_receipt(self):
        api, operation = self.capability_case()
        api.caps_change = lambda data: {**data, "player": {"runtime": PRIVATE_URL}}
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(self.commands.ott.Error):
            operation()
        self.assert_receipt(output, "capabilities", "unknown")
        self.assert_single_post(api)

    def test_unvalidated_capability_refusal_is_unknown(self):
        api, operation = self.capability_case()
        self.replace_reply(api, lambda body: body.update(status="rejected"))
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(self.commands.commands.CommandError):
            operation()
        self.assert_receipt(output, "capabilities", "unknown")
        self.assert_single_post(api)

    def test_capability_callback_failure_does_not_change_result_or_replay(self):
        api, operation = self.capability_case()
        api.on_receipt = Mock(side_effect=RuntimeError(PRIVATE_URL))
        self.assertIsNotNone(operation())
        self.assertEqual(api.on_receipt.call_count, 1)
        self.assert_single_post(api)


if __name__ == "__main__":
    unittest.main()
