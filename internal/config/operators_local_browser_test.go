package config

import "testing"

func TestLocalBrowserPermissionsAreExact(t *testing.T) {
	for _, action := range []string{"local_browser.roots", "local_browser.list", "local_browser.more", "local_browser.select", "local_media.connect", "local_media.play", "local_media.shuffle"} {
		if !ValidCommandAction(action) {
			t.Fatal("missing exact operation", action)
		}
	}
	for _, action := range []string{"local_browser", "local_browser.*", "local_browser.ROOTS", "local_browser.scan", "local_browser.list more", "local_browser.select.save", "local_media.source_handle"} {
		if ValidCommandAction(action) {
			t.Fatal("invented/wildcard operation accepted", action)
		}
	}
}
