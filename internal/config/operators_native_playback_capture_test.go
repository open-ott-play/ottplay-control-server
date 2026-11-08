package config

import "testing"

func TestNativePlaybackCaptureExactPermissionsOnly(t *testing.T) {
	for _, operation := range []string{"start", "status", "read", "stop"} {
		if !ValidCommandAction("native_playback_diagnostics." + operation) {
			t.Fatalf("native capture permission missing: %s", operation)
		}
	}
	for _, action := range []string{
		"native_playback_diagnostics", "native_playback_diagnostics.*", "native_playback_diagnostics.export",
		"native_playback_diagnostics.START", "native_playback_diagnostics.read stop", "native_playback_diagnostics.read ",
		"native_playback_capture.read", "native_playback_diagnostics.status.runtime",
	} {
		if ValidCommandAction(action) {
			t.Fatalf("broad or unknown native capture permission accepted: %s", action)
		}
	}
	if !ValidCommandAction("inspect") || !ValidCommandAction("perf_capture.read") {
		t.Fatal("existing diagnostics authority changed")
	}
}
