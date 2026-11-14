package config

import "testing"

func TestLGChannelsCountryRequiresExplicitSupportedRegion(t *testing.T) {
	base := televisionConfig(t).Televisions[0]
	for _, country := range []string{"", "US"} {
		c := base
		c.LGChannelsCountry = country
		if err := c.Validate(); err != nil {
			t.Fatalf("%q: %v", country, err)
		}
	}
	for _, country := range []string{"us", "AU", "US\n", "https://example.invalid", "US:120"} {
		c := base
		c.LGChannelsCountry = country
		if c.Validate() == nil {
			t.Fatalf("accepted %q", country)
		}
	}
}
