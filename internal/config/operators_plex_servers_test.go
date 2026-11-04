package config

import "testing"

func TestPlexServersExactPermissionsOnly(t *testing.T) {
	for _, action := range []string{"plex_servers.list", "plex_servers.select"} {
		if !ValidCommandAction(action) {
			t.Fatalf("missing managed Plex permission %q", action)
		}
	}
	for _, action := range []string{"plex_servers", "plex_servers.*", "plex_servers.play", "plex_servers.get", "plex_servers.SELECT", "plex_servers.list select", "plex_servers.select.server"} {
		if ValidCommandAction(action) {
			t.Fatalf("unscoped or invented permission %q accepted", action)
		}
	}
	for _, action := range []string{"profile", "plex_library.list", "plex_library.play", "plex_queue.play", "local_media.play"} {
		if !ValidCommandAction(action) {
			t.Fatalf("legacy permission %q changed", action)
		}
	}
}
