"""Queue state v2 negotiates a read contract without enlarging write requests."""
import contextlib
import copy
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_plex_queue_state_v2", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ott)
CONTRACT = json.loads((ROOT / "contracts/plex-queue-state-v2.json").read_text())
OPERATIONS = ["play", "status", "next", "previous", "stop"]
IDS = ["11", "12", "11"]


def caps(version=2, runtime="page-123"):
    result = {
        "version": 1, "player": {"version": "test", "platform": "tauri", "runtime": runtime},
        "lifecycle": [], "input": [], "playback": ["next_channel", "previous_channel"],
        "plex_queue": {"version": 1, "operations": [*OPERATIONS, "preview"], "max_items": 500},
        "plex_library": {"version": 1, "operations": ["list", "play", "preview"], "max_items": 500},
    }
    if version == 2:
        result["plex_queue_state_v2"] = copy.deepcopy(CONTRACT["capability"])
    return result


def state(version=2, ids=None, runtime="page-123", index=0):
    ids = list(IDS if ids is None else ids)
    return {"version": version, "runtime": runtime, "active": bool(ids),
            "state": "playing" if ids else "idle", "ids": ids,
            "index": index if ids else None, "repeat": "none", "order": "listed"}


class QueueStateV2Tests(unittest.TestCase):
    def run_cli(self, words, responses):
        client = mock.Mock(timeout=45)
        client.device.return_value = "dev_tv"
        client.call.side_effect = responses
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "Client", return_value=client), \
                mock.patch.object(ott, "read_json", return_value={}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(["--json", "tv", *words])
        self.assertEqual(client.timeout, 45)
        return code, output.getvalue(), errors.getvalue(), client.call.call_args_list

    def test_producer_fixture_keeps_v1_and_exact_v2_limits(self):
        self.assertEqual(CONTRACT["capability"], {
            "version": 2, "operations": OPERATIONS, "max_write_items": 500,
            "max_state_items": 1000, "max_state_bytes": 32768,
        })
        self.assertEqual(CONTRACT["legacy_state_max_items"], 500)
        self.assertEqual(CONTRACT["legacy_state_max_bytes"], 16384)
        self.assertEqual(CONTRACT["preview_and_library_version"], 1)
        self.assertEqual(CONTRACT["state_request_field"], "state_version")
        self.assertEqual(ott.MAX_PLEX_QUEUE_ITEMS, 500)
        self.assertEqual(ott.MAX_PLEX_QUEUE_STATE_ITEMS, 1000)
        self.assertEqual(ott.MAX_PLEX_QUEUE_STATE_BYTES, 32768)
        self.assertEqual(ott.PLEX_QUEUE_STATE_OPERATIONS, set(OPERATIONS))
        self.assertEqual(ott.plex_queue_metadata(CONTRACT["idle_response"], expected_version=2),
                         CONTRACT["idle_response"])
        # Default consumers remain v1 even for an empty v2 state.
        with self.assertRaises(ott.Error):
            ott.plex_queue_metadata(CONTRACT["idle_response"])

    def test_full_marker_is_required_and_numeric_booleans_are_not_versions(self):
        malformed = [None, {}, dict(CONTRACT["capability"], extra=True)]
        for key in CONTRACT["capability"]:
            missing = copy.deepcopy(CONTRACT["capability"])
            del missing[key]
            malformed.append(missing)
        for key, values in {
            "version": [True, 2.0, 1, "2"],
            "max_write_items": [True, 500.0, 499, 1000],
            "max_state_items": [True, 1000.0, 500, 1001],
            "max_state_bytes": [True, 32768.0, 16384, 32769],
            "operations": [OPERATIONS[:-1], [*OPERATIONS, "preview"],
                           [*OPERATIONS[:-1], "play"], [*OPERATIONS[:-1], False], "status"],
        }.items():
            malformed.extend(dict(CONTRACT["capability"], **{key: value}) for value in values)
        for marker in malformed:
            with self.subTest(marker=marker):
                value = caps()
                value["plex_queue_state_v2"] = marker
                with self.assertRaises(ott.Error):
                    ott.capabilities_metadata(value)
        for base in (None, {"version": 1, "operations": OPERATIONS, "max_items": 499},
                     {"version": 1, "operations": ["status"], "max_items": 500}):
            value = caps()
            if base is None:
                del value["plex_queue"]
            else:
                value["plex_queue"] = base
            with self.subTest(base=base), self.assertRaises(ott.Error):
                ott.capabilities_metadata(value)
        reversed_caps = caps()
        reversed_caps["plex_queue_state_v2"]["operations"].reverse()
        self.assertEqual(ott.capabilities_metadata(caps()), ott.capabilities_metadata(reversed_caps))

    def test_state_versions_and_item_limits_are_independent_of_write_limit(self):
        for version, maximum in ((1, 500), (2, 1000)):
            for count in (0, 1, maximum):
                value = state(version, [str(i + 1) for i in range(count)])
                with self.subTest(version=version, count=count):
                    self.assertEqual(ott.plex_queue_metadata(value, expected_version=version), value)
            with self.assertRaises(ott.Error):
                ott.plex_queue_metadata(state(version, ["1"] * (maximum + 1)), expected_version=version)
            for bad in (True, float(version), str(version), 3 - version):
                with self.subTest(version=version, actual=bad), self.assertRaises(ott.Error):
                    ott.plex_queue_metadata(dict(state(version), version=bad), expected_version=version)
        for requested in (True, 2.0, "2", None, 3):
            with self.subTest(requested=requested), self.assertRaises(ott.Error):
                ott.plex_queue_metadata(state(), expected_version=requested)
        for operation in ("play", "preview"):
            self.assertEqual(len(ott.parse_command(["plex", operation, *(["1"] * 500)])[1]["ids"]), 500)
            code, out, _, calls = self.run_cli(["plex", operation, *(["1"] * 501)], [])
            self.assertEqual((code, out, calls), (1, "", []))

    def test_duplicates_order_index_and_state_semantics_are_preserved(self):
        value = state(ids=["11", "12", "11"] * 333 + ["11"], index=998)
        decoded = ott.plex_queue_metadata(value, expected_version=2)
        self.assertEqual(decoded, value)
        self.assertIsNot(decoded["ids"], value["ids"])
        bad_fields = [
            {"ids": ["0"]}, {"ids": ["01"]}, {"ids": [1]}, {"ids": [True]},
            {"ids": ["1" * 21]}, {"ids": ["１２"]}, {"ids": ["1\n"]},
            {"index": True}, {"index": -1}, {"index": len(IDS)}, {"index": None},
            {"active": 1}, {"active": False}, {"state": "idle"}, {"state": "ended"},
            {"state": "unknown"}, {"repeat": "one"}, {"order": "random"},
            {"runtime": "page-other"}, {"runtime": "https://private.example"},
            {"title": "x" * 513}, {"title": "a\nb"}, {"extra": True},
        ]
        for fields in bad_fields:
            with self.subTest(fields=fields), self.assertRaises(ott.Error):
                ott.plex_queue_metadata(dict(state(), **fields), "page-123", expected_version=2)
        self.assertEqual(ott.plex_queue_metadata(dict(state(index=2), state="ended", repeat="all"),
                                                expected_version=2)["index"], 2)
        with self.assertRaises(ott.Error):
            ott.plex_queue_metadata(state(ids=["11", "11", "12"]), "page-123",
                                    {"op": "play", "ids": IDS}, expected_version=2)
        with self.assertRaises(ott.Error):
            ott.plex_queue_metadata(state(index=1), params={"op": "play", "ids": IDS}, expected_version=2)
        with self.assertRaises(ott.Error):
            ott.plex_queue_metadata(state(), params={"op": "stop"}, expected_version=2)

    def test_decoded_canonical_size_is_checked_without_claiming_raw_transport_validation(self):
        value = dict(state(), title="🎬" * 128)
        canonical_bytes = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        with mock.patch.object(ott, "MAX_PLEX_QUEUE_STATE_BYTES", canonical_bytes):
            self.assertEqual(ott.plex_queue_metadata(value, expected_version=2), value)
        with mock.patch.object(ott, "MAX_PLEX_QUEUE_STATE_BYTES", canonical_bytes - 1), \
                self.assertRaises(ott.Error):
            ott.plex_queue_metadata(value, expected_version=2)
        # Raw whitespace/escape limits are enforced by the relay before decoding.
        # The CLI must not mistake a decoded object's repr for the wire byte count.
        padded_wire = " " * 32768 + json.dumps(value)
        self.assertGreater(len(padded_wire.encode("utf-8")), 32768)
        self.assertEqual(ott.plex_queue_metadata(json.loads(padded_wire), expected_version=2), value)
        for unencodable in (dict(value, title="\ud800"), dict(value, unexpected=float("nan"))):
            with self.assertRaises(ott.Error):
                ott.plex_queue_metadata(unencodable, expected_version=2)

    def test_wire_decoder_rejects_duplicate_fields_before_version_validation(self):
        valid = json.dumps({"data": state()}, separators=(",", ":"))
        malformed = [valid.replace('"version":2', '"version":1,"version":2'),
                     valid.replace('"index":0', '"index":1,"index":0'),
                     json.dumps({"data": caps()}, separators=(",", ":")).replace(
                         '"max_state_items":1000', '"max_state_items":500,"max_state_items":1000')]
        for raw in malformed:
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "Duplicate"):
                ott.decode_response_json(raw)
        self.assertEqual(ott.decode_response_json(valid)["data"]["ids"], IDS)

    def test_each_eligible_operation_requires_two_reads_and_one_versioned_request(self):
        for operation in OPERATIONS:
            words = ["plex", operation, *(IDS if operation == "play" else [])]
            result = state(ids=[] if operation == "stop" else IDS)
            params = {"op": operation, "runtime": "page-123", "state_version": 2}
            if operation == "play":
                params["ids"] = IDS
            confirmed = caps()
            confirmed["plex_queue_state_v2"]["operations"].reverse()
            with self.subTest(operation=operation):
                code, out, err, calls = self.run_cli(words, [caps(), confirmed, result])
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(json.loads(out), result)
                self.assertEqual(calls, [mock.call("dev_tv", "capabilities", {}),
                                        mock.call("dev_tv", "capabilities", {"plex_queue_state_v2": True}),
                                        mock.call("dev_tv", "plex_queue", params)])

    def test_explicit_reads_and_steps_return_full_1000_item_state_without_truncating(self):
        for operation in ("status", "next", "previous"):
            value = state(ids=["11", "12"] * 500, index=999)
            code, out, err, calls = self.run_cli(["plex", operation], [caps(), caps(), value])
            with self.subTest(operation=operation):
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(json.loads(out), value)
                self.assertEqual(len(calls), 3)

    def test_legacy_state_preview_library_and_generic_channel_receipts_remain_v1(self):
        preview = {"version": 1, "runtime": "page-123", "state": "ready", "ids": IDS,
                   "titles": ["One", "Two", "One"], "order": "listed"}
        library = {"version": 1, "runtime": "page-123", "op": "play", "state": "ready",
                   "library": {"id": "7", "title": "Test collection", "type": "show"},
                   "shuffled": True, "queue": state(1)}
        cases = [(["plex", "status"], [caps(1), state(1)], "plex_queue"),
                 (["plex", "preview", *IDS], [caps(), preview], "plex_queue"),
                 (["plex", "shuffle", "Test collection"], [caps(), library], "plex_library")]
        for words, responses, action in cases:
            with self.subTest(words=words):
                code, out, err, calls = self.run_cli(words, responses)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(json.loads(out), responses[-1])
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0], mock.call("dev_tv", "capabilities", {}))
                self.assertEqual(calls[1].args[1], action)
                self.assertNotIn("state_version", calls[1].args[2])
        for words, operation in ((["next"], "next_channel"), (["prev"], "previous_channel")):
            receipt = {"operation": operation, "dispatched": True,
                       "channel": {"id": "channel-2", "number": 2, "name": "Test TV"}}
            code, out, err, calls = self.run_cli(words, [receipt])
            self.assertEqual((code, err, json.loads(out)), (0, "", receipt))
            self.assertEqual(calls, [mock.call("dev_tv", "playback", {"operation": operation})])
        with self.assertRaises(ott.Error):
            ott.plex_queue_metadata(preview, params={"op": "preview", "ids": IDS}, expected_version=2)
        with self.assertRaises(ott.Error):
            ott.plex_library_metadata(dict(library, queue=state(2)), "page-123",
                                      {"op": "play", "query": "Test collection", "shuffle": True}, 500)

    def test_probe_failure_or_changed_capabilities_never_sends_a_queue_request(self):
        changed = caps(runtime="page-new")
        absent = caps(1)
        malformed = caps()
        malformed["plex_queue_state_v2"]["max_state_items"] = 500
        denied = ott.HTTPError(400)
        for response in (denied, changed, absent, malformed, ott.PlayerUnsupported("unsupported")):
            with self.subTest(response=type(response).__name__):
                code, out, err, calls = self.run_cli(["plex", "next"], [caps(), response])
                self.assertEqual((code, out, len(calls)), (1, "", 2))
                self.assertTrue(err)
                self.assertTrue(all(call.args[1] == "capabilities" for call in calls))
                if response is denied:
                    self.assertIn("controller does not support Plex queue state v2", err)
                    self.assertIn("No queue command was sent", err)

    def test_failures_after_dispatch_never_retry_or_downgrade(self):
        failures = [state(1), state(runtime="page-other"), dict(state(), ids=["1"] * 1001),
                    ott.PlayerRejected("private-never-print", {"error": "private-never-print"}),
                    ott.PlayerRejected("private-never-print", {"error": ott.PLEX_QUEUE_UPGRADE_ERROR}),
                    ott.HTTPError(400),
                    ott.RequestError("request_pending_timeout", "receipt", "Acceptance is uncertain"),
                    ott.PlayerUnsupported("private-never-print")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                code, out, err, calls = self.run_cli(["plex", "next"], [caps(), caps(), failure])
                self.assertEqual((code, out, len(calls)), (1, "", 3))
                self.assertEqual(sum(call.args[1] == "plex_queue" for call in calls), 1)
                self.assertEqual(calls[-1].args[2]["state_version"], 2)
                self.assertNotIn("private-never-print", err)

    def test_both_reads_and_mutation_share_one_deadline_and_restore_client_timeout(self):
        for costs, expected_calls, error in [
            ([2, 3, 4], 3, None), ([10], 1, "no queue command"),
            ([2, 8], 2, "no queue command"), ([2, 3, 5], 3, "uncertain"),
        ]:
            with self.subTest(costs=costs):
                now, budgets = [0.0], []
                client = mock.Mock(timeout=67)
                responses = [caps(), caps(), state()]
                def call(device, action, params, *, budgets=budgets, client=client,
                         now=now, costs=costs, responses=responses):
                    index = len(budgets)
                    budgets.append(client.timeout)
                    now[0] += costs[index]
                    return responses[index]
                client.call.side_effect = call
                with mock.patch.object(ott.time, "monotonic", side_effect=lambda now=now: now[0]):
                    if error:
                        with self.assertRaisesRegex(ott.Error, error):
                            ott.plex_queue_command(client, "dev_tv", {"op": "next"}, 10)
                    else:
                        self.assertEqual(ott.plex_queue_command(client, "dev_tv", {"op": "next"}, 10), state())
                self.assertEqual(client.call.call_count, expected_calls)
                self.assertEqual(budgets, [10 - sum(costs[:i]) for i in range(expected_calls)])
                self.assertEqual(client.timeout, 67)


class MixedGroupQueueStateV2Tests(unittest.TestCase):
    def test_real_entry_point_negotiates_each_worker_without_serializing_the_group(self):
        lock, peers_dispatched = threading.Lock(), threading.Event()
        records, workers, fast_players = [], {}, set()
        barrier_results = []

        class GroupClient(ott.Client):
            def __init__(self):
                self.config = {"players": {f"t{i}": f"dev_{i}" for i in range(1, 5)}}
                self.credentials = {"devices": [{"id": value} for value in self.config["players"].values()]}
                self.server, self.timeout, self.on_receipt = "https://controller.example", 45, None

            def call(self, device, action, params):
                with lock:
                    records.append((device, action, copy.deepcopy(params)))
                    workers[device] = self
                runtime = device.replace("_", "-")
                if action == "capabilities":
                    if device == "dev_1" and not params:
                        barrier_results.append(peers_dispatched.wait(5))
                    if device == "dev_3" and params:
                        raise ott.HTTPError(400)
                    return caps(1 if device == "dev_2" else 2, runtime)
                if action == "plex_queue":
                    with lock:
                        fast_players.add(device)
                        if {"dev_2", "dev_4"} <= fast_players:
                            peers_dispatched.set()
                    version = 1 if device == "dev_2" else 2
                    return state(version, runtime=runtime)
                raise AssertionError((device, action, params))

        client = GroupClient()
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "Client", return_value=client), \
                mock.patch.object(ott, "read_json", return_value=client.config), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(["--json", "t1,t2,t3,t4", "plex", "next"])
        self.assertEqual((code, errors.getvalue()), (1, ""))
        self.assertEqual(barrier_results, [True], "T1 blocked both independent peer mutations")
        rows = json.loads(output.getvalue())["results"]
        self.assertEqual([row["player"] for row in rows], ["t1", "t2", "t3", "t4"])
        self.assertEqual([row["ok"] for row in rows], [True, True, False, True])
        self.assertIn("controller does not support Plex queue state v2", rows[2]["error"])
        self.assertEqual([row["data"]["version"] for row in rows if row["ok"]], [2, 1, 2])
        for device, worker in workers.items():
            calls = [(action, params) for target, action, params in records if target == device]
            expected = [("capabilities", {})]
            if device != "dev_2":
                expected.append(("capabilities", {"plex_queue_state_v2": True}))
            if device != "dev_3":
                params = {"op": "next", "runtime": device.replace("_", "-")}
                if device != "dev_2":
                    params["state_version"] = 2
                expected.append(("plex_queue", params))
            self.assertEqual(calls, expected)
            self.assertEqual(worker.timeout, 45)
        self.assertEqual(len({id(worker) for worker in workers.values()}), 4)
        self.assertEqual(len({id(worker.opener) for worker in workers.values()}), 4)
        self.assertEqual(client.timeout, 45)


if __name__ == "__main__":
    unittest.main()
