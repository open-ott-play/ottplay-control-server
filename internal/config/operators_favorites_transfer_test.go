package config

import "testing"

func TestFavoritesTransferBindingPermissionsAreExactAndDistinct(t *testing.T) {
	for _, action := range []string{"favorites_transfer.bindingcatalog", "favorites_transfer.bind"} {
		if !ValidCommandAction(action) {
			t.Fatal("explicit binding permission rejected", action)
		}
	}
	for _, action := range []string{"favorites_transfer", "favorites_transfer.*", "favorites_transfer.bind.*", "favorites_transfer.create", "favorites_transfer.rebind", "favorites.bind", "favorites.bindingcatalog"} {
		if ValidCommandAction(action) {
			t.Fatal("unscoped, legacy or unsupported permission admitted", action)
		}
	}
	for _, action := range []string{"favorites.list", "favorites.add", "favorites.play", "favorites.remove", "favorites_transfer.snapshot", "favorites_transfer.commit"} {
		if !ValidCommandAction(action) {
			t.Fatal("existing distinct permission removed", action)
		}
	}
}
