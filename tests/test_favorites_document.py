import copy
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("favorites_document_test", ROOT / "cli/favorites_document.py")
codec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codec)


def uuid(number):
    return f"00000000-0000-4000-8000-{number:012x}"


def local(name="Films/Example.mp4"):
    return {"state": "located", "locator": {"kind": "local", "rootBindingId": uuid(50), "relativePath": name}}


def unresolved(identifier=1):
    provenance = {"origin": "raw"}
    if identifier is not None:
        provenance["legacyId"] = identifier
    return {"state": "unresolved", "reason": "missing", "provenance": provenance}


def scope(number=1):
    return {"schema": 1, "scope": {"provider": "offline", "profileBindingId": uuid(100), "sourceBindingId": uuid(number)},
            "coverage": copy.deepcopy(codec.COVERAGE),
            "channelLists": {"active": "Favorites", "order": ["Favorites", "Later"],
                             "default": {"present": False},
                             "lists": [{"name": "Favorites", "entries": []}, {"name": "Later", "entries": []}]},
            "mediaFavorites": [local()]}


def bundle(*scopes):
    return {"version": 1, "scopes": list(scopes)}


class FavoritesDocumentTests(unittest.TestCase):
    def error(self, value, code=None, **kwargs):
        with self.assertRaises(codec.DocumentError) as caught:
            codec.validate_document(value, **kwargs)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn("fixture-secret", str(caught.exception))

    def test_bundle_sort_only_preserves_nested_order_empty_lists_and_provenance(self):
        first, second = scope(2), scope(1)
        first["channelLists"]["order"].reverse()
        first["channelLists"]["lists"][1]["entries"] = [unresolved(3), unresolved(2), unresolved(3)]
        second["channelLists"]["default"] = {"present": True, "value": None}
        original = bundle(first, second)
        saved = copy.deepcopy(original)
        result = codec.validate_document(original)
        self.assertEqual(result, bundle(second, first))
        self.assertEqual(original, saved)
        result["scopes"][1]["channelLists"]["lists"].clear()
        self.assertEqual(original, saved)

    def test_exact_scope_duplicates_and_strict_shape(self):
        self.error(bundle(scope(), scope()), "duplicate_scope")
        for value in ([], {"version": True, "scopes": []}, {"version": 1, "entries": []}, {"version": 1, "scopes": [], "extra": "fixture-secret"}):
            self.error(value)
        value = scope()
        value["scope"]["provider"] = "stalker"
        self.error(bundle(value))
        value = scope()
        value["coverage"]["history"] = "included"
        self.error(bundle(value))

    def test_list_name_order_active_and_default_semantics(self):
        for mutate in (
            lambda value: value["lists"].append(copy.deepcopy(value["lists"][0])),
            lambda value: value.update(order=["Favorites", "Favorites"]),
            lambda value: value.update(active="missing"),
            lambda value: value.update(default={"present": True}),
            lambda value: value.update(default={"present": False, "value": None}),
            lambda value: value.update(default={"present": True, "value": "missing"}),
            lambda value: value["lists"][0].update(name="unsafe\nname"),
        ):
            value = scope()
            mutate(value["channelLists"])
            self.error(bundle(value))
        value = scope()
        value["channelLists"]["lists"][1]["name"] = "favorites"
        value["channelLists"]["order"][1] = "favorites"
        self.assertEqual(codec.validate_document(bundle(value)), bundle(value))
        value["channelLists"] = {"active": None, "order": [], "default": {"present": False}, "lists": []}
        self.assertEqual(codec.validate_document(bundle(value)), bundle(value))

    def test_channel_codec_denied_without_proven_callback(self):
        value = scope()
        value["channelLists"]["lists"][0]["entries"] = [{"state": "located", "locator": {"kind": "channel", "itemId": "fixture-only-channel"}}]
        self.error(bundle(value), "unsupported_channel_codec")
        self.error(bundle(value), "unsupported_channel_codec", channel_codec=lambda *_: 1)
        self.assertEqual(codec.validate_document(bundle(value), channel_codec=lambda context, locator: context == value["scope"] and locator["itemId"] == "fixture-only-channel"), bundle(value))
        def rejected(*_):
            raise ValueError("fixture-secret")
        self.error(bundle(value), "unsupported_channel_codec", channel_codec=rejected)

    def test_plex_final_locator_shape_and_old_shape_rejection(self):
        value = scope()
        value["scope"]["provider"] = "plex"
        value["mediaFavorites"] = [{"state": "located", "locator": {"kind": "plex-media", "serverId": "verified-fixture-PMS", "ratingKey": "78777"}}]
        self.assertEqual(codec.validate_document(bundle(value)), bundle(value))
        for locator in ({"kind": "plex", "serverMachineId": "old", "ratingKey": "1"},
                        {"kind": "plex-media", "serverId": "verified", "ratingKey": "01"},
                        {"kind": "plex-media", "serverId": "verified", "ratingKey": 1}):
            changed = copy.deepcopy(value)
            changed["mediaFavorites"][0]["locator"] = locator
            self.error(bundle(changed))

    def test_local_path_is_exact_relative_and_does_not_grant_access(self):
        for path in ("/Films/a.mp4", "../a.mp4", "Films/../a.mp4", "Films//a.mp4", "Films/./a.mp4", "C:/a.mp4", "https://example.invalid/a", "Films\\a.mp4", "Films/a\x00.mp4"):
            value = scope()
            value["mediaFavorites"] = [local(path)]
            self.error(bundle(value))
        value = scope()
        value["mediaFavorites"] = [local("Films/Три кота 01.mp4")]
        self.assertEqual(codec.validate_document(bundle(value)), bundle(value))

    def test_auxiliary_shuffle_baseline_can_differ_after_membership_edits(self):
        value = scope()
        row = value["channelLists"]["lists"][0]
        row["entries"] = [unresolved(2), unresolved(3)]
        row["shuffleBefore"] = [unresolved(1), unresolved(2)]
        result = codec.plan_documents(None, [bundle(value)], "replace")
        self.assertEqual(result["document"], bundle(value))
        self.assertEqual(result["summary"]["after"], 3)
        self.assertEqual(result["summary"]["memberships"]["after"], 3)
        # Actual player reset filters historical baseline against current refs.
        current = [entry["provenance"]["legacyId"] for entry in row["entries"]]
        reset = [number for number in (1, 2, 3) if number in current]
        self.assertEqual(reset, [2, 3])

    def test_clear_preserves_structure_provenance_and_shuffle_auxiliary(self):
        value = scope()
        value["channelLists"]["default"] = {"present": True, "value": "Favorites"}
        value["channelLists"]["lists"][0].update(entries=[unresolved(2)], shuffleBefore=[unresolved(1)])
        old = copy.deepcopy(value)
        cleared = codec.clear_scope(value)
        self.assertEqual(value, old)
        self.assertEqual(cleared["channelLists"]["lists"][0]["shuffleBefore"], [unresolved(1)])
        self.assertNotIn("shuffleBefore", cleared["channelLists"]["lists"][1])
        self.assertEqual(cleared["channelLists"]["default"], old["channelLists"]["default"])
        result = codec.plan_documents(bundle(value), [bundle(cleared)], "replace")
        self.assertEqual(result["summary"]["after"], 0)
        self.assertEqual(result["summary"]["removed"], 2)

    def test_distinct_scope_merge_and_identical_repeats_are_idempotent(self):
        first, second = scope(1), scope(2)
        result = codec.plan_documents(bundle(first), [bundle(second), bundle(second)], "merge")
        self.assertEqual(result["document"], bundle(first, second))
        self.assertEqual(result["summary"]["skipped_duplicates"], 1)
        self.assertEqual(result["summary"]["added"], 1)
        self.assertEqual(result["summary"]["removed"], 0)
        repeated = codec.plan_documents(result["document"], [bundle(second)], "merge")
        self.assertEqual(repeated["document"], result["document"])
        self.assertEqual((repeated["summary"]["added"], repeated["summary"]["removed"]), (0, 0))

    def test_differing_same_scope_merge_blocks_and_preserves_original(self):
        original = scope()
        variations = []
        for field in ("title", "order", "active", "default", "empty-list"):
            value = copy.deepcopy(original)
            if field == "title":
                value["mediaFavorites"][0]["title"] = "New display"
            elif field == "order":
                value["channelLists"]["order"].reverse()
            elif field == "active":
                value["channelLists"]["active"] = "Later"
            elif field == "default":
                value["channelLists"]["default"] = {"present": True, "value": None}
            else:
                value["channelLists"]["lists"].append({"name": "Empty", "entries": []})
                value["channelLists"]["order"].append("Empty")
            variations.append(value)
        for value in variations:
            result = codec.plan_documents(bundle(original), [bundle(value)], "merge")
            self.assertEqual(result["document"], bundle(original))
            self.assertEqual(result["summary"]["conflicts"], {"resolved": 0, "unresolved": 1})

    def test_replace_uses_inputs_only_and_conflicting_group_overlap_blocks(self):
        original = scope()
        replacement = scope()
        replacement["mediaFavorites"] = [local("Films/new.mp4")]
        replaced = codec.plan_documents(bundle(original, scope(99)), [bundle(replacement)], "replace")
        self.assertEqual(replaced["document"], bundle(replacement))
        self.assertEqual(replaced["summary"]["conflicts"]["unresolved"], 0)
        self.assertEqual((replaced["summary"]["removed"], replaced["summary"]["added"]), (2, 1))
        blocked = codec.plan_documents(bundle(original), [bundle(replacement), bundle(original)], "replace")
        self.assertEqual(blocked["summary"]["conflicts"]["unresolved"], 1)
        self.assertEqual(blocked["document"], bundle(replacement))

    def test_membership_multiplicity_is_separate_from_unique_item_counts(self):
        value = scope()
        value["mediaFavorites"] = []
        value["channelLists"]["lists"][0]["entries"] = [unresolved(1), unresolved(1)]
        value["channelLists"]["lists"][1]["entries"] = [unresolved(1)]
        result = codec.plan_documents(None, [bundle(value), bundle(value)], "replace")
        self.assertEqual(result["document"], bundle(value))
        self.assertEqual(result["summary"]["after"], 1)
        self.assertEqual(result["summary"]["memberships"]["after"], 3)
        self.assertEqual(result["summary"]["skipped_duplicates"], 1)
        changed = copy.deepcopy(value)
        changed["channelLists"]["lists"][0]["entries"].pop()
        delta = codec.plan_documents(bundle(value), [bundle(changed)], "replace")["summary"]
        self.assertEqual((delta["removed"], delta["memberships"]["removed"]), (0, 1))

    def test_opaque_unresolved_occurrences_are_not_deduped_by_title(self):
        value = scope()
        value["mediaFavorites"] = [unresolved(None), unresolved(None)]
        for row in value["mediaFavorites"]:
            row["title"] = "Same descriptive name"
        result = codec.plan_documents(None, [bundle(value)], "replace")
        self.assertEqual(result["summary"]["after"], 2)
        self.assertEqual(result["document"], bundle(value))

    def test_opaque_comparison_requires_same_full_record_and_position(self):
        original = scope()
        original["mediaFavorites"] = [unresolved(None), unresolved(None)]
        original["mediaFavorites"][0]["title"] = "First"
        original["mediaFavorites"][1]["title"] = "Second"
        unchanged = codec.plan_documents(bundle(original), [bundle(original)], "replace")["summary"]
        self.assertEqual((unchanged["retained"], unchanged["added"], unchanged["removed"]), (2, 0, 0))
        changed = copy.deepcopy(original)
        changed["mediaFavorites"][0]["title"] = "Replacement"
        summary = codec.plan_documents(bundle(original), [bundle(changed)], "replace")["summary"]
        self.assertEqual((summary["retained"], summary["added"], summary["removed"]), (1, 1, 1))
        changed["mediaFavorites"] = list(reversed(original["mediaFavorites"]))
        summary = codec.plan_documents(bundle(original), [bundle(changed)], "replace")["summary"]
        self.assertEqual((summary["retained"], summary["added"], summary["removed"]), (0, 2, 2))

    def test_located_cross_store_identity_and_unresolved_namespaces(self):
        value = scope()
        value["channelLists"]["lists"][0]["entries"] = [local(), unresolved(1)]
        value["mediaFavorites"].append(unresolved(1))
        summary = codec.plan_documents(None, [bundle(value)], "replace")["summary"]
        self.assertEqual(summary["after"], 3)
        self.assertEqual(summary["memberships"]["after"], 4)
        changed = copy.deepcopy(value)
        changed["channelLists"]["lists"][0]["entries"].pop(0)
        summary = codec.plan_documents(bundle(value), [bundle(changed)], "replace")["summary"]
        self.assertEqual((summary["retained"], summary["added"], summary["removed"]), (3, 0, 0))
        self.assertEqual(summary["memberships"], {"before": 4, "after": 3, "added": 0, "removed": 1, "retained": 3})

    def test_dense_valid_bundle_is_not_rejected_by_smaller_node_limit(self):
        value = scope()
        value["mediaFavorites"] = [unresolved(None) for _ in range(8000)]
        document = bundle(value)
        self.assertLess(len(codec._json(document).encode("utf-8")), codec.MAX_BYTES)
        self.assertEqual(codec.validate_document(document), document)

    def test_compact_byte_budget_accepts_exact_boundary(self):
        value = bundle(scope())
        value["scopes"][0]["mediaFavorites"][0]["title"] = 'Unicode кот, quote " and slash \\ '
        size = len(codec._json(value).encode("utf-8"))
        with mock.patch.object(codec, "MAX_BYTES", size):
            self.assertEqual(codec.validate_document(value), value)
        with mock.patch.object(codec, "MAX_BYTES", size - 1):
            self.error(value, "document_too_large")

    def test_byte_and_traversal_limits_fail_without_mutating_inputs(self):
        value = bundle(scope())
        original = copy.deepcopy(value)
        with mock.patch.object(codec, "MAX_BYTES", 128):
            self.error(value, "document_too_large")
        cyclic = {}
        cyclic["loop"] = cyclic
        self.error(cyclic, "document_too_large")
        self.error({"version": 1, "scopes": [], "bad": float("nan")})
        self.error({"version": 1, "scopes": [], "bad": "\ud800"})
        self.assertEqual(value, original)

    def test_summary_matches_existing_cli_injected_contract(self):
        cli_spec = importlib.util.spec_from_file_location("favorites_document_cli_contract", ROOT / "cli/ott.py")
        cli = importlib.util.module_from_spec(cli_spec)
        cli_spec.loader.exec_module(cli)
        for mode in ("merge", "replace"):
            result = codec.plan_documents(bundle(scope()), [bundle(scope(2))], mode)
            self.assertEqual(cli.favorites_summary_metadata(result["summary"], mode), result["summary"])
            document, summary = cli._plan_favorites_documents(bundle(scope()), [bundle(scope(2))], mode,
                                                               codec.validate_document, codec.plan_documents)
            self.assertEqual((document, summary), (result["document"], result["summary"]))


if __name__ == "__main__":
    unittest.main()
