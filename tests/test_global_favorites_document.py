import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "cli"), *sys.path]):
    spec = importlib.util.spec_from_file_location("global_document_test", ROOT / "cli/global_favorites_document.py")
    codec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(codec)
FIXTURES = json.loads((ROOT / "contracts/global-favorites-portable-v1.fixtures.json").read_bytes())


def row(identifier="001", title="Example"):
    return {"title": title,
            "origin": {"provider": "m3u", "profile": "TV", "subprofile": None, "sourceAlias": "Family"},
            "locator": {"kind": "channel", "channelId": identifier}}


def document(*rows):
    return {"version": 1, "entries": list(rows)}


def raw(value, **kwargs):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, **kwargs).encode("utf-8")


def boundary(target):
    recipe = FIXTURES["byte_limit_recipe"]
    rows = []
    for index in range(recipe["entries"]):
        item = copy.deepcopy(recipe["row"])
        item["locator"]["channelId"] = f"id-{index:04d}"
        item["title"] = ""
        rows.append(item)
    value = document(*rows)
    common, remainder = divmod(target - len(raw(value)), len(rows))
    for index, item in enumerate(rows):
        item["title"] = "a" * (common + (index < remainder))
    return value


class GlobalFavoritesDocumentTests(unittest.TestCase):
    def error(self, function, value, code=None, *args):
        with self.assertRaises(codec.DocumentError) as caught:
            function(value, *args)
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn("fixture-secret", str(caught.exception))
        return caught.exception

    def test_exact_document_entry_and_origin_shapes(self):
        for value in ([], None, {"version": True, "entries": []},
                      {"version": 2, "entries": []}, {"version": 1, "entries": ()},
                      {"version": 1, "entries": [], "extra": "fixture-secret"}):
            self.error(codec.validate_document, value)
        for path in (None, "origin", "locator"):
            value = row()
            target = value if path is None else value[path]
            target["extra"] = "fixture-secret"
            self.error(codec.validate_document, document(value))
            del target["extra"]
            target.pop(next(iter(target)))
            self.error(codec.validate_document, document(value))
        for provider in ("M3U", "", "../m3u", "m3u\n", "a" * 65, 1):
            value = row()
            value["origin"]["provider"] = provider
            self.error(codec.validate_document, document(value))

    def test_json_number_version_matches_js_without_accepting_boolean(self):
        for version in (1, 1.0):
            value = {"version": version, "entries": []}
            result = codec.validate_document(value)
            self.assertEqual(result, document())
            self.assertIs(type(result["version"]), int)
        for spelling in (b"1", b"1.0", b"1e0", b"1.00e+0"):
            self.assertEqual(codec.load_document(b'{"version":' + spelling + b',"entries":[]}'), document())
        for version in (True, False, 0, 2, 1.1, float("inf"), float("nan"), "1"):
            self.error(codec.validate_document, {"version": version, "entries": []})
        for spelling in (b"NaN", b"Infinity", b"1e999"):
            self.error(codec.load_document, b'{"version":' + spelling + b',"entries":[]}', "invalid_json")

    def test_v5_provider_ids_are_exact_bounded_opaque_segments(self):
        for provider in ("vendor/service", "d/provider-tv", "example.tv", "1provider", "x..y",
                         "source_a/source-b.ext", "a" * 64, "a/" + "b" * 62):
            with self.subTest(provider=provider):
                value = row()
                value["origin"]["provider"] = provider
                normalized = codec.validate_document(document(value))
                self.assertEqual(normalized, document(value))
                self.assertEqual(codec.load_document(codec.canonical_bytes(normalized)), normalized)
        for provider in ("", "A", "/a", "a/", "a//b", "a/.", "a/..", "a/../b", "../a", ".a",
                         "a/.hidden", "a/-b", "a/_b", "a\\b", "https://host", "a?token=x", "a#fragment",
                         "a%2fb", "a\n", "a\x00", "a\x7f", " a", "a ", "a\ufeff", "é", "😀", "\ud800",
                         "a" * 65, "a/" + "b" * 63, 1, None, True):
            with self.subTest(provider=repr(provider)):
                value = row()
                value["origin"]["provider"] = provider
                self.error(codec.validate_document, document(value))

    def test_v5_shared_neutral_provider_fixtures(self):
        content = (ROOT / "contracts/global-favorites-provider-id.fixtures.json").read_bytes()
        self.assertEqual(hashlib.sha256(content).hexdigest(),
                         "ba232bfce383fde18d81c1d8f71a911b418c6cf21fca3afdd9a5256f3aa10742")
        fixtures = json.loads(content)
        for case in fixtures["cases"]:
            with self.subTest(case=repr(case["value"])):
                value = row()
                value["origin"]["provider"] = case["value"]
                if case["valid"]:
                    origin = codec._origin(value["origin"])
                    self.assertEqual(origin["provider"], case["canonical"])
                else:
                    self.error(codec.validate_document, document(value))
        for left, right in fixtures["distinct_pairs"]:
            first, second = row(), row()
            first["origin"]["provider"], second["origin"]["provider"] = left, right
            self.assertNotEqual(codec.identity(first), codec.identity(second))

    def test_v5_provider_lookalikes_remain_distinct_without_relabeling(self):
        for left, right in (("vendor/service", "vendor-service"), ("example.tv", "example-tv"),
                            ("1source", "source"), ("vendor/a.b", "vendor/a-b")):
            first, second = row(), row()
            first["origin"]["provider"], second["origin"]["provider"] = left, right
            self.assertNotEqual(codec.identity(first), codec.identity(second))
            plan = codec.plan_documents(document(first), [document(second)], "merge")
            self.assertTrue(plan["can_commit"])
            self.assertEqual(plan["document"], document(first, second))
        for provider in ("vendor/plex", "local.example", "m3u/portal", "vportal-extension"):
            value = row()
            value["origin"]["provider"] = provider
            self.assertEqual(codec.validate_document(document(value)), document(value))
            for locator in ({"kind": "vportal-media", "idField": "id", "id": "1"},
                            {"kind": "plex-media", "serverId": "server", "ratingKey": "1"},
                            {"kind": "local-media", "rootAlias": "Movies", "relativePath": "movie.mp4"}):
                value["locator"] = locator
                self.error(codec.validate_document, document(value))

    def test_provider_specific_locators_and_unavailable_origins_are_data(self):
        examples = [("plex", {"kind": "plex-media", "serverId": "PMS-a_1.test", "ratingKey": "78777"}),
                    ("local", {"kind": "local-media", "rootAlias": "Offline disk", "relativePath": "Films/Три кота 01.mp4"}),
                    ("vportal", {"kind": "vportal-media", "idField": "episode_id", "id": "0002"}),
                    ("stalker", {"kind": "channel", "channelId": "000123"}),
                    ("future-provider", {"kind": "channel", "channelId": "id:001"})]
        rows = []
        for provider, locator in examples:
            value = row()
            value["origin"].update(provider=provider, profile="Missing receiver profile", subprofile="Unresolved", sourceAlias="Unplugged")
            value["locator"] = locator
            rows.append(value)
            self.assertEqual(codec.validate_document(document(value)), document(value))
            wrong = copy.deepcopy(value)
            wrong["origin"]["provider"] = "m3u" if provider in ("plex", "local") else "stalker" if provider == "vportal" else "plex"
            self.error(codec.validate_document, document(wrong))
        self.assertEqual(codec.load_document(codec.canonical_bytes(document(*rows))), document(*rows))

    def test_identifiers_are_exact_bounded_strings(self):
        for identifier in ("", "a" * 257, "id\n", "id?token=fixture-secret", "https://fixture-secret", "id/name", 1, True):
            self.error(codec.validate_document, document(row(identifier)))
        for identifier in ("0001", "A" * 256, "item:1_-."):
            self.assertEqual(codec.validate_document(document(row(identifier))), document(row(identifier)))
        value = row()
        value["origin"]["provider"] = "plex"
        for rating in ("0", "01", "1" * 21, "1\n", 1):
            value["locator"] = {"kind": "plex-media", "serverId": "PMS", "ratingKey": rating}
            self.error(codec.validate_document, document(value))
        value["locator"] = {"kind": "plex-media", "serverId": "PMS:foreign", "ratingKey": "1"}
        self.error(codec.validate_document, document(value))
        value["origin"]["provider"] = "vportal"
        for field in codec.VPORTAL_ID_FIELDS:
            value["locator"] = {"kind": "vportal-media", "idField": field, "id": "0001"}
            self.assertEqual(codec.validate_document(document(value)), document(value))
        value["locator"]["idField"] = "unknown"
        self.error(codec.validate_document, document(value))

    def test_v4_embedded_m3u_portal_shared_cases_preserve_outer_origin(self):
        content = (ROOT / "contracts/global-favorites-nested-m3u-vportal.fixtures.json").read_bytes()
        self.assertEqual(hashlib.sha256(content).hexdigest(),
                         "362978d296107b331f815f178d0f51c7893e86e0a412f0304f476b6e0b866d83")
        examples = json.loads(content)
        for case in examples["cases"]:
            with self.subTest(case=case["id"]):
                value = document(case["entry"])
                if not case["valid"]:
                    self.error(codec.validate_document, value)
                    continue
                self.assertEqual(codec.validate_document(value), value)
                self.assertEqual(codec.load_document(codec.canonical_bytes(value)), value)
                self.assertEqual(value["entries"][0]["origin"]["provider"], "m3u")
                self.assertEqual(codec.required_locator_kinds(value), ["vportal-media"])
                self.assertFalse(codec.supports(value, ["channel", "channel-name"]))
                self.assertTrue(codec.supports(value, ["vportal-media"]))
        root, folder = (examples["cases"][index]["entry"] for index in (0, 1))
        moved = copy.deepcopy(root)
        moved["origin"]["subprofile"] = folder["origin"]["subprofile"]
        self.assertNotEqual(codec.identity(root), codec.identity(moved))
        direct = copy.deepcopy(root)
        direct["origin"]["provider"] = "vportal"
        self.assertNotEqual(codec.identity(root), codec.identity(direct))

    def test_v4_exception_keeps_provider_kind_and_safe_id_restrictions(self):
        value = row()
        value["locator"] = {"kind": "vportal-media", "idField": "id", "id": "0002"}
        for provider in ("m3u", "vportal"):
            value["origin"]["provider"] = provider
            for field in codec.VPORTAL_ID_FIELDS:
                value["locator"]["idField"] = field
                self.assertEqual(codec.validate_document(document(value)), document(value))
            value["locator"]["idField"] = "id"
            for identifier in (0, 42, "", "https://secret", "id/path", "x" * 257):
                bad = copy.deepcopy(value)
                bad["locator"]["id"] = identifier
                self.error(codec.validate_document, document(bad))
            bad = copy.deepcopy(value)
            bad["locator"]["idField"] = "cmd"
            self.error(codec.validate_document, document(bad))
        for provider in ("plex", "local", "stalker", "future-provider"):
            value["origin"]["provider"] = provider
            self.error(codec.validate_document, document(value))
        value["origin"]["provider"] = "m3u"
        for locator in ({"kind": "plex-media", "serverId": "pms", "ratingKey": "1"},
                        {"kind": "local-media", "rootAlias": "Movies", "relativePath": "movie.mp4"}):
            value["locator"] = locator
            self.error(codec.validate_document, document(value))

    def test_relative_paths_do_not_decode_or_grant_filesystem_access(self):
        value = row()
        value["origin"]["provider"] = "local"
        value["locator"] = {"kind": "local-media", "rootAlias": "Movies", "relativePath": "x"}
        for path in ("/film", "C:/film", "C:film", "../film", "A/../film", "A/./film", "A//film", "A/", "A\\film", "https://fixture-secret"):
            value["locator"]["relativePath"] = path
            self.error(codec.validate_document, document(value))
        for path in ("a" * 8192, "Films/%2e%2e/%2f.mp4", "Films/é😀.mkv", "Films/A:B.mp4"):
            value["locator"]["relativePath"] = path
            self.assertEqual(codec.validate_document(document(value)), document(value))
        for path in ("a" * 8193, "😀" * 4097):
            value["locator"]["relativePath"] = path
            self.error(codec.validate_document, document(value))

    def test_cross_language_unicode_and_exact_js_trim_fixtures(self):
        for case in FIXTURES["text_cases"]:
            with self.subTest(case=case["id"]):
                value = row()
                if case["max_utf16_units"] == 1024:
                    value["title"] = case["value"]
                else:
                    self.assertEqual(case["max_utf16_units"], 256)
                    value["origin"]["profile"] = case["value"]
                if case["expected_valid"]:
                    self.assertEqual(codec.validate_document(document(value)), document(value))
                else:
                    self.error(codec.validate_document, document(value))
        for character in codec.JS_TRIM:
            for title in (character + "Title", "Title" + character):
                self.error(codec.validate_document, document(row(title=title)))

    def test_scalar_validation_covers_all_string_positions(self):
        for field in ("title", "profile", "sourceAlias", "subprofile", "rootAlias", "relativePath"):
            value = row()
            value["origin"]["provider"] = "local"
            value["locator"] = {"kind": "local-media", "rootAlias": "Movies", "relativePath": "film"}
            target = value if field == "title" else value["origin"] if field in value["origin"] else value["locator"]
            target[field] = "x\ud800x"
            self.error(codec.validate_document, document(value))
        value = document(row(title="😀"))
        escaped = json.dumps(value, ensure_ascii=True).encode()
        self.assertIn(b"\\ud83d\\ude00", escaped)
        self.assertEqual(codec.load_document(escaped), value)

    def test_identity_fixtures_and_duplicate_titles_never_change_identity(self):
        for case in FIXTURES["identity_cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(codec.identity(case["first"]) == codec.identity(case["second"]), case["expected_equal"])
                if case["expected_equal"]:
                    self.error(codec.validate_document, document(case["first"], case["second"]), "duplicate_entry")
                else:
                    self.assertEqual(codec.validate_document(document(case["first"], case["second"])), document(case["first"], case["second"]))
        for field, change in (("provider", "stalker"), ("sourceAlias", "Other"), ("subprofile", "News")):
            changed = row()
            changed["origin"][field] = change
            self.assertNotEqual(codec.identity(row()), codec.identity(changed))

    def test_canonical_bytes_are_sorted_scalar_utf8_and_preserve_entry_order(self):
        case = FIXTURES["canonical_cases"][0]
        expected = FIXTURES["canonical_expected"][0]
        # This historical vector measures serialization only: trailing U+2028/9
        # is JS trim whitespace and intentionally fails the document validator.
        encoded = raw(case["document"])
        self.assertEqual(len(encoded), expected["bytes"])
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), expected["sha256"])
        self.error(codec.validate_document, case["document"])
        first, second = row("2", "é😀\u2028\u2029x"), row("1")
        value = document(first, second)
        encoded = codec.canonical_bytes(value)
        self.assertEqual(encoded, raw(value))
        self.assertIn("é😀\u2028\u2029x".encode(), encoded)
        self.assertNotIn(b"\\u", encoded)
        self.assertEqual(codec.load_document(encoded)["entries"], [first, second])
        self.assertFalse(encoded.endswith(b"\n"))

    def test_strict_json_rejects_duplicates_nonfinite_invalid_utf8_and_depth(self):
        good = codec.canonical_bytes(document(row()))
        invalid = [b'{"version":1,"version":1,"entries":[]}',
                   good.replace(b'"channelId":"001"', b'"channelId":"001","channelId":"2"'),
                   good.replace(b'"title":"Example"', b'"title":NaN'),
                   good.replace(b'"title":"Example"', b'"title":Infinity'),
                   good.replace(b'"title":"Example"', b'"title":1e999'),
                   good.replace(b'"title":"Example"', b'"title":"\\ud800"'),
                   good.replace(b'"title":"Example"', b'"title":"\\udc00"'),
                   good.replace(b'"title":"Example"', b'"\\ud800":"x"'),
                   b'\xef\xbb\xbf' + good, good + b'\xff', good + good,
                   b'[' * 9 + b'0' + b']' * 9]
        for encoded in invalid:
            self.error(codec.load_document, encoded, "invalid_json")
        # Braces/escaped quotes inside strings must not trip the depth scanner.
        value = document(row(title='Title {[[[ \\" }}}'))
        self.assertEqual(codec.load_document(raw(value)), value)

    def test_exact_one_mib_canonical_and_raw_limits(self):
        for expected in FIXTURES["byte_boundaries_expected"]:
            with self.subTest(case=expected["id"]):
                value = boundary(expected["bytes"])
                encoded = raw(value)
                self.assertEqual(len(encoded), expected["bytes"])
                self.assertEqual(hashlib.sha256(encoded).hexdigest(), expected["sha256"])
                if expected["valid_size"]:
                    self.assertEqual(codec.canonical_bytes(value), encoded)
                    self.assertEqual(codec.load_document(encoded), value)
                else:
                    self.error(codec.validate_document, value, "document_too_large")
                    self.error(codec.load_document, encoded, "document_too_large")
        tiny = codec.canonical_bytes(document())
        self.error(codec.load_document, tiny + b" " * codec.MAX_BYTES, "document_too_large")
        for value in (b"", "{}", bytearray(b"{}"), None):
            self.error(codec.load_document, value)

    def test_entry_limit_and_failure_do_not_modify_input(self):
        value = document(*(row(str(index)) for index in range(1000)))
        self.assertEqual(len(codec.validate_document(value)["entries"]), 1000)
        value["entries"].append(row("1001"))
        original = copy.deepcopy(value)
        self.error(codec.validate_document, value, "document_too_large")
        self.assertEqual(value, original)
        original = document(row())
        result = codec.validate_document(original)
        result["entries"][0]["origin"]["profile"] = "Changed"
        self.assertEqual(original, document(row()))

    def test_merge_keeps_order_and_title_and_reports_safe_blocking_conflict(self):
        current = document(row("2"), row("1"))
        incoming = [document(row("1", "fixture-secret-new-title"), row("3")), document(row("3"))]
        saved = copy.deepcopy((current, incoming))
        plan = codec.plan_documents(current, incoming, "merge")
        self.assertEqual(plan["document"], document(row("2"), row("1"), row("3")))
        self.assertFalse(plan["can_commit"])
        self.assertEqual(plan["title_conflicts"], [{"existing_index": 1, "incoming_document": 0,
                                                     "incoming_index": 0, "reason": "title_mismatch"}])
        self.assertEqual(plan["summary"], {"before": 2, "after": 3, "added": 1, "removed": 0, "retained": 2,
                                           "moved": 0, "skipped_duplicates": 1, "title_changes": 0, "title_conflicts": 1})
        self.assertNotIn("fixture-secret", json.dumps(plan["summary"]) + json.dumps(plan["title_conflicts"]))
        self.assertEqual((current, incoming), saved)
        plan["document"]["entries"][0]["title"] = "Changed"
        self.assertEqual((current, incoming), saved)

    def test_idempotent_merge_and_explicit_replace_order_title_changes_clear(self):
        current = document(row("2"), row("1"))
        repeated = codec.plan_documents(current, [current], "merge")
        self.assertTrue(repeated["can_commit"])
        self.assertEqual(repeated["document"], current)
        self.assertEqual(repeated["summary"]["skipped_duplicates"], 2)
        self.assertEqual(repeated["summary"]["added"], 0)
        replacement = document(row("1", "Changed"), row("3"))
        plan = codec.plan_documents(current, [replacement], "replace")
        self.assertTrue(plan["can_commit"])
        self.assertEqual(plan["document"], replacement)
        self.assertEqual(plan["summary"], {"before": 2, "after": 2, "added": 1, "removed": 1, "retained": 1,
                                           "moved": 1, "skipped_duplicates": 0, "title_changes": 1, "title_conflicts": 0})
        empty = codec.plan_documents(current, [document()], "replace")
        self.assertTrue(empty["can_commit"])
        self.assertEqual(empty["document"], document())
        self.assertEqual(empty["summary"]["removed"], 2)

    def test_replace_conflicts_across_inputs_block_without_silently_winning(self):
        plan = codec.plan_documents(None, [document(row("1", "First")), document(row("1", "Other")),
                                           document(row("1", "Third"))], "replace")
        self.assertFalse(plan["can_commit"])
        self.assertEqual(plan["document"], document(row("1", "First")))
        self.assertEqual(plan["summary"]["title_conflicts"], 1)
        self.assertEqual(len(plan["title_conflicts"]), 1)

    def test_planning_admission_bounds_all_incoming_before_merging(self):
        many = document(*(row(str(index)) for index in range(501)))
        self.error(lambda value: codec.plan_documents(None, value, "merge"), [many, many], "document_too_large")
        large = boundary(codec.MAX_BYTES)
        self.error(lambda value: codec.plan_documents(None, value, "replace"), [large, document()], "document_too_large")
        current = document(*(row(str(index)) for index in range(1000)))
        self.error(lambda value: codec.plan_documents(current, [value], "merge"), document(row("new")), "document_too_large")
        for incoming in ([], (), [document()] * 65, None):
            self.error(lambda value: codec.plan_documents(None, value, "merge"), incoming, "invalid_documents")
        for mode in ("replace-all", "MERGE", None, True):
            self.error(lambda value: codec.plan_documents(None, [document()], value), mode, "invalid_mode")
        self.assertTrue(codec.plan_documents(None, [document()] * 64, "merge")["can_commit"])

    def test_reorder_only_and_deletion_shifts_are_visible(self):
        current = document(row("A"), row("B"))
        reversed_plan = codec.plan_documents(current, [document(row("B"), row("A"))], "replace")
        self.assertTrue(reversed_plan["can_commit"])
        self.assertEqual(reversed_plan["summary"], {"before": 2, "after": 2, "added": 0, "removed": 0,
                                                   "retained": 2, "moved": 2, "skipped_duplicates": 0,
                                                   "title_changes": 0, "title_conflicts": 0})
        unchanged = codec.plan_documents(current, [current], "replace")
        self.assertEqual(unchanged["summary"]["moved"], 0)
        deleted = codec.plan_documents(current, [document(row("B"))], "replace")
        self.assertEqual((deleted["summary"]["removed"], deleted["summary"]["moved"]), (1, 1))
        appended = codec.plan_documents(current, [document(row("C"))], "merge")
        self.assertEqual((appended["summary"]["added"], appended["summary"]["moved"]), (1, 0))

    def test_frozen_v3_channel_name_cases(self):
        for case in FIXTURES["v3_channel_name"]["cases"]:
            with self.subTest(case=case["id"]):
                value = document(case["entry"])
                if case["valid"]:
                    self.assertEqual(codec.validate_document(value), value)
                    self.assertEqual(codec.required_locator_kinds(value), ["channel-name"])
                else:
                    self.error(codec.validate_document, value)

    def test_frozen_v3_unicode_cases_and_identical_trim_set(self):
        fixture = FIXTURES["v3_text_semantics"]
        self.assertEqual([ord(char) for char in codec.JS_TRIM], fixture["trim_codepoints"])
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                value = row()
                value["origin"]["profile"] = case["value"]
                self.assertEqual(case["max_utf16_units"], 256)
                if case["expected"]:
                    self.assertEqual(codec.validate_document(document(value)), document(value))
                else:
                    self.error(codec.validate_document, document(value))

    def test_channel_name_is_explicit_exact_context_and_not_id_fallback(self):
        first = copy.deepcopy(FIXTURES["v3_channel_name"]["cases"][0]["entry"])
        for path, key, changed in (("locator", "name", "news HD"), ("locator", "group", "World"),
                                   ("origin", "profile", "Other"), ("origin", "subprofile", "Other"),
                                   ("origin", "sourceAlias", "Other")):
            second = copy.deepcopy(first)
            second[path][key] = changed
            self.assertNotEqual(codec.identity(first), codec.identity(second))
        renamed = copy.deepcopy(first)
        renamed["title"] = "Different presentation"
        self.assertEqual(codec.identity(first), codec.identity(renamed))
        self.error(codec.validate_document, document(first, renamed), "duplicate_entry")
        identified = copy.deepcopy(first)
        identified["locator"] = {"kind": "channel", "channelId": "News"}
        self.assertNotEqual(codec.identity(first), codec.identity(identified))
        for provider in ("plex", "local", "vportal"):
            second = copy.deepcopy(first)
            second["origin"]["provider"] = provider
            self.error(codec.validate_document, document(second))
        for identifier in ("channel:0", "channel:42", "channel:001", 42):
            self.error(codec.validate_document, document(row(identifier)))
        for identifier in ("001", "channel:alpha", "Channel:42"):
            self.assertEqual(codec.validate_document(document(row(identifier))), document(row(identifier)))

    def test_channel_name_and_group_use_scalar_utf16_limits(self):
        first = copy.deepcopy(FIXTURES["v3_channel_name"]["cases"][0]["entry"])
        first["locator"].update(name="😀" * 512, group="😀" * 128)
        self.assertEqual(codec.validate_document(document(first)), document(first))
        for key, value in (("name", "😀" * 513), ("group", "😀" * 129),
                           ("name", "Title\ufeff"), ("group", "\ud800"), ("group", "")):
            changed = copy.deepcopy(first)
            changed["locator"][key] = value
            self.error(codec.validate_document, document(changed))

    def test_locator_kind_negotiation_is_pure_explicit_and_forward_compatible(self):
        named = copy.deepcopy(FIXTURES["v3_channel_name"]["cases"][0]["entry"])
        values = [row(), named]
        for provider, locator in (("plex", {"kind": "plex-media", "serverId": "PMS", "ratingKey": "1"}),
                                  ("local", {"kind": "local-media", "rootAlias": "Movies", "relativePath": "file.mp4"}),
                                  ("vportal", {"kind": "vportal-media", "idField": "id", "id": "001"})):
            value = row()
            value["origin"]["provider"] = provider
            value["locator"] = locator
            values.append(value)
        doc = document(*values)
        kinds = ["channel", "channel-name", "local-media", "plex-media", "vportal-media"]
        self.assertEqual(codec.required_locator_kinds(doc), kinds)
        self.assertTrue(codec.supports(doc, list(reversed(kinds))))
        self.assertTrue(codec.supports(doc, [*kinds, "future-kind"]))
        self.assertFalse(codec.supports(doc, [kind for kind in kinds if kind != "channel-name"]))
        for advertised in (None, "channel-name", kinds + ["channel"], [*kinds, 1], [*kinds, "x" * 65],
                           [str(i) for i in range(33)], [*kinds, "😀" * 33]):
            self.assertFalse(codec.supports(doc, advertised))
        self.assertTrue(codec.supports(doc, [*kinds, "😀" * 32]))
        self.assertEqual(codec.required_locator_kinds(document()), [])
        self.assertTrue(codec.supports(document(), []))
        self.assertFalse(codec.supports({"version": 1, "entries": [True]}, kinds))
        self.error(codec.required_locator_kinds, {"version": 1, "entries": [True]})

    def test_oversized_advertisement_is_refused_even_for_empty_document(self):
        for kind in ("x" * (1024 * 1024), "😀" * (1024 * 1024)):
            self.assertFalse(codec.supports(document(), [kind]))
        self.assertTrue(codec.supports(document(), ["x" * 64]))
        self.assertFalse(codec.supports(document(), ["x" * 65]))
        self.assertTrue(codec.supports(document(), ["😀" * 32]))
        self.assertFalse(codec.supports(document(), ["😀" * 33]))


if __name__ == "__main__":
    unittest.main()
