package config

import "testing"

func TestFavoritesRecoveryPermissionIsExactAndDoesNotReplaceExistingGrants(t *testing.T) {
	if !ValidCommandAction("favorites_transfer.recovery") {
		t.Fatal("new explicit recovery permission missing")
	}
	for _, action := range []string{"favorites_transfer.recovery.*", "favorites_transfer.recovery.acknowledge", "favorites_transfer.*", "favorites.recovery", "favorites_transfer.acknowledge", "favorites_transfer.reset"} {
		if ValidCommandAction(action) {
			t.Fatal("implicit/unfrozen recovery permission accepted", action)
		}
	}
	for _, action := range []string{"favorites.list", "favorites_transfer.commit", "favorites_transfer.status", "favorites_transfer.prepare", "favorites_transfer.provision"} {
		if !ValidCommandAction(action) {
			t.Fatal("recovery altered an existing permission", action)
		}
	}
}
