package config

import "testing"

func TestDiscoveryValidation(t *testing.T) {
	for _, d := range []Discovery{
		{Domain: "alvit.cf", Nameserver: "192.168.160.1:53", PublicURL: "https://www.example.test/ott-control"},
		{PublicURL: "https://example.test"},
		{Domain: "example.test.", Nameserver: "[::1]:5353", PublicURL: "https://[::1]:8443/control"},
	} {
		if err := d.Validate(); err != nil {
			t.Fatal(err)
		}
	}
	for _, d := range []Discovery{
		{Domain: "_bad.example", PublicURL: "https://example.test"},
		{Domain: "example.test?resolver=evil", PublicURL: "https://example.test"},
		{Nameserver: "resolver.test:53", PublicURL: "https://example.test"},
		{Nameserver: "127.0.0.1:0", PublicURL: "https://example.test"},
		{PublicURL: "http://example.test"}, {PublicURL: "https://a:b@example.test"},
		{PublicURL: "https://example.test/a?token=secret"}, {PublicURL: "https://example.test/a#b"},
		{PublicURL: "https://example.test/a//b"}, {PublicURL: "https://example.test/../a"},
		{PublicURL: "https://example.test/%2e%2e"}, {PublicURL: "https://example.test:"},
		{PublicURL: "https://example.test:99999"}, {PublicURL: "https://bad_host.test"},
		{PublicURL: "https://example.test/?"}, {PublicURL: "https://example.test\\evil"},
	} {
		if err := d.Validate(); err == nil {
			t.Fatalf("accepted invalid discovery config: %+v", d)
		}
	}
	for input, want := range map[string]string{"https://EXAMPLE.test.:443/base/": "https://example.test/base", "https://[::1]:443/": "https://[::1]", "https://example.test:8443/": "https://example.test:8443"} {
		got, err := HTTPSBase(input)
		if err != nil || got != want {
			t.Fatalf("canonical URL %q: %q %v", input, got, err)
		}
	}
}
