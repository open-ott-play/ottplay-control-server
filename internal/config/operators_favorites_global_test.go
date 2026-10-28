package config

import "testing"

func TestGlobalFavoritesExactPermissionsOnly(t *testing.T) {
	for _, op := range []string{"describe", "export", "begin", "append", "preview", "commit", "status", "abort"} {
		if !ValidCommandAction("favorites_global." + op) {
			t.Fatal("missing exact global permission", op)
		}
	}
	for _, op := range []string{"inspect", "acknowledge"} {
		if !ValidCommandAction("favorites_global_recovery." + op) {
			t.Fatal("missing exact recovery permission", op)
		}
	}
	for _, action := range []string{"favorites_global", "favorites_global.*", "favorites_global.recovery", "favorites_global_recovery", "favorites_global_recovery.*", "favorites_global_recovery.commit", "favorites_global.acknowledge", "favorites.global"} {
		if ValidCommandAction(action) {
			t.Fatal("unscoped or invented permission accepted", action)
		}
	}
	for _, action := range []string{"favorites.list", "favorites_transfer.commit", "favorites_transfer.recovery", "local_media.play"} {
		if !ValidCommandAction(action) {
			t.Fatal("legacy permission changed", action)
		}
	}
}

func TestGlobalItemsExactPermissionsOnly(t *testing.T) {
	for _, op := range []string{"list", "current", "play"} {
		if !ValidCommandAction("favorites_global_items." + op) {
			t.Fatal("missing items permission", op)
		}
	}
	for _, action := range []string{"favorites_global_items", "favorites_global_items.*", "favorites_global_items.add", "favorites_global_items.remove", "favorites_global_items.commit", "favorites_global_items.recovery"} {
		if ValidCommandAction(action) {
			t.Fatal("invented items permission accepted", action)
		}
	}
}
