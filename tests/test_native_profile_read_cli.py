"""Native empty profile lists remain distinct from mutation preconditions."""
import copy
import json
import unittest
from unittest import mock

import test_profiles_cli as profiles
import test_presets_cli as presets
import test_preset_session as sessions

ott = profiles.ott


def listing(provider, active=None, configured=False):
    rows = []
    for number in range(1, 16):
        row = {"number": number, "name": "Configured" if configured and number == 7 else "",
               "active": number == active, "history_hours": 0,
               "playlist_configured": provider == "m3u" and configured and number == 7}
        if provider == "vportal":
            row["vportal_configured"] = configured and number == 7
        rows.append(row)
    return {"provider": provider, "profiles": rows}


class NativeProfileReadTests(unittest.TestCase):
    def run_cli(self, words, response, machine=False):
        return profiles.ProfileOutputTest().run_command(words, response, machine)

    def test_zero_or_one_active_public_lists_remain_readable(self):
        for provider in ("m3u", "vportal"):
            for active in (None, 1, 15):
                for configured in (False, True):
                    for machine in (False, True):
                        with self.subTest(provider=provider, active=active, configured=configured, machine=machine):
                            data = listing(provider, active, configured)
                            code, output, errors, calls = self.run_cli(["profiles"], data, machine)
                            self.assertEqual((code, errors), (0, ""))
                            self.assertEqual(calls, [mock.call("dev_tv", "profiles", {})])
                            if machine:
                                self.assertEqual(json.loads(output), data)
                            else:
                                self.assertEqual(len(output.splitlines()), 15)
                                self.assertEqual(sum(line.startswith("*") for line in output.splitlines()),
                                                 0 if active is None else 1)

    def test_relaxed_read_keeps_shape_bounds_uniqueness_and_redaction(self):
        for provider in ("m3u", "vportal"):
            safe = listing(provider)
            raw = copy.deepcopy(safe)
            raw["token"] = "private-fixture-token"
            raw["profiles"][0]["source"] = "https://private.invalid/?token=private-fixture-token"
            code, output, errors, _ = self.run_cli(["profs"], raw, True)
            self.assertEqual((code, errors), (0, ""))
            self.assertEqual(json.loads(output), safe)
            for mutate in (lambda d: d["profiles"].pop(),
                           lambda d: d["profiles"][1].update(number=1),
                           lambda d: d["profiles"].reverse(),
                           lambda d: d["profiles"][0].update(active=1),
                           lambda d: (d["profiles"][0].update(active=True), d["profiles"][1].update(active=True))):
                bad = copy.deepcopy(safe)
                mutate(bad)
                with self.subTest(provider=provider, bad=bad):
                    code, output, errors, calls = self.run_cli(["profiles"], bad, True)
                    self.assertEqual((code, output), (1, ""))
                    self.assertIn("invalid profile metadata", errors)
                    self.assertEqual(len(calls), 1)

    def test_preflight_default_stays_strict_and_opt_in_is_read_only(self):
        for provider in ("m3u", "vportal"):
            data = listing(provider)
            with self.assertRaisesRegex(ott.Error, "invalid profile metadata"):
                ott.profile_metadata(data, "profiles", {})
            self.assertEqual(ott.profile_metadata(data, "profiles", {}, allow_no_active=True), data)
            response = {"provider": provider, "profile": data["profiles"][0], "dispatched": True}
            with self.assertRaisesRegex(ott.Error, "may have executed"):
                ott.profile_metadata(response, "profile", {"number": 1}, allow_no_active=True)

    def test_vportal_mutation_preflight_sends_no_write_without_active_profile(self):
        client = mock.Mock(timeout=45)
        client.call.return_value = listing("vportal")
        with self.assertRaisesRegex(ott.Error, "invalid profile metadata"):
            ott.vportal_profile_command(client, "dev_tv", {"number": 1, "settings": {"vportal": "fixture"}}, 45)
        client.call.assert_called_once_with("dev_tv", "profiles", {})

    def test_preset_no_active_start_stops_before_any_m3u_write(self):
        player = presets.FakePlayer(active=None)
        name, preset = ott.validate_preset(presets.config(), "local")
        result = ott.load_preset(player, "dev_tv", name, preset)
        self.assertEqual((result["status"], result["stage"]), ("failed", "wait_m3u"))
        self.assertFalse(any(action in ("profile", "profile_settings") for _, action, _ in player.calls))
        self.assertIn("save_plex", result["completed"])

    def test_preset_no_active_final_readback_cannot_confirm_success(self):
        player = presets.FakePlayer(faults={("profiles", 2): listing("m3u")})
        name, preset = ott.validate_preset(presets.config(), "local")
        result = ott.load_preset(player, "dev_tv", name, preset)
        self.assertEqual((result["status"], result["stage"]), ("failed", "verify_profiles"))
        self.assertEqual(player.counts["profiles"], 2)
        self.assertEqual(player.calls[-1][1], "profiles")

    def test_preset_session_verify_still_rejects_no_active_precondition(self):
        player = sessions.ExactPlayer()
        player.active = None
        _, preset = ott.validate_preset(presets.config(), "local")
        with self.assertRaisesRegex(ott.Error, "invalid profile metadata"):
            sessions.session.verify(ott, player, "dev_tv", preset)
        self.assertTrue(all(action in ("settings_compare", "status", "profiles") for _, action, _ in player.calls))


if __name__ == "__main__":
    unittest.main()
