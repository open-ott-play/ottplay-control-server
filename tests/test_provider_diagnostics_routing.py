"""Additive provider diagnostics preserve old capabilities and group paths."""
import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("provider_routing_ott", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class ProviderDiagnosticsRoutingTests(unittest.TestCase):
    def capabilities(self):
        return {"version": 1, "player": {"version": "1.1.62", "platform": "tauri", "runtime": "runtime-a"},
                "lifecycle": [], "input": [], "playback": [],
                "inspect": {"version": 1, "sections": ["doctor", "snapshot", "operation"]}}

    def test_capability_is_additive_without_changing_inspection_sections(self):
        raw = self.capabilities()
        old = ott.capabilities_metadata(raw)
        self.assertNotIn("provider_diagnostics", old)
        raw["provider_diagnostics"] = {"version": 1}
        new = ott.capabilities_metadata(raw)
        self.assertEqual(new.pop("provider_diagnostics"), {"version": 1})
        self.assertEqual(new, old)

    def test_capability_rejects_unknown_fields_and_noninteger_versions(self):
        for value in ({}, {"version": True}, {"version": 1.0}, {"version": 2},
                      {"version": 1, "url": "https://private.invalid"}, None, []):
            raw = self.capabilities()
            raw["provider_diagnostics"] = value
            with self.subTest(value=value), self.assertRaises(ott.Error):
                ott.capabilities_metadata(raw)

    def test_provider_report_directories_are_independent_for_groups(self):
        for verb in ("doctor", "debug", "dbg"):
            words = [verb, "--provider", "--out", "./incident"]
            original = copy.deepcopy(words)
            route = ott.player_command_route(words)
            self.assertEqual(route, "workbench")
            one, _, destinations = ott.group_output_arguments(words, route, None, "t1")
            two, _, _ = ott.group_output_arguments(words, route, None, "t2")
            self.assertEqual(one[-1], "incident.t1")
            self.assertEqual(two[-1], "incident.t2")
            self.assertEqual(words, original)
            self.assertEqual(len(destinations), 1)

    def test_provider_report_equals_option_is_suffix_expanded(self):
        words = ["doctor", "--provider", "--out=./incident"]
        rewritten, _, _ = ott.group_output_arguments(words, "workbench", None, "t1")
        self.assertEqual(rewritten[-1], "--out=incident.t1")


if __name__ == "__main__":
    unittest.main()
