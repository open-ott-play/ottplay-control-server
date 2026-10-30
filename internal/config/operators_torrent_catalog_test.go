package config

import "testing"

func TestTorrentCatalogExactPermissions(t *testing.T) {
	for _, op := range []string{"read", "read_chunk", "metadata", "status", "cancel", "write_begin", "write_chunk", "write_commit", "play"} {
		if !ValidCommandAction("torrent_catalog." + op) {
			t.Fatal("missing exact torrent permission", op)
		}
	}
	for _, action := range []string{"torrent_catalog", "torrent_catalog.*", "torrent_catalog.write", "torrent_catalog.fetch", "torrent_catalog.endpoint", "torrent_catalog.status.extra"} {
		if ValidCommandAction(action) {
			t.Fatal("unscoped or invented torrent permission accepted", action)
		}
	}
}
