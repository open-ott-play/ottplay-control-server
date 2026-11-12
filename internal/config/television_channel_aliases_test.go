package config

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"
)

func TestTelevisionChannelPlayerAliasesAreExplicitAndBounded(t *testing.T) {
	base := televisionConfig(t).Televisions[0]
	for _, ids := range [][]string{{"living"}, {"native", "native"}, {""}, {"invalid id"}, {strings.Repeat("n", 129)}} {
		c := base
		c.ChannelPlayerIDs = ids
		if c.Validate() == nil {
			t.Fatalf("invalid channel aliases admitted: %q", ids)
		}
	}
	base.ChannelPlayerIDs = []string{"native-living", "other-ui"}
	if err := base.Validate(); err != nil {
		t.Fatal(err)
	}
	tooMany := base
	for i := 0; i < 17; i++ {
		tooMany.ChannelPlayerIDs = append(tooMany.ChannelPlayerIDs, fmt.Sprintf("native-%d", i))
	}
	if tooMany.Validate() == nil {
		t.Fatal("unbounded channel aliases")
	}
	other := base
	other.DeviceID = "bedroom"
	other.Host = "192.0.2.5"
	other.ClientKeyFile += ".bedroom"
	if ValidateTelevisions([]Television{base, other}) == nil {
		t.Fatal("alias shared across televisions")
	}
	other.ChannelPlayerIDs = []string{"living"}
	if ValidateTelevisions([]Television{base, other}) == nil {
		t.Fatal("alias overlaps another canonical identity")
	}
	other.DeviceID = "native-living"
	other.ChannelPlayerIDs = nil
	if ValidateTelevisions([]Television{base, other}) == nil {
		t.Fatal("canonical identity overlaps an earlier alias")
	}
	var many []Television
	for i := 0; i < 4; i++ {
		c := base
		c.DeviceID = fmt.Sprintf("tv-%d", i)
		c.ChannelPlayerIDs = nil
		for j := 0; j < 16; j++ {
			c.ChannelPlayerIDs = append(c.ChannelPlayerIDs, fmt.Sprintf("native-%d-%d", i, j))
		}
		many = append(many, c)
	}
	if ValidateTelevisions(many) == nil {
		t.Fatal("total television identity budget bypassed")
	}
}

func TestTelevisionAliasesFollowDeviceAdmissionWithoutDuplicatingEndpoint(t *testing.T) {
	c := televisionConfig(t)
	c.Televisions[0].ChannelPlayerIDs = []string{"native"}
	if c.Validate() == nil {
		t.Fatal("unknown static alias admitted")
	}
	c.Devices = append(c.Devices, Device{ID: "native", Token: strings.Repeat("c", 32)})
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	if len(c.Televisions) != 1 {
		t.Fatal("alias duplicated television configuration")
	}
	c.Devices = c.Devices[:1]
	c.DeviceRegistry = &DeviceRegistry{Path: filepath.Join(t.TempDir(), "registry.json")}
	if err := c.Validate(); err != nil {
		t.Fatalf("durable registry binding should await authenticated runtime admission: %v", err)
	}
	// The existing endpoint/key isolation remains unchanged with aliases present.
	other := c.Televisions[0]
	other.DeviceID = "another"
	other.ChannelPlayerIDs = nil
	other.ClientKeyFile += ".other"
	c.Televisions = append(c.Televisions, other)
	if c.Validate() == nil {
		t.Fatal("duplicate physical endpoint admitted through alias support")
	}
}
