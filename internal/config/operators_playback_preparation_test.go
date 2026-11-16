package config

import "testing"

func TestPlaybackPreparationExactPermission(t *testing.T) {
	if !ValidCommandAction("playback_preparation_diagnostics") {
		t.Fatal("missing passive snapshot permission")
	}
	for _, bad := range []string{"playback_preparation_diagnostics.*", "playback_preparation_diagnostics.start", "playback_preparation_diagnostics.read", "playback_preparation_diagnostics ", "PLAYBACK_PREPARATION_DIAGNOSTICS"} {
		if ValidCommandAction(bad) {
			t.Fatalf("invalid snapshot scope accepted: %q", bad)
		}
	}
}
