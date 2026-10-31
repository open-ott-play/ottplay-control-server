package config

import "testing"

func TestTorrentIndexerExactPermissions(t *testing.T) {
	for _, op := range []string{"list", "configure", "remove", "caps", "search", "prepare"} {
		if !ValidCommandAction("torrent_indexer." + op) {
			t.Fatal("missing exact torrent permission", op)
		}
	}
	for _, action := range []string{"torrent_indexer", "torrent_indexer.*", "torrent_indexer.write", "torrent_indexer.fetch", "torrent_indexer.endpoint", "torrent_indexer.status.extra"} {
		if ValidCommandAction(action) {
			t.Fatal("unscoped or invented torrent permission accepted", action)
		}
	}
}
