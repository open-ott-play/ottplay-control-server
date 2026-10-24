package config

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
)

// CommandOperator is independent from diagnostic consent and runtime credentials.
type CommandOperator struct {
	ID               string   `json:"id"`
	CredentialSHA256 string   `json:"credential_sha256"`
	DeviceIDs        []string `json:"device_ids"`
	Actions          []string `json:"actions"`
}

// Composite actions require the exact operation, never a family-wide wildcard.
var CommandOperations = map[string]string{
	"command":   "popup_message channel_by_number channel_by_name random_channel change_provider change_provider_settings change_playlist set_volume exit_player",
	"input":     "up down left right ok back menu settings channels guide info channel_up channel_down volume_up volume_down mute play_pause audio aspect zoom pip fullscreen",
	"playback":  "pause resume previous_channel next_channel step_channel seek",
	"lifecycle": "restart_stream reload_player restart_app standby wake exit_app reboot_device",
	"restart":   "stream player", "kiosk": "status off on set", "aspect": "get set",
	"plex_queue": "status preview play next previous stop", "plex_library": "list preview play",
	"favorites": "list add play remove", "vportal_queue": "status next previous restart stop play",
	"maintenance": "health logs recover_video update", "app_update": "status prepare install",
	"categories":    "list select",
	"notifications": "get set",
}

func ValidCommandAction(action string) bool {
	switch action {
	case "status", "providers", "profiles", "epg_catalog", "capabilities", "screenshot", "inspect", "profile", "profile_settings", "profile_save", "profile_export", "profile_import", "play_catalog", "resolve_archive", "play_archive_catalog", "play", "provider", "vportal", "vportal_random", "vportal_search", "channels", "programs", "provider_settings", "settings_compare", "playback_handoff_export", "playback_handoff_import":
		return true
	}
	family, operation, ok := strings.Cut(action, ".")
	return ok && operation != "" && strings.Contains(" "+CommandOperations[family]+" ", " "+operation+" ")
}

func (c Config) validateOperators() error {
	if len(c.Operators) > 64 {
		return errors.New("too many command operators")
	}
	devices, hashes, ids := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, d := range c.Devices {
		devices[d.ID] = true
		h := sha256.Sum256([]byte(d.Token))
		hashes[hex.EncodeToString(h[:])] = true
	}
	h := sha256.Sum256([]byte(c.AdminToken))
	hashes[hex.EncodeToString(h[:])] = true
	if c.Diagnostics != nil {
		for _, o := range c.Diagnostics.Operators {
			hashes[o.CredentialSHA256] = true
		}
	}
	for _, o := range c.Operators {
		b, e := hex.DecodeString(o.CredentialSHA256)
		if !diagnosticLabel.MatchString(o.ID) || ids[o.ID] || e != nil || len(b) != 32 || hex.EncodeToString(b) != o.CredentialSHA256 || hashes[o.CredentialSHA256] {
			return errors.New("invalid or duplicate command operator identity or credential digest")
		}
		ids[o.ID], hashes[o.CredentialSHA256] = true, true
		if len(o.DeviceIDs) == 0 || len(o.DeviceIDs) > MaxDevices || len(o.Actions) == 0 || len(o.Actions) > 128 {
			return errors.New("command operator requires bounded device and action scopes")
		}
		seen := map[string]bool{}
		for _, id := range o.DeviceIDs {
			if !devices[id] || seen[id] {
				return errors.New("invalid command operator device scope")
			}
			seen[id] = true
		}
		seen = map[string]bool{}
		for _, action := range o.Actions {
			if !ValidCommandAction(action) || seen[action] {
				return errors.New("invalid command operator action scope")
			}
			seen[action] = true
		}
	}
	return nil
}
