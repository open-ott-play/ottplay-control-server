package main

import (
	"context"
	"errors"
	"net"
	"reflect"
	"testing"
)

func TestAndroidResolverUsesNetworkProperties(t *testing.T) {
	values := map[string]string{"net.dns1": " 192.168.160.1\n", "net.dns2": "2001:db8::53", "net.dns3": "192.168.160.1", "net.dns4": "bad-host"}
	var targets []string
	wantErr := errors.New("probe")
	ctx := context.WithValue(context.Background(), struct{}{}, "context")
	r, err := androidResolver(func(k string) string { return values[k] }, func(got context.Context, network, address string) (net.Conn, error) {
		if got != ctx {
			t.Fatal("lost context")
		}
		targets = append(targets, network+" "+address)
		return nil, wantErr
	})
	if err != nil || !r.PreferGo {
		t.Fatalf("resolver: %v %v", r, err)
	}
	for _, network := range []string{"udp", "tcp", "udp"} {
		if _, err := r.Dial(ctx, network, "127.0.0.1:53"); err != wantErr {
			t.Fatal(err)
		}
	}
	want := []string{"udp 192.168.160.1:53", "tcp [2001:db8::53]:53", "udp 192.168.160.1:53"}
	if !reflect.DeepEqual(targets, want) {
		t.Fatalf("got %v", targets)
	}
}

func TestAndroidResolverMissingDNSDoesNotUseLocalhost(t *testing.T) {
	for _, value := range []string{"", "dns.example", "0.0.0.0", "::", "224.0.0.1", "ff02::1"} {
		if _, err := androidResolver(func(string) string { return value }, nil); err == nil {
			t.Fatalf("accepted %q", value)
		}
	}
}

func TestAndroidResolverRefreshesForNewConnection(t *testing.T) {
	value := "192.168.160.1"
	read := func(string) string { return value }
	var target string
	dial := func(_ context.Context, _, address string) (net.Conn, error) { target = address; return nil, nil }
	first, err := androidResolver(read, dial)
	if err != nil {
		t.Fatal(err)
	}
	value = "10.0.0.1"
	second, err := androidResolver(read, dial)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = first.Dial(context.Background(), "udp", "ignored")
	if target != "192.168.160.1:53" {
		t.Fatal(target)
	}
	_, _ = second.Dial(context.Background(), "udp", "ignored")
	if target != "10.0.0.1:53" {
		t.Fatal(target)
	}
}
