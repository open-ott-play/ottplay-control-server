"""Offline guards against routing a legacy USB command to the wrong device."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "android_usb_legacy_redirect",
    Path(__file__).resolve().parents[1] / "tools/android_usb_legacy_redirect.py",
)
redirect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(redirect)


class LegacyRedirectTests(unittest.TestCase):
    def setUp(self):
        self.identity = {
            "serial": "EXAMPLE-SERIAL",
            "model": "Example Model",
            "sdk": 22,
            "android_id": "0123456789abcdef",
        }
        self.old = {"version": 1, "target": copy.deepcopy(self.identity)}
        self.new = {
            "version": 2,
            "devices": {
                "renamed-tablet": {
                    "enabled": True,
                    "target": copy.deepcopy(self.identity),
                },
            },
        }

    def command(self, arguments):
        documents = {
            redirect.OLD_CONFIG: self.old,
            redirect.NEW_CONFIG: self.new,
        }
        return redirect.build_command(
            ["--config", redirect.OLD_CONFIG, *arguments],
            reader=lambda path: documents[path],
        )

    def test_routes_matching_identity_to_current_alias(self):
        for action in ("status", "wake", "recover", "reboot"):
            with self.subTest(action=action):
                self.assertEqual(
                    self.command([action]),
                    [
                        "/usr/bin/python3", "-B", redirect.NEW_SCRIPT,
                        "--config", redirect.NEW_CONFIG, action, "renamed-tablet",
                    ],
                )

    def test_each_identity_field_must_match(self):
        for field, value in (
            ("serial", "ANOTHER-SERIAL"),
            ("model", "Another Model"),
            ("sdk", 23),
            ("android_id", "fedcba9876543210"),
        ):
            with self.subTest(field=field):
                original = self.new["devices"]["renamed-tablet"]["target"][field]
                self.new["devices"]["renamed-tablet"]["target"][field] = value
                with self.assertRaises(redirect.RedirectError):
                    self.command(["recover"])
                self.new["devices"]["renamed-tablet"]["target"][field] = original

    def test_sdk_must_be_an_integer_not_a_boolean(self):
        self.old["target"]["sdk"] = True
        self.new["devices"]["renamed-tablet"]["target"]["sdk"] = True
        with self.assertRaises(redirect.RedirectError):
            self.command(["wake"])

    def test_disabled_or_unprovisioned_entry_is_not_a_match(self):
        self.new["devices"]["renamed-tablet"]["enabled"] = False
        with self.assertRaises(redirect.RedirectError):
            self.command(["recover"])
        self.new["devices"]["renamed-tablet"] = {
            "enabled": True, "target": {"model": "Example Model"},
        }
        with self.assertRaises(redirect.RedirectError):
            self.command(["recover"])

    def test_multiple_enabled_identity_matches_are_rejected(self):
        self.new["devices"]["duplicate"] = copy.deepcopy(
            self.new["devices"]["renamed-tablet"]
        )
        with self.assertRaises(redirect.RedirectError):
            self.command(["reboot"])
        self.new["devices"]["duplicate"]["enabled"] = False
        self.assertEqual(self.command(["status"])[-1], "renamed-tablet")

    def test_run_and_unknown_config_rejected_before_any_file_read(self):
        for arguments in (
            ["--config", redirect.OLD_CONFIG, "run"],
            ["--config", "/untrusted/config.json", "status"],
        ):
            with self.subTest(arguments=arguments):
                with patch.object(
                    redirect, "read_config", side_effect=AssertionError("file read")
                ) as reader:
                    with self.assertRaises(redirect.RedirectError):
                        redirect.build_command(arguments, reader=reader)
                    reader.assert_not_called()

    def test_unknown_versions_cannot_select_a_device(self):
        for document in (self.old, self.new):
            original = document["version"]
            document["version"] = 99
            with self.assertRaises(redirect.RedirectError):
                self.command(["recover"])
            document["version"] = original

    def test_swipe_and_screenshot_arguments_remain_separate(self):
        self.assertEqual(
            self.command(["swipe", "10", "20", "30", "40", "--duration", "700"])[-8:],
            ["swipe", "renamed-tablet", "10", "20", "30", "40", "--duration", "700"],
        )
        path = "capture-2026_10.png"
        self.assertEqual(
            self.command(["screenshot", path])[-3:],
            ["screenshot", "renamed-tablet", path],
        )

    def test_screenshot_paths_rejected_before_reading_identity_or_executing(self):
        for name in (
            "/etc/cron.d/capture.png", "../capture.png", "sub/capture.png",
            r"sub\capture.png", ".capture.png", "capture.PNG", "capture.png\n",
            "capture;$(id).png", "a" * 121 + ".png", "",
        ):
            with self.subTest(name=name):
                with patch.object(
                    redirect, "read_config", side_effect=AssertionError("file read")
                ) as reader:
                    with self.assertRaises(redirect.RedirectError):
                        redirect.build_command(
                            ["--config", redirect.OLD_CONFIG, "screenshot", name],
                            reader=reader,
                        )
                    reader.assert_not_called()

    def test_main_replaces_process_without_capturing_output(self):
        command = self.command(["status"])
        with patch.object(redirect, "build_command", return_value=command) as build:
            with patch.object(redirect.os, "execv") as execute:
                redirect.main(["--config", redirect.OLD_CONFIG, "status"])
        build.assert_called_once_with(["--config", redirect.OLD_CONFIG, "status"])
        execute.assert_called_once_with("/usr/bin/python3", command)

    def test_invalid_json_does_not_echo_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{PRIVATE_SENTINEL", encoding="utf-8")
            with self.assertRaises(redirect.RedirectError) as failure:
                redirect.read_config(path)
            self.assertNotIn("PRIVATE_SENTINEL", str(failure.exception))
            path.write_text(json.dumps(self.old), encoding="utf-8")
            self.assertEqual(redirect.read_config(path), self.old)


if __name__ == "__main__":
    unittest.main()
