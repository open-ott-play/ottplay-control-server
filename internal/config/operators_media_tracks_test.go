package config

import (
	"strings"
	"testing"
)

func TestMediaTracksExactPermissionsOnly(t *testing.T) {
	for _, action := range []string{"media_tracks.list", "media_tracks.select"} {
		if !ValidCommandAction(action) {
			t.Fatalf("missing exact media tracks permission %q", action)
		}
	}
	for _, action := range []string{"media_tracks", "media_tracks.*", "media_tracks.get", "media_tracks.audio", "media_tracks.off", "media_tracks.select.audio", "media_tracks.LIST", "media_tracks.list select"} {
		if ValidCommandAction(action) {
			t.Fatalf("unscoped or invented media tracks permission %q accepted", action)
		}
	}
	for _, action := range []string{"local_media.play", "aspect.get", "player_settings.get", "input.audio"} {
		if !ValidCommandAction(action) {
			t.Fatalf("legacy permission %q changed", action)
		}
	}
}

func TestCommandOperationMembershipIsExact(t *testing.T) {
	for family, operations := range CommandOperations {
		parts := strings.Fields(operations)
		for _, operation := range parts {
			if !ValidCommandAction(family + "." + operation) {
				t.Fatalf("documented individual operation %s.%s rejected", family, operation)
			}
		}
		if len(parts) > 1 && ValidCommandAction(family+"."+strings.Join(parts[:2], " ")) {
			t.Fatalf("combined operations accepted for %s", family)
		}
	}
}
