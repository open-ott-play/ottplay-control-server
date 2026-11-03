"""Passive Local diagnostics bind fresh status without exporting private status fields."""
import contextlib
import copy
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from test_debug_cli import DebugClient
from test_workbench_cli import Clock, RUNTIME, SECRET, capabilities, ott, wb


PRIVATE_URL = "https://private.invalid/video?token=" + SECRET
MAX_INTEGER = 9007199254740991


def descriptor(kind="empty", match="not_applicable", item=None, source=None):
    return {"kind": kind, "itemId": item, "sourceId": source, "endpointMatch": match}


def sample(runtime=RUNTIME):
    return {"version": 1, "runtime": runtime, "capturedAt": 1700000000000,
            "consistent": True, "generation": 7, "handleId": 2,
            "currentSource": descriptor(),
            "requestedSource": descriptor("direct", "match", "a" * 32, "b" * 32),
            "mediaErrorCode": None, "codecs": {"h264": "probably", "hevc": "maybe"}}


def inconsistent(runtime=RUNTIME):
    value = sample(runtime)
    value.update(consistent=False, generation=None, handleId=None, mediaErrorCode=None,
                 currentSource=descriptor("unavailable", "unknown"),
                 requestedSource=descriptor("unavailable", "unknown"),
                 codecs={"h264": None, "hevc": None})
    return value


class LocalClient(ott.Client):
    """Exercise real CLI routing with only the network request boundary replaced."""

    def __init__(self, clock=None):
        self.clock = clock or Clock()
        self.timeout, self.on_receipt = 45, None
        self.config = {"players": {"t1": "web", "t2": "web2"},
                       "groups": {"tt": ["t1", "t2"]}, "native_devices": {}}
        self.credentials = {"devices": [{"id": "web"}, {"id": "web2"}]}
        self.server = "https://controller.invalid"
        self.calls, self.shared_calls, self.children = [], [], []
        self.lock = threading.Lock()
        self.runtimes = {"web": RUNTIME, "web2": "page-two"}
        self.cap_transform = self.status_transform = self.before = None
        self.barrier = None

    def call_receipt(self, target, action, params):
        if action not in ("capabilities", "status") or params != {}:
            raise AssertionError("Local diagnostics must issue only passive caps/status reads")
        with self.lock:
            record = (target, action, copy.deepcopy(params), self.timeout)
            self.calls.append(record)
            self.shared_calls.append(record)
            identifier = len(self.shared_calls)
        if self.before:
            self.before(self, target, action)
        runtime = self.runtimes[target]
        if action == "capabilities":
            value = capabilities()
            value["player"]["runtime"] = runtime
            value["local_source_diagnostics"] = {"version": 1}
            transform = self.cap_transform
        else:
            if self.barrier:
                self.barrier.wait(timeout=5)
            value = {"provider": "offline", "url": PRIVATE_URL, "token": SECRET,
                     "local_source_diagnostics": sample(runtime)}
            transform = self.status_transform
        if transform:
            value = transform(value, target)
        return {"request_id": format(identifier, "032x"), "status": "ok", "data": value}

    def for_group(self, cancelled):
        child = LocalClient()
        for name in ("shared_calls", "children", "lock", "runtimes", "cap_transform",
                     "status_transform", "before", "barrier"):
            setattr(child, name, getattr(self, name))
        child.group_cancelled = cancelled
        with self.lock:
            self.children.append(child)
        return child


class LocalSourceDiagnosticsCLITest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = LocalClient(self.clock)

    def execute(self, words=None, *, client=None, json_output=True, timeout=20):
        output, errors = io.StringIO(), io.StringIO()
        client = client or self.client
        previous = client.timeout
        with mock.patch.object(wb, "time", self.clock), contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(errors):
            code = wb.run(ott, client, "web", words or ["debug", "--local-source"], timeout, json_output)
        text = output.getvalue()
        self.assertNotIn(SECRET, text + errors.getvalue())
        self.assertNotIn(PRIVATE_URL, text + errors.getvalue())
        self.assertEqual(client.timeout, previous)
        return code, json.loads(text) if json_output else text

    def test_fresh_capability_then_empty_status_request_projects_only_the_dto(self):
        code, result = self.execute()
        self.assertEqual((code, result["verdict"]), (0, "observed"))
        self.assertTrue(result["read_only"])
        self.assertFalse(result["physical_display_verified"])
        self.assertEqual(result["observations"][0]["data"], sample())
        self.assertEqual([(c[1], c[2]) for c in self.client.calls], [("capabilities", {}), ("status", {})])
        self.client.calls.clear()
        self.assertEqual(self.execute()[0], 0)
        self.assertEqual([c[1] for c in self.client.calls], ["capabilities", "status"])

    def test_canonical_producer_fixture_has_the_same_consumer_contract(self):
        path = Path(__file__).resolve().parents[1] / "contracts" / "local-source-diagnostics-v1.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        value = fixture["example"]
        self.assertEqual(fixture["capability"], {"local_source_diagnostics": {"version": 1}})
        self.assertEqual(wb.local_source_metadata({fixture["status_field"]: value}, value["runtime"]), value)

    def test_real_cli_main_route_and_group_alias_remain_passive(self):
        for player in ("t1", "tt", "t1,t2"):
            with self.subTest(player=player):
                self.client = LocalClient()
                output, errors = io.StringIO(), io.StringIO()
                with mock.patch.object(ott, "read_json", return_value=self.client.config), \
                        mock.patch.object(ott, "Client", return_value=self.client), \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    code = ott.main(["-j", player, "debug", "--local-source"])
                self.assertEqual(code, 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result["verdict"] if player == "t1" else result["succeeded"],
                                 "observed" if player == "t1" else 2)
                self.assertEqual(errors.getvalue(), "")
                self.assertNotIn(SECRET, output.getvalue())

    def test_ordinary_status_and_no_argument_overview_ignore_optional_diagnostics(self):
        ordinary = {"provider": "offline", "ready": True, "volume": 23, "channels": 46,
                    "player": {**capabilities()["player"], "standby": False}}
        for words in ([], ["status"]):
            baseline = None
            for optional in (None, sample(), {"version": 2, "private": PRIVATE_URL}):
                with self.subTest(words=words, optional=optional):
                    client = LocalClient()
                    # Block the optional TV-binding discovery at the network boundary.
                    client.api = mock.Mock(side_effect=ott.Error("No television binding"))
                    status = copy.deepcopy(ordinary)
                    if optional is not None:
                        status["local_source_diagnostics"] = copy.deepcopy(optional)
                    client.status_transform = lambda _value, _target: status
                    output, errors = io.StringIO(), io.StringIO()
                    with mock.patch.object(ott, "read_json", return_value=client.config), \
                            mock.patch.object(ott, "Client", return_value=client), \
                            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        code = ott.main(["t1", *words])
                    text = output.getvalue()
                    self.assertEqual((code, errors.getvalue()), (0, ""))
                    self.assertEqual([call[1] for call in client.calls], ["status", "capabilities"])
                    for expected in ("Player summary:", "Awake", "Local videos", "23%", "46 (catalog ready)"):
                        self.assertIn(expected, text)
                    self.assertNotIn("Local source runtime:", text)
                    self.assertNotIn(SECRET, text)
                    self.assertNotIn(PRIVATE_URL, text)
                    if baseline is None:
                        baseline = text
                    else:
                        self.assertEqual(text, baseline)

    def test_absent_future_or_malformed_marker_never_requests_status(self):
        for marker in ("absent", None, {}, True, 1, {"version": True}, {"version": 1.0},
                       {"version": 0}, {"version": 2}, {"version": 1, "url": PRIVATE_URL}):
            with self.subTest(marker=marker):
                self.client = LocalClient(self.clock)
                def change(value, _target):
                    if marker == "absent":
                        value.pop("local_source_diagnostics")
                    else:
                        value["local_source_diagnostics"] = marker
                    return value
                self.client.cap_transform = change
                code, result = self.execute()
                self.assertEqual((code, result["verdict"]), (3, "unknown"))
                self.assertNotIn("data", result["observations"][0])
                self.assertEqual([c[1] for c in self.client.calls], ["capabilities"])

    def test_marker_does_not_require_inspect_support_or_native_agent_binding(self):
        def change(value, _target):
            value.pop("inspect")
            return value
        self.client.cap_transform = change
        self.assertEqual(self.execute()[0], 0)
        self.assertEqual([c[1] for c in self.client.calls], ["capabilities", "status"])

    def test_shared_deadline_exhaustion_prevents_status_and_reduces_remaining_budget(self):
        self.client.before = lambda client, _target, action: client.clock.sleep(21) if action == "capabilities" else None
        code, result = self.execute()
        self.assertEqual((code, result["verdict"]), (3, "unknown"))
        self.assertEqual([c[1] for c in self.client.calls], ["capabilities"])
        self.client = LocalClient(self.clock)
        self.client.before = lambda client, _target, action: client.clock.sleep(7) if action == "capabilities" else None
        self.assertEqual(self.execute()[0], 0)
        self.assertEqual([c[3] for c in self.client.calls], [20, 13])

    def test_status_timeout_or_rejection_is_not_retried_or_echoed(self):
        for failure in (ott.Error(SECRET), ott.PlayerRejected(SECRET, {"error": PRIVATE_URL}),
                        ott.PlayerUnsupported(SECRET, {"error": PRIVATE_URL})):
            with self.subTest(failure=type(failure).__name__):
                self.client = LocalClient(self.clock)
                def fail(_value, _target):
                    raise failure
                self.client.status_transform = fail
                code, result = self.execute()
                self.assertEqual((code, result["verdict"]), (3, "unknown"))
                self.assertEqual([c[1] for c in self.client.calls], ["capabilities", "status"])
                self.assertNotIn("data", result["observations"][0])

    def test_status_runtime_must_match_fresh_capabilities(self):
        for runtime in ("another-page", "", "Native_One", "x" * 65, PRIVATE_URL):
            with self.subTest(runtime=runtime):
                self.client = LocalClient(self.clock)
                self.client.status_transform = lambda v, _target: {**v, "local_source_diagnostics": sample(runtime)}
                code, result = self.execute()
                self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
        for runtime in ("Native_One", "n" * 65):
            self.client = LocalClient(self.clock)
            self.client.runtimes["web"] = runtime
            self.client.cap_transform = lambda value, _target: {**value, "native_playback_diagnostics": {"version": 1}}
            code, _ = self.execute()
            self.assertEqual(code, 3)

    def test_future_or_missing_block_never_exports_raw_status(self):
        for value in ({"token": SECRET}, {"local_source_diagnostics": None},
                      {"local_source_diagnostics": {**sample(), "version": 2}, "url": PRIVATE_URL}):
            self.client = LocalClient(self.clock)
            self.client.status_transform = lambda _v, _target, value=value: value
            code, result = self.execute()
            self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))

    def test_descriptor_combinations_preserve_measured_unknown_and_unmapped_match(self):
        for kind in ("direct", "compatibility"):
            for match in ("match", "mismatch", "unknown"):
                pairs = [(None, None)]
                if match == "match":
                    pairs.extend([("a" * 32, None), ("a" * 32, "b" * 32)])
                for item, source in pairs:
                    value = sample()
                    value["currentSource"] = descriptor(kind, match, item, source)
                    self.assertEqual(wb.local_source_metadata({"local_source_diagnostics": value}, RUNTIME), value)
        for kind in ("empty", "blob", "other", "unavailable"):
            value = sample()
            value["currentSource"] = descriptor(kind, "unknown" if kind == "unavailable" else "not_applicable")
            self.assertEqual(wb.local_source_metadata({"local_source_diagnostics": value}, RUNTIME), value)
        for field in ("generation", "handleId"):
            value = sample(); value[field] = None
            self.assertEqual(wb.local_source_metadata({"local_source_diagnostics": value}, RUNTIME), value)
        for hint in (None, "", "maybe", "probably"):
            value = sample(); value["codecs"] = {"h264": hint, "hevc": hint}
            self.assertEqual(wb.local_source_metadata({"local_source_diagnostics": value}, RUNTIME), value)

    def test_strict_metadata_rejects_bad_types_bounds_unknown_fields_and_private_identity(self):
        mutations = [lambda d: d.update(url=PRIVATE_URL), lambda d: d.pop("handleId"),
                     lambda d: d.update(version=True), lambda d: d.update(version=1.0),
                     lambda d: d.update(consistent=1), lambda d: d.update(codecs={"h264": "probably"}),
                     lambda d: d["codecs"].update(hevc=PRIVATE_URL),
                     lambda d: d["codecs"].update(extra="maybe"),
                     lambda d: d["requestedSource"].update(url=PRIVATE_URL),
                     lambda d: d["requestedSource"].update(itemId=PRIVATE_URL),
                     lambda d: d["requestedSource"].update(sourceId="B" * 32),
                     lambda d: d["requestedSource"].update(itemId=None),
                     lambda d: d["requestedSource"].update(endpointMatch="mismatch"),
                     lambda d: d["requestedSource"].update(endpointMatch="not_applicable"),
                     lambda d: d["currentSource"].update(endpointMatch="unknown"),
                     lambda d: d["requestedSource"].update(kind="blob"),
                     lambda d: d.update(currentSource=[]),
                     lambda d: d.update(mediaErrorCode=0), lambda d: d.update(mediaErrorCode=5)]
        for field in ("capturedAt", "generation", "handleId", "mediaErrorCode"):
            for value in (True, 1.0, "1", -1, MAX_INTEGER + 1, float("nan"), float("inf")):
                mutations.append(lambda d, field=field, value=value: d.update({field: value}))
        for mutate in mutations:
            value = sample(); mutate(value)
            with self.subTest(value=value), self.assertRaises(wb.InvalidData):
                wb.local_source_metadata({"local_source_diagnostics": value}, RUNTIME)
        value = sample(); value["private"] = SECRET * 300
        with self.assertRaises(wb.InvalidData):
            wb.local_source_metadata({"local_source_diagnostics": value}, RUNTIME)

    def test_inconsistent_snapshot_must_clear_all_identity_and_capability_evidence(self):
        value = inconsistent()
        self.assertEqual(wb.local_source_metadata({"local_source_diagnostics": value}, RUNTIME), value)
        mutations = [lambda d: d.update(generation=0), lambda d: d.update(handleId=0),
                     lambda d: d.update(mediaErrorCode=1), lambda d: d["codecs"].update(h264=""),
                     lambda d: d.update(currentSource=descriptor()),
                     lambda d: d.update(requestedSource=descriptor("direct", "match"))]
        for mutate in mutations:
            value = inconsistent(); mutate(value)
            with self.assertRaises(wb.InvalidData):
                wb.local_source_metadata({"local_source_diagnostics": value}, RUNTIME)
        self.client.status_transform = lambda value, _target: {**value, "local_source_diagnostics": inconsistent()}
        code, result = self.execute()
        self.assertEqual((code, result["verdict"]), (3, "unknown"))
        self.assertFalse(result["observations"][0]["data"]["consistent"])

    def test_maximum_integer_and_zero_values_remain_exact(self):
        for number in (0, MAX_INTEGER):
            value = sample()
            value.update(capturedAt=number, generation=number, handleId=number)
            self.assertEqual(wb.local_source_metadata({"local_source_diagnostics": value}, RUNTIME), value)

    def test_plain_output_distinguishes_attached_assigned_and_codec_hints(self):
        code, text = self.execute(json_output=False)
        self.assertEqual(code, 0)
        for phrase in ("Attached: empty", "Assigned: direct", "NOT OBSERVED", "H.264 capability hint: probably",
                       "HEVC capability hint: maybe", "do not confirm that this file can play"):
            self.assertIn(phrase, text)
        self.assertNotIn('"capturedAt"', text)
        self.client.status_transform = lambda value, _target: {**value, "local_source_diagnostics": {
            **sample(), "codecs": {"h264": "", "hevc": None}}}
        _, text = self.execute(json_output=False)
        self.assertIn("H.264 capability hint: unsupported", text)
        self.assertIn("HEVC capability hint: NOT OBSERVED", text)

    def test_incompatible_options_fail_before_any_rpc(self):
        for extra in (["--engine"], ["--provider"], ["--native-playback"], ["--lane", "native"],
                      ["--lane", "auto"], ["--out", "/unused"], ["extra"]):
            with self.subTest(extra=extra):
                code, result = self.execute(["debug", "--local-source", *extra])
                self.assertEqual((code, result["reason"]), (1, "invalid_arguments"))
                self.assertEqual(self.client.calls, [])

    def test_existing_debug_keeps_its_inspect_contract_when_new_marker_is_present(self):
        client = DebugClient(self.clock)
        client.capabilities["local_source_diagnostics"] = {"version": 1}
        code, result = self.execute(["debug"], client=client)
        self.assertEqual(code, 0)
        self.assertEqual(result["observations"][0]["data"], client.debug)
        self.assertEqual([c[1] for c in client.calls], ["capabilities", "inspect"])
        self.assertEqual(client.calls[-1][2]["section"], "debug")

    def test_help_advertises_passive_command_only_when_supported(self):
        for present in (False, True):
            caps = capabilities()
            if present:
                caps["local_source_diagnostics"] = {"version": 1}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                ott.print_player_help({"capabilities": ott.capabilities_metadata(caps)}, "t1")
            self.assertEqual("ott t1 debug --local-source" in output.getvalue(), present)

    def test_groups_dispatch_in_parallel_and_isolate_runtime_and_failure(self):
        self.client.barrier = threading.Barrier(2)
        def mutate(value, target):
            if target == "web2":
                value["local_source_diagnostics"]["runtime"] = RUNTIME
            return value
        self.client.status_transform = mutate
        args = SimpleNamespace(words=["tt", "debug", "--local-source"], json=True,
                               timeout=20, config="/unused")
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.player_group_command(self.client, self.client.config,
                                            [("t1", "web"), ("t2", "web2")], args, [])
        result = json.loads(output.getvalue())
        self.assertNotEqual(code, 0)
        self.assertEqual((result["succeeded"], result["failed"]), (1, 1))
        self.assertEqual([r["player"] for r in result["results"]], ["t1", "t2"])
        self.assertEqual(result["results"][0]["data"]["observations"][0]["data"], sample())
        for child in self.client.children:
            self.assertEqual([c[1] for c in child.calls], ["capabilities", "status"])
            self.assertEqual(child.timeout, 45)
        self.assertNotIn(SECRET, output.getvalue() + errors.getvalue())


if __name__ == "__main__":
    unittest.main()
