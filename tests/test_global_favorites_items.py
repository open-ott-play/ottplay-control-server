"""Synthetic protocol and fake-clock transport tests; no player or disk state."""

import copy
import importlib.util
import json
import sys
import unittest
from itertools import pairwise
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "cli"), *sys.path]):
    spec = importlib.util.spec_from_file_location("global_items_test", ROOT / "cli/global_favorites_items.py")
    items = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = items
    spec.loader.exec_module(items)
FIX = json.loads((ROOT / "docs/design/favorites-global-items-v1.fixtures.json").read_bytes())
RUNTIME = FIX["list_request"]["runtime"]
RID = "a" * 32
REVISION, CONTEXT = "1" * 64, "2" * 64


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def checked(request, data, **kwargs):
    return items.validate_result(request, data, request_id=RID, response_id=RID, status="ok", **kwargs)


def row(index):
    return {"id": f"{index:08x}-0000-4000-8000-000000000001", "entry": {
        "title": f"Item {index}", "origin": {"provider": "m3u", "profile": "Example", "subprofile": None,
                                                "sourceAlias": "Main"},
        "locator": {"kind": "channel", "channelId": f"item-{index}"}}}


def page(rows, *, total=None, offset=0, cursor=None, next_cursor=None):
    return {"version": 1, "runtime": RUNTIME, "op": "list", "subject": "global", "revision": REVISION,
            "context_revision": CONTEXT, "cursor": cursor, "page_offset": offset,
            "total": len(rows) if total is None else total, "entries": copy.deepcopy(rows),
            "next_cursor": next_cursor, "unresolved_legacy_count": 0}


class ProtocolTests(unittest.TestCase):
    def error(self, callback, *args, **kwargs):
        with self.assertRaises(items.ProtocolError) as result:
            callback(*args, **kwargs)
        self.assertNotIn("fixture-secret", str(result.exception))

    def test_frozen_examples_schema_fields_and_unverified_results(self):
        schema = json.loads((ROOT / "docs/design/favorites-global-items-v1.schema.json").read_bytes())["$defs"]
        for request, response, definition in (("list_request", "list_result", "list_result"),
                                               ("current_request", "current_result", "current_available"),
                                               ("current_request", "current_unavailable", "current_unavailable"),
                                               ("play_request", "play_result", "play_result")):
            with self.subTest(response=response):
                self.assertEqual(set(schema[request]["required"]), set(FIX[request]))
                self.assertEqual(set(schema[definition]["required"]), set(FIX[response]))
                value = checked(FIX[request], FIX[response])
                self.assertEqual(value.data, FIX[response])
                self.assertFalse(value.verified)
                encoded = raw({"id": RID, "status": "ok", "data": FIX[response]})
                self.assertEqual(items.validate_response(FIX[request], encoded, request_id=RID).data, value.data)

    def test_capability_and_exact_request_boundaries(self):
        self.assertEqual(items.validate_items_capability({"version": 1}), {"version": 1})
        for value in (None, {"version": True}, {"version": 2}, {"version": 1, "max_entries": 1000}):
            self.error(items.validate_capability, value)
        for name in ("list_request", "current_request", "play_request"):
            for change in ({"subject": "scoped"}, {"runtime": "UPPER"}, {"op": "remove"},
                           {"path": "fixture-secret"}, {"version": 1}):
                self.error(items.validate_request, {**FIX[name], **change})
        for count in (0, 101, True, 1.0, "100", None):
            self.error(items.validate_request, {**FIX["list_request"], "max_entries": count})
        for cursor in ("", "a" * 513, "fixture-secret://token", "c\n", 1, {}, []):
            self.error(items.validate_request, {**FIX["list_request"], "cursor": cursor})
        self.assertEqual(items.validate_request({**FIX["list_request"], "cursor": "_-a9"})["cursor"], "_-a9")
        for key in ("id", "expected_revision", "expected_context_revision"):
            self.error(items.validate_request, {**FIX["play_request"], key: "bad\n"})

    def test_original_json_parser_rejects_duplicates_unicode_nonfinite_and_size(self):
        request = raw(FIX["current_request"])
        for bad in (request.replace(b'"current"', b'"current","op":"current"'),
                    request.replace(RUNTIME.encode(), b'\\ud800'), request.replace(RUNTIME.encode(), b'bad\\n'),
                    request[:-1] + b',"extra":1e400}', request[:-1] + b',"extra":NaN}',
                    b'\xef\xbb\xbf' + request, request[:-1] + b',"extra":"\xff"}',
                    request + b' ' * 16384):
            self.error(items.validate_request, bad)
        data = raw(FIX["list_result"])
        for bad in (data.replace(b'"version":1', b'"version":1,"version":1'),
                    data.replace(b'Example item', b'\\ud800'), b'[' * 20 + data + b']' * 20):
            self.error(checked, FIX["list_request"], bad)

    def test_raw_data_exact_64k_limit_and_negative_padding(self):
        for status, data in (("ok", FIX["play_result"]), ("rejected", FIX["error"])):
            encoded = raw(data)
            padded = b'{' + b' ' * (65536 - len(encoded)) + encoded[1:]
            for wrapper in (b'{"id":"' + RID.encode() + b'","status":"' + status.encode() + b'","data":' + padded + b'}',
                            b'{"data":' + padded + b',"status":"' + status.encode() + b'","id":"' + RID.encode() + b'"}'):
                value = items.validate_response(FIX["play_request"], wrapper, request_id=RID)
                self.assertEqual(value.data, data)
                self.assertFalse(value.verified)
                self.error(items.validate_response, FIX["play_request"], wrapper.replace(padded, b'{ ' + padded[1:]), request_id=RID)
                self.error(items.validate_response, FIX["play_request"], wrapper, request_id=RID, max_metadata_bytes=65535)
        for bound in (True, 0, 65537, "65536"):
            self.error(checked, FIX["list_request"], FIX["list_result"], max_metadata_bytes=bound)
        self.error(items.validate_response, FIX["list_request"], b' ' * items.MAX_RESPONSE_BYTES + raw({}), request_id=RID)

    def test_request_id_runtime_op_version_and_status_fencing(self):
        for change in ({"runtime": "other"}, {"subject": "scoped"}, {"op": "play"}, {"version": True},
                       {"private": "fixture-secret"}):
            self.error(checked, FIX["list_request"], {**FIX["list_result"], **change})
        self.error(items.validate_result, FIX["list_request"], FIX["list_result"], request_id=RID,
                   response_id="b" * 32, status="ok")
        self.error(items.validate_result, FIX["list_request"], FIX["list_result"], request_id=RID,
                   response_id=RID, status="rejected")
        self.error(checked, FIX["list_request"], FIX["list_result"], scope_validator=lambda _: True)
        self.error(checked, FIX["list_request"], FIX["list_result"], context={})

    def test_exact_errors_never_grant_playback_or_storage_proof(self):
        for code in items.ERRORS:
            data = {**FIX["error"], "error": code}
            value = items.validate_result(FIX["play_request"], data, request_id=RID, response_id=RID, status="rejected")
            self.assertFalse(value.verified)
        for change in ({"error": "fixture-secret://token"}, {"dispatched": True}, {"reason": "fixture-secret"}):
            self.error(items.validate_result, FIX["play_request"], {**FIX["error"], **change}, request_id=RID,
                       response_id=RID, status="rejected")
        self.error(checked, FIX["play_request"], FIX["error"])

    def test_all_locator_kinds_and_portable_rules_are_reused(self):
        examples = [row(1)["entry"], FIX["current_result"]["entry"]]
        for provider, locator in (("m3u", {"kind": "channel-name", "name": "Канал 😀", "group": None}),
                                  ("local", {"kind": "local-media", "rootAlias": "Videos", "relativePath": "Series/part.mp4"}),
                                  ("vportal", {"kind": "vportal-media", "idField": "fid", "id": "episode-1"})):
            value = row(1)["entry"]
            value["origin"]["provider"], value["locator"] = provider, locator
            examples.append(value)
        for entry in examples:
            checked(FIX["current_request"], {**FIX["current_result"], "entry": entry})
        for provider, locator in (("m3u", {"kind": "channel", "channelId": "channel:12"}),
                                  ("plex", {"kind": "channel", "channelId": "x"}),
                                  ("local", {"kind": "local-media", "rootAlias": "Videos", "relativePath": "../fixture-secret"})):
            value = row(1)["entry"]
            value["origin"]["provider"], value["locator"] = provider, locator
            self.error(checked, FIX["current_request"], {**FIX["current_result"], "entry": value})
        for title in ("\ud800", "😀" * 513, "\ufeffname", "fixture-secret://token"):
            value = {**row(1)["entry"], "title": title}
            self.error(checked, FIX["current_request"], {**FIX["current_result"], "entry": value})

    def test_list_empty_adaptive_and_duplicate_boundaries(self):
        checked(FIX["list_request"], page([]))
        checked(FIX["list_request"], page([row(i) for i in range(100)]))
        checked(FIX["list_request"], page([row(1)], total=1000, next_cursor="next"))
        duplicate = copy.deepcopy(row(1)); duplicate["id"] = row(2)["id"]
        duplicate["entry"]["title"] = "A different title is still the same identity"
        for rows in ([row(1)] * 2, [row(1), duplicate], [row(i) for i in range(101)]):
            self.error(checked, FIX["list_request"], page(rows))
        self.error(checked, {**FIX["list_request"], "max_entries": 1}, page([row(1), row(2)]))
        for change in ({"total": True}, {"total": 1001}, {"page_offset": 1}, {"unresolved_legacy_count": 101},
                       {"revision": None}, {"context_revision": "bad"}, {"cursor": "unknown"}):
            self.error(checked, FIX["list_request"], {**FIX["list_result"], **change})

    def test_next_cursor_and_offset_relations(self):
        request = {**FIX["list_request"], "cursor": "page1"}
        checked(request, page([row(1)], offset=1, total=3, cursor="page1", next_cursor="page2"))
        for change in ({"next_cursor": "page1"}, {"next_cursor": None}, {"page_offset": 0},
                       {"entries": []}, {"next_cursor": "x/unsafe"}, {"total": 1}):
            self.error(checked, request, {**page([row(1)], offset=1, total=3, cursor="page1", next_cursor="page2"), **change})
        self.error(checked, FIX["list_request"], {**FIX["list_result"], "next_cursor": "unused"})

    def test_current_null_reason_truth_table_and_immutable_payload(self):
        for reason in items.UNAVAILABLE:
            checked(FIX["current_request"], {**FIX["current_unavailable"], "unavailable_reason": reason})
        for change in ({"entry": None, "unavailable_reason": None}, {"unavailable_reason": "standby"},
                       {"id": FIX["play_request"]["id"]}, {"entry": {"url": "fixture-secret"}}):
            self.error(checked, FIX["current_request"], {**FIX["current_result"], **change})
        result = checked(FIX["current_request"], FIX["current_result"])
        result.data["entry"]["title"] = "changed"
        self.assertEqual(result.data, FIX["current_result"])

    def test_play_requires_exact_echo_and_true_dispatch(self):
        for key, value in (("id", row(999)["id"]), ("revision", "f" * 64), ("context_revision", "e" * 64),
                           ("dispatched", 1), ("dispatched", False), ("verified", True)):
            self.error(checked, FIX["play_request"], {**FIX["play_result"], key: value})


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, duration):
        self.now += duration


class API:
    server = "https://synthetic.invalid"
    def __init__(self, clock, count=1):
        self.clock, self.rows = clock, [row(i) for i in range(count)]
        self.calls, self.requests = [], {}
        self.page_size = 100
        self.hook = lambda request, data: data
        self.mode, self.pending = None, 0
        self.current_value = copy.deepcopy(FIX["current_result"])
    def api(self, path, payload, *, timeout):
        self.calls.append((self.clock(), "POST", copy.deepcopy(payload), timeout))
        if self.mode == "lost" and payload["params"]["op"] == "play":
            raise OSError("fixture-secret://token")
        if self.mode == "post429":
            error = OSError("fixture-secret"); error.code = 429; raise error
        identifier = f"{len(self.requests) + 1:032x}"
        self.requests[identifier] = copy.deepcopy(payload["params"])
        return 202, {"id": identifier, "status": "queued"}
    def api_raw(self, path, payload, *, timeout):
        self.calls.append((self.clock(), "GET", None, timeout))
        if self.pending:
            self.pending -= 1
            return 202, b'{"status":"pending"}'
        identifier = path.rsplit("id=", 1)[1]
        request = self.requests[identifier]
        if request["op"] == "list":
            offset = int(request["cursor"][1:]) if request["cursor"] else 0
            count = min(request["max_entries"], self.page_size)
            selected = self.rows[offset:offset + count]
            end = offset + len(selected)
            data = page(selected, offset=offset, total=len(self.rows), cursor=request["cursor"],
                        next_cursor=f"c{end}" if end < len(self.rows) else None)
        elif request["op"] == "current":
            data = copy.deepcopy(self.current_value)
        else:
            data = {"version": 1, "runtime": RUNTIME, "op": "play", "subject": "global", "id": request["id"],
                    "revision": request["expected_revision"], "context_revision": request["expected_context_revision"],
                    "dispatched": True}
        status = "ok"
        if self.mode == "reject" and request["op"] == "play":
            data = {"version": 1, "runtime": RUNTIME, "op": "play", "subject": "global", "error": "context_changed"}
            status = "rejected"
        data = self.hook(request, data)
        return 200, raw({"id": identifier, "status": status, "data": data})
    def ops(self):
        return [call[2]["params"]["op"] for call in self.calls if call[1] == "POST"]


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.api = API(self.clock)
        self.peer = self.make_peer()
    def make_peer(self, **kwargs):
        return items.ItemsPeer(self.api, "device", runtime=RUNTIME, monotonic=self.clock, sleep=self.clock.sleep, **kwargs)
    def test_zero_through_1000_complete_rows_under_one_typical_deadline(self):
        for count in (0, 1, 100, 101, 1000):
            with self.subTest(count=count):
                self.setUp(); self.api.rows = [row(i) for i in range(count)]
                listing = self.peer.list()
                self.assertEqual((listing.data["status"], listing.data["total"]), ("complete", count))
                self.assertEqual(listing.data["entries"], self.api.rows)
                self.assertEqual(listing.document, {"version": 1, "entries": [r["entry"] for r in self.api.rows]})
                self.assertEqual(len(self.api.ops()), max(1, (count + 99) // 100))
                self.assertLess(self.clock(), 45)
    def test_adaptive_short_pages_preserve_order_and_legacy_count(self):
        self.api.rows, self.api.page_size = [row(i) for i in range(5)], 2
        self.api.hook = lambda _, data: {**data, "unresolved_legacy_count": 3}
        listing = self.peer.list(max_entries=80)
        self.assertEqual(listing.data["entries"], self.api.rows)
        self.assertEqual(listing.data["unresolved_legacy_count"], 3)
        posts = [c for c in self.api.calls if c[1] == "POST"]
        self.assertEqual([c[2]["params"]["cursor"] for c in posts], [None, "c2", "c4"])
        self.assertTrue(all(b[0] - a[0] >= 1.5 for a, b in pairwise(posts)))
    def test_cross_page_revision_context_counts_and_offset_changes_fail_closed(self):
        for key, value in (("revision", "f" * 64), ("context_revision", "e" * 64), ("total", 4),
                           ("unresolved_legacy_count", 1), ("page_offset", 3)):
            with self.subTest(key=key):
                self.setUp(); self.api.rows, self.api.page_size = [row(i) for i in range(3)], 1
                self.api.hook = lambda request, data, key=key, value=value: ({**data, key: value} if request.get("cursor") else data)
                with self.assertRaises(items.RemoteError):
                    self.peer.list()
                self.assertEqual(self.api.ops(), ["list", "list"])
                self.assertIsNone(self.peer._listing)
    def test_cross_page_duplicate_id_or_identity_with_other_title_rejected(self):
        for duplicate_id in (False, True):
            self.setUp(); self.api.rows, self.api.page_size = [row(1), row(2)], 1
            if duplicate_id:
                self.api.rows[1]["id"] = self.api.rows[0]["id"]
            else:
                self.api.rows[1]["entry"] = {**self.api.rows[0]["entry"], "title": "Other title"}
            with self.assertRaises(items.RemoteError):
                self.peer.list()
            self.assertEqual(self.api.ops(), ["list", "list"])
    def test_opaque_cursor_cycle_never_loops(self):
        self.api.rows, self.api.page_size = [row(i) for i in range(4)], 1
        self.api.hook = lambda request, data: ({**data, "next_cursor": "c1"} if request.get("cursor") == "c2" else data)
        with self.assertRaises(items.RemoteError):
            self.peer.list()
        self.assertEqual(self.api.ops(), ["list"] * 3)
    def test_aggregate_document_limit_checked_before_retaining_all_pages(self):
        self.api.rows = [row(i) for i in range(150)]
        for i, value in enumerate(self.api.rows):
            value["entry"]["origin"]["provider"] = "local"
            value["entry"]["locator"] = {"kind": "local-media", "rootAlias": "Videos", "relativePath": str(i) + "a" * 8000}
        self.api.page_size = 1
        peer = self.make_peer(timeout=300)
        with self.assertRaises(items.RemoteError):
            peer.list()
        self.assertLess(len(self.api.ops()), 150)
        self.assertGreater(len(self.api.ops()), 100)
        self.assertIsNone(peer._listing)
    def test_current_reads_and_unavailable_do_not_send_other_operations(self):
        self.assertEqual(self.peer.current().data, FIX["current_result"])
        self.api.current_value = copy.deepcopy(FIX["current_unavailable"])
        value = self.peer.current()
        self.assertIsNone(value.data["entry"])
        self.assertFalse(value.verified)
        self.assertEqual(self.api.ops(), ["current", "current"])
    def test_play_requires_own_complete_listing_and_known_id(self):
        listing = self.peer.list()
        for candidate in (None, listing.data, items.Listing(listing._target, listing._data)):
            with self.assertRaises(items.RemoteError):
                self.peer.play(self.api.rows[0]["id"], candidate)
        with self.assertRaises(items.RemoteError):
            self.peer.play(row(999)["id"], listing)
        with self.assertRaises(items.RemoteError):
            self.make_peer().play(self.api.rows[0]["id"], listing)
        self.assertEqual(self.api.ops(), ["list"])
        result = self.peer.play(self.api.rows[0]["id"], listing)
        self.assertTrue(result.data["dispatched"])
        self.assertFalse(result.verified)
        self.assertEqual(self.api.ops(), ["list", "play"])
    def test_listing_external_mutations_cannot_change_play_identity_or_revisions(self):
        listing = self.peer.list()
        changed = listing.data
        changed["revision"] = "f" * 64
        changed["entries"][0]["id"] = row(999)["id"]
        listing.document["entries"][0]["title"] = "Changed"
        self.peer.play(self.api.rows[0]["id"], listing)
        sent = list(self.api.requests.values())[-1]
        self.assertEqual(sent["expected_revision"], REVISION)
        self.assertEqual(sent["id"], self.api.rows[0]["id"])
    def test_play_lost_reply_rejection_or_bad_echo_never_reposts(self):
        for mode in ("lost", "reject", "bad"):
            self.setUp(); listing = self.peer.list(); self.api.mode = mode
            if mode == "bad":
                self.api.hook = lambda _, data: {**data, "context_revision": "f" * 64}
            with self.assertRaises(items.RemoteError) as result:
                self.peer.play(self.api.rows[0]["id"], listing)
            self.assertNotIn("fixture-secret", str(result.exception))
            with self.assertRaises(items.RemoteError):
                self.peer.play(self.api.rows[0]["id"], listing)
            self.assertEqual(self.api.ops(), ["list", "play"])
    def test_explicit_new_complete_read_required_for_another_attempt(self):
        listing = self.peer.list()
        self.peer.play(self.api.rows[0]["id"], listing)
        next_listing = self.peer.list()
        with self.assertRaises(items.RemoteError):
            self.peer.play(self.api.rows[0]["id"], listing)
        self.peer.play(self.api.rows[0]["id"], next_listing)
        self.assertEqual(self.api.ops(), ["list", "play", "list", "play"])
    def test_failed_new_list_invalidates_previous_complete_read(self):
        listing = self.peer.list()
        self.api.rows, self.api.page_size = [row(i) for i in range(2)], 1
        self.api.hook = lambda request, data: ({**data, "revision": "f" * 64} if request.get("cursor") else data)
        with self.assertRaises(items.RemoteError):
            self.peer.list()
        with self.assertRaises(items.RemoteError):
            self.peer.play(row(0)["id"], listing)
        self.assertNotIn("play", self.api.ops())
    def test_one_deadline_for_all_pages_and_play(self):
        self.api.rows, self.api.page_size = [row(i) for i in range(5)], 1
        peer = self.make_peer(timeout=3)
        with self.assertRaises(items.RemoteError):
            peer.list()
        self.assertLessEqual(self.clock(), 3)
        self.assertLess(len(self.api.ops()), 5)
        self.assertIsNone(peer._listing)
        self.setUp(); listing = self.peer.list(); self.clock.now = 45
        with self.assertRaises(items.RemoteError):
            self.peer.play(row(0)["id"], listing)
        self.assertEqual(self.api.ops(), ["list"])
    def test_pending_receipt_polls_do_not_resend_request(self):
        self.api.pending = 2
        listing = self.peer.list()
        self.assertEqual(listing.data["total"], 1)
        self.assertEqual(self.api.ops(), ["list"])
        self.assertEqual(len([call for call in self.api.calls if call[1] == "GET"]), 3)
    def test_post_backpressure_not_replayed_and_target_change_prevents_send(self):
        self.api.mode = "post429"
        with self.assertRaises(items.RemoteError):
            self.peer.list()
        self.assertEqual(self.api.ops(), ["list"])
        self.setUp(); listing = self.peer.list(); self.api.server = "https://other.invalid"
        with self.assertRaises(items.RemoteError):
            self.peer.play(row(0)["id"], listing)
        self.assertEqual(self.api.ops(), ["list"])
    def test_no_reentrant_dispatch_from_transport_callback(self):
        listing = self.peer.list()
        def hook(_, data):
            with self.assertRaises(items.RemoteError):
                self.peer.play(row(0)["id"], listing)
            return data
        self.api.hook = hook
        self.peer.play(row(0)["id"], listing)
        self.assertEqual(self.api.ops(), ["list", "play"])


if __name__ == "__main__":
    unittest.main()
