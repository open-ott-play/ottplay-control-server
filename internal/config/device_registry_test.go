package config

import (
	"fmt"
	"path/filepath"
	"testing"
)

func TestDeviceRegistryPathAndAliasValidation(t *testing.T) {
	if (DeviceRegistry{Path: "relative.json"}).Validate() == nil {
		t.Fatal("relative store path accepted")
	}
	if (DeviceRegistry{Path: filepath.Join(t.TempDir(), "devices.json")}).Validate() != nil {
		t.Fatal("absolute store path rejected")
	}
	d := Discovery{PublicURL: "https://controller.example/base", PublicAliases: []string{"https://hosted.example/base"}}
	if d.Validate() != nil {
		t.Fatal("valid alias rejected")
	}
	for _, aliases := range [][]string{
		{"http://hosted.example/base"},
		{"https://controller.example:443/base/"},
		{"https://hosted.example/base", "https://HOSTED.example:443/base/"},
		{"https://hosted.example/base?x=1"},
	} {
		d.PublicAliases = aliases
		if d.Validate() == nil {
			t.Fatal("unsafe alias accepted")
		}
	}
	d.PublicAliases = nil
	for i := 0; i < 9; i++ {
		d.PublicAliases = append(d.PublicAliases, fmt.Sprintf("https://alias%d.example", i))
	}
	if d.Validate() == nil {
		t.Fatal("unbounded aliases accepted")
	}
}
