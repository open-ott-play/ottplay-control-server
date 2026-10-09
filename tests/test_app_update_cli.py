"""Capacitor updates use the player's authenticated queue and explicit stages."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("ott", Path(__file__).resolve().parents[1] / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
HASH = "a" * 64


class AppUpdateTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(ott.parse_command(["update"]), ("app_update", {"operation": "status"}))
        self.assertEqual(ott.parse_command(["update", "prepare", "https://example.org/app.apk", HASH]),
                         ("app_update", {"operation": "prepare", "url": "https://example.org/app.apk", "sha256": HASH}))
        self.assertEqual(ott.parse_command(["update", "install", HASH]), ("app_update", {"operation": "install", "sha256": HASH}))

    def test_rejects_unsafe_and_ambiguous_commands(self):
        for args in (["install"], ["status", "extra"], ["install", "bad"], ["prepare", "http://example.org/a", HASH],
                     ["prepare", "https://user:password@example.org/a", HASH], ["prepare", "https://example.org/a#x", HASH],
                     ["prepare", "https://example.org:0/a", HASH], ["prepare", "https://example.org/a\n", HASH]):
            with self.subTest(args=args), self.assertRaises(ott.Error):
                ott.parse_command(["update", *args])

    def test_status_never_reports_acceptance_as_installation(self):
        self.assertEqual(ott.app_update_metadata({"accepted": True, "operation": "install", "secret": "hidden"}, {"operation": "install"}),
                         {"accepted": True, "operation": "install"})
        value = dict(version=1, phase="ready", sha256=HASH, installed_version="1.1.53-beta.26", installed_code=10154,
                     target_version="1.1.53-beta.27", target_code=10155, error="", can_request_installs=True,
                     user_confirmation_required=True, url="https://private.example/secret")
        result = ott.app_update_metadata(value, {"operation": "status"})
        self.assertNotIn("url", result)
        self.assertEqual(result["phase"], "ready")
        for key, bad in (("phase", "success"), ("installed_code", True), ("sha256", "bad"), ("error", "https://private/secret")):
            with self.subTest(key=key), self.assertRaises(ott.Error):
                ott.app_update_metadata(value | {key: bad}, {"operation": "status"})


if __name__ == "__main__":
    unittest.main()
