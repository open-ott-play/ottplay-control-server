package config

import "testing"

func TestMultiviewExactPermissionsOnly(t *testing.T) {
	for _, operation := range []string{"status", "sources", "layout", "play", "focus", "audio", "stop", "off"} {
		if !ValidCommandAction("multiview." + operation) {
			t.Fatalf("missing exact multiview permission %q", operation)
		}
	}
	for _, action := range []string{"multiview", "multiview.*", "multiview.on", "multiview.get", "multiview.FOCUS", "multiview.focus.audio", "multiview.status sources"} {
		if ValidCommandAction(action) {
			t.Fatalf("unscoped or invented multiview permission %q accepted", action)
		}
	}
	for _, action := range []string{"play", "play_catalog", "input.pip", "media_tracks.select", "playback.pause", "lifecycle.standby"} {
		if !ValidCommandAction(action) {
			t.Fatalf("legacy permission %q changed", action)
		}
	}
}
