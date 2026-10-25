import copy
import json
from pathlib import Path
import unittest
from unittest import mock

import test_player_settings_cli as base

base_caps, ott, settings, setting_result = base.caps, base.ott, base.settings, base.result

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "contracts/plex-folders-v1.json").read_text())


def caps():
    value = base_caps()
    value["plex_folders"] = copy.deepcopy(FIXTURE["capability"])
    return value


def result(operation):
    return copy.deepcopy(FIXTURE["responses"][operation])


class PlexFoldersCliTests(unittest.TestCase):
    run_cli = base.PlayerSettingsCliTests.run_cli

    def test_read_complete_selection_and_each_atomic_edit(self):
        code, out, err, client = self.run_cli(["-j", "f", "setting", "plexFolderPaths"], [caps(), result("list")])
        self.assertEqual((code, err, json.loads(out)), (0, "", result("list")))
        self.assertEqual(client.call.call_count, 2)
        for operation, words in [("add", ["/library/sections/16/all", "Children's films"]),
                                 ("remove", ["/library/sections/16/all"]), ("clear", [])]:
            code, out, err, client = self.run_cli(["f", "setting", "plexFolderPaths", operation] + words,
                                                [caps(), result("list"), result(operation)])
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(client.call.call_count, 3)
            self.assertEqual(client.call.call_args, mock.call("device-f", "plex_folders", FIXTURE["requests"][operation]))
            self.assertIn("next catalogue load", out)

    def test_all_1000_folders_are_returned_from_bounded_pages(self):
        pages = []
        for start in range(0, 1000, 10):
            page = result("list")
            page.update(total=1000, items=[{"path": "/library/sections/" + str(n + 1) + "/all", "title": "Folder " + str(n + 1)} for n in range(start, start + 10)],
                        next_cursor="cursor-" + str(start + 10) if start + 10 < 1000 else None)
            pages.append(page)
        code, out, err, client = self.run_cli(["-j", "f", "setting", "plexFolderPaths"], [caps()] + pages)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(json.loads(out)["items"]), 1000)
        self.assertEqual(client.call.call_count, 101)

    def test_empty_selection_means_all_libraries(self):
        page = result("list"); page.update(items=[], total=0)
        code, out, err, _ = self.run_cli(["f", "setting", "plexFolderPaths"], [caps(), page])
        self.assertEqual((code, err), (0, "")); self.assertIn("all libraries", out)

    def test_old_player_and_bad_paths_never_write(self):
        code, out, err, client = self.run_cli(["f", "setting", "plexFolderPaths", "clear"], [base_caps()])
        self.assertEqual((code, out, client.call.call_count), (1, "", 1))
        for path in ["https://example.invalid/library/sections/1/all", "/library/sections/1/../all", "/library/sections/1/all?token=secret"]:
            code, out, err, client = self.run_cli(["f", "setting", "plexFolderPaths", "add", path, "Folder"], [])
            self.assertEqual((code, out, client.call.call_count), (1, "", 0))
        code, out, err, client = self.run_cli(["f", "setting", "plexFolderPaths", "[]"], [])
        self.assertEqual((code, out, client.call.call_count), (1, "", 0))

    def test_snapshot_change_duplicate_or_incomplete_page_is_rejected(self):
        first = result("list"); first.update(total=2, next_cursor="cursor-2")
        second = result("list"); second.update(total=2, items=[{"path": "/library/sections/17/all", "title": "Second"}])
        for change in [lambda p: p.update(revision="other"), lambda p: p.update(items=first["items"]),
                       lambda p: p.update(total=3), lambda p: p.update(items=[])]:
            page = copy.deepcopy(second); change(page)
            code, out, err, client = self.run_cli(["f", "setting", "plexFolderPaths"], [caps(), first, page])
            self.assertEqual((code, out, client.call.call_count), (1, "", 3))

    def test_invalid_or_failed_write_is_not_repeated(self):
        for change in [lambda p: p.update(persisted=False), lambda p: p.update(runtime="other"),
                       lambda p: p.update(total=0),
                       lambda p: p["context"].update(token="other"), lambda p: p.update(total=1001)]:
            response = result("add"); change(response)
            code, out, err, client = self.run_cli(["f", "setting", "plexFolderPaths", "add", "/library/sections/16/all", "Title"],
                                                [caps(), result("list"), response])
            self.assertEqual((code, out, client.call.call_count), (1, "", 3))
        error = result("error"); error.update(reason="capacity_exceeded", error="private remote error")
        code, out, err, client = self.run_cli(["f", "setting", "plexFolderPaths", "add", "/library/sections/16/all", "Title"],
                                            [caps(), result("list"), ott.PlayerRejected("private remote error", error)])
        self.assertEqual((code, out, client.call.call_count), (1, "", 3))
        self.assertIn("1000", err); self.assertNotIn("private remote error", err)

    def test_malformed_metadata_including_surrogates_returns_clean_error(self):
        for edit in [lambda p: p.update(items=[{}]), lambda p: p.update(next_cursor=[]),
                     lambda p: p["items"][0].update(title="\ud800"), lambda p: p.update(total=True)]:
            page = result("list"); edit(page)
            with self.assertRaises(ott.Error):
                settings.folder_metadata(ott, page, FIXTURE["requests"]["list"])

    def test_title_matches_real_utf16_limit(self):
        self.assertTrue(settings.folder_title("😀" * 512))
        self.assertFalse(settings.folder_title("😀" * 513))
        self.assertFalse(settings.folder_title("\ud800"))

    def test_settings_inventory_links_collection_only_for_active_plex(self):
        page = {"version": 1, "op": "schema", "runtime": "page-123", "context": copy.deepcopy(FIXTURE["context"]), "settings": [], "next_cursor": None}
        code, out, err, _ = self.run_cli(["f", "settings"], [caps(), copy.deepcopy(page)])
        self.assertEqual((code, err), (0, "")); self.assertIn("plexFolderPaths", out)
        page["context"]["provider"] = "m3u"
        code, out, err, _ = self.run_cli(["f", "settings"], [caps(), page])
        self.assertEqual((code, err), (0, "")); self.assertNotIn("plexFolderPaths", out)

    def test_scalar_after_ack_is_explicit(self):
        get, write = setting_result("get"), setting_result("set")
        get["descriptor"]["apply"] = "after_ack"
        write["application"] = {"state": "deferred", "reason": "after_ack"}
        code, out, err, _ = self.run_cli(["f", "setting", "bufSize", "3"], [base_caps(), get, write])
        self.assertEqual((code, err), (0, "")); self.assertIn("after ack", out)


if __name__ == "__main__":
    unittest.main()
