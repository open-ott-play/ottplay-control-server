package config

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestPlayerStatePathAndStorageSeparation(t *testing.T) {
	base := Config{AdminToken: strings.Repeat("a", 32), Devices: []Device{{ID: "first", Token: strings.Repeat("b", 32)}}}
	base.Defaults()
	path := filepath.Join(t.TempDir(), "private", "state.json")
	for _, bad := range []string{"", "relative.json", filepath.VolumeName(path) + string(filepath.Separator), path + string(filepath.Separator)} {
		if (PlayerState{Path: bad}).Validate() == nil {
			t.Fatal("accepted invalid path", bad)
		}
	}
	base.PlayerState = &PlayerState{Path: path}
	if e := base.Validate(); e != nil {
		t.Fatal(e)
	}
	for _, other := range []string{path, path + ".lock"} {
		c := base
		c.DeviceRegistry = &DeviceRegistry{Path: other}
		if c.Validate() == nil {
			t.Fatal("accepted overlapping storage")
		}
	}
	base.PlayerState = &PlayerState{Path: path + ".lock"}
	base.DeviceRegistry = &DeviceRegistry{Path: path}
	if base.Validate() == nil {
		t.Fatal("accepted data over lock")
	}
}
