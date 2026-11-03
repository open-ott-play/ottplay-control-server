"""Native provider profile selection stays separate from profile configuration."""
import contextlib
import copy
import io
import json
import unittest
from unittest import mock

import test_profiles_cli as profiles


ott = profiles.ott
PROVIDERS = ("plex", "xtream", "stalker")
PRIVATE = "private-native-profile-token"
PRIVATE_URL = "https://private.invalid/source?token=" + PRIVATE


def row(number=1, active=True, **changes):
    value = {"number": number, "name": "Profile " + str(number), "active": active,
             "history_hours": 0, "playlist_configured": False}
    value.update(changes)
    return value


def listing(provider, active=3):
    return {"provider": provider,
            "profiles": [row(number, active=number == active) for number in range(1, 16)]}


class NativeProfilesCLITest(unittest.TestCase):
    def execute(self, words, response, json_output=False):
        result = profiles.ProfileOutputTest().run_command(words, response, json_output)
        self.assertNotIn(PRIVATE, result[1] + result[2])
        self.assertNotIn(PRIVATE_URL, result[1] + result[2])
        return result

    def test_all_three_providers_list_fifteen_ordered_slots_with_zero_or_one_active(self):
        for provider in PROVIDERS:
            for active in (None, 3):
                with self.subTest(provider=provider, active=active):
                    data = listing(provider, active)
                    data["profiles"][0]["name"] = ""
                    data["profiles"][1]["history_hours"] = None
                    status, output, errors, calls = self.execute(["profiles"], data, True)
                    self.assertEqual((status, errors), (0, ""))
                    self.assertEqual(json.loads(output), data)
                    self.assertEqual(calls, [mock.call("dev_tv", "profiles", {})])

    def test_public_projection_excludes_extra_secret_fields(self):
        for provider in PROVIDERS:
            safe = listing(provider)
            data = copy.deepcopy(safe)
            data["source"] = PRIVATE_URL
            data["credentials"] = {"token": PRIVATE}
            for item in data["profiles"]:
                item.update(source=PRIVATE_URL, token=PRIVATE, private={"source": PRIVATE_URL})
            with self.subTest(provider=provider):
                status, output, errors, calls = self.execute(["profs"], data, True)
                self.assertEqual((status, errors), (0, ""))
                self.assertEqual(json.loads(output), safe)
                self.assertEqual(calls, [mock.call("dev_tv", "profiles", {})])

    def test_plain_listing_uses_names_and_active_marker_without_m3u_labels(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                status, output, errors, calls = self.execute(["profiles"], listing(provider))
                self.assertEqual((status, errors), (0, ""))
                for number in range(1, 16):
                    self.assertIn(str(number) + ": Profile " + str(number), output)
                self.assertIn("* 3: Profile 3", output)
                self.assertNotIn("playlist:", output.casefold())
                self.assertNotIn("history:", output.casefold())
                self.assertEqual(calls, [mock.call("dev_tv", "profiles", {})])

    def test_requested_active_profile_selection_is_usable_through_real_cli_route(self):
        for provider in PROVIDERS:
            for number in (1, 15):
                with self.subTest(provider=provider, number=number):
                    public = {"provider": provider, "profile": row(number), "dispatched": True}
                    response = copy.deepcopy(public)
                    response.update(reloaded=True, source=PRIVATE_URL)
                    response["profile"]["token"] = PRIVATE
                    status, output, errors, calls = self.execute(["prof", str(number)], response, True)
                    self.assertEqual((status, errors), (0, ""))
                    self.assertEqual(json.loads(output), public)
                    self.assertEqual(calls, [mock.call("dev_tv", "profile", {"number": number})])
                    status, output, errors, _ = self.execute(["profile", str(number)], response)
                    self.assertEqual((status, errors), (0, ""))
                    self.assertIn("Profile switch requested: " + str(number) + ": Profile " + str(number), output)

    def test_invalid_slot_shape_bounds_and_listing_consistency_remain_rejected(self):
        mutations = [lambda d: d["profiles"].pop(),
                     lambda d: d["profiles"].append(row(16, False)),
                     lambda d: d["profiles"].reverse(),
                     lambda d: d["profiles"][1].update(number=1),
                     lambda d: d["profiles"][0].update(active=True),
                     lambda d: d["profiles"][0].pop("history_hours")]
        fields = (("number", True), ("number", 1.0), ("number", 0), ("number", 16),
                  ("active", 1), ("active", None), ("name", None), ("name", "\ud800"),
                  ("name", "я" * 129), ("playlist_configured", True),
                  ("playlist_configured", 0), ("playlist_configured", None),
                  ("history_hours", True), ("history_hours", 1.0),
                  ("history_hours", -1), ("history_hours", 8761))
        mutations.extend(lambda d, field=field, value=value: d["profiles"][0].update({field: value})
                         for field, value in fields)
        for provider in PROVIDERS:
            for index, mutate in enumerate(mutations):
                with self.subTest(provider=provider, mutation=index):
                    data = listing(provider)
                    mutate(data)
                    status, output, errors, calls = self.execute(["profiles"], data, True)
                    self.assertEqual((status, output), (1, ""))
                    self.assertIn("invalid profile metadata", errors)
                    self.assertEqual(calls, [mock.call("dev_tv", "profiles", {})])

    def test_valid_name_and_history_boundaries_remain_accepted(self):
        for provider in PROVIDERS:
            data = listing(provider)
            data["profiles"][0].update(name="я" * 128, history_hours=8760)
            data["profiles"][1].update(name="😀" * 64, history_hours=None)
            with self.subTest(provider=provider):
                self.assertEqual(ott.profile_metadata(data, "profiles", {}), data)

    def test_selection_needs_exact_number_active_and_literal_dispatch_ack(self):
        mutations = (lambda d: d["profile"].update(number=1),
                     lambda d: d["profile"].update(active=False),
                     lambda d: d["profile"].update(playlist_configured=True),
                     lambda d: d.update(dispatched=False),
                     lambda d: d.update(dispatched=1),
                     lambda d: d.pop("dispatched"))
        for provider in PROVIDERS:
            for index, mutate in enumerate(mutations):
                with self.subTest(provider=provider, mutation=index):
                    data = {"provider": provider, "profile": row(2), "dispatched": True}
                    mutate(data)
                    status, output, errors, calls = self.execute(["profile", "2"], data, True)
                    self.assertEqual((status, output), (1, ""))
                    self.assertIn("may have executed", errors)
                    self.assertEqual(calls, [mock.call("dev_tv", "profile", {"number": 2})])

    def test_additional_providers_cannot_acknowledge_settings_or_override_expected_provider(self):
        for provider in PROVIDERS:
            for action, params in (
                ("profile_settings", {"number": 1, "settings": {"name": "Profile 1"}}),
                ("profile_save", {"number": 1, "name": "Profile 1"}),
                ("profile_import", {"number": 1, "profile": {"name": "Profile 1", "sources": [], "selected": None}}),
            ):
                with self.subTest(provider=provider, action=action), self.assertRaises(ott.Error):
                    ott.profile_metadata({"provider": provider, "profile": row(), "saved": True}, action, params)
            for expected in ("m3u", "vportal", "offline"):
                with self.subTest(provider=provider, expected=expected), self.assertRaises(ott.Error):
                    ott.profile_metadata(listing(provider), "profiles", {}, expected_provider=expected)
                with self.assertRaises(ott.Error):
                    ott.profile_metadata({"provider": provider, "profile": row(), "dispatched": True},
                                         "profile", {"number": 1}, expected_provider=expected)
        for provider in ("unknown", "Plex", "local"):
            with self.subTest(provider=provider), self.assertRaises(ott.Error):
                ott.profile_metadata(listing(provider), "profiles", {})

    def test_dynamic_help_only_advertises_native_profiles_on_webos_native(self):
        for provider in PROVIDERS:
            for platform in ("webos-native", "webos", "browser", "tauri", "capacitor-android"):
                for locked in (False, True):
                    with self.subTest(provider=provider, platform=platform, locked=locked):
                        data = {"provider": provider, "kiosk": {"enabled": locked},
                                "capabilities": {"player": {"platform": platform},
                                                 "lifecycle": [], "playback": [], "input": []}}
                        output = io.StringIO()
                        with contextlib.redirect_stdout(output):
                            ott.print_player_help(data, "lg-native")
                        text = output.getvalue()
                        self.assertEqual("ott lg-native profiles " in text, platform == "webos-native")
                        self.assertEqual("ott lg-native profile N " in text, platform == "webos-native" and not locked)
                        self.assertNotIn("ott lg-native profile save", text)
                        self.assertNotIn("ott lg-native profile import", text)


if __name__ == "__main__":
    unittest.main()
