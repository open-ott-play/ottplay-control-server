package main

import (
	"context"
	"errors"
	"net"
	"os"
	"strings"
	"sync/atomic"
)

type dialContextFunc func(context.Context, string, string) (net.Conn, error)

// KitKat has no resolv.conf. A static Linux build otherwise sends DNS to
// localhost instead of the Wi-Fi resolver stored in Android properties.
// Read these again for every new connection so a network change is respected.
func androidDialContext(ctx context.Context, network, address string) (net.Conn, error) {
	dialer := &net.Dialer{}
	if _, err := os.Stat("/system/bin/getprop"); err == nil {
		read := func(key string) string {
			b, _ := runCommand(ctx, "/system/bin/getprop", key)
			return string(b)
		}
		resolver, err := androidResolver(read, (&net.Dialer{}).DialContext)
		if err != nil {
			return nil, err
		}
		dialer.Resolver = resolver
	}
	return dialer.DialContext(ctx, network, address)
}

func androidResolver(read func(string) string, dial dialContextFunc) (*net.Resolver, error) {
	var servers []string
	seen := map[string]bool{}
	for _, key := range []string{"net.dns1", "net.dns2", "net.dns3", "net.dns4"} {
		ip := net.ParseIP(strings.TrimSpace(read(key)))
		if ip == nil || ip.IsUnspecified() || ip.IsMulticast() {
			continue
		}
		server := net.JoinHostPort(ip.String(), "53")
		if !seen[server] {
			seen[server] = true
			servers = append(servers, server)
		}
	}
	if len(servers) == 0 {
		return nil, errors.New("Android network DNS unavailable")
	}
	var next atomic.Uint32
	return &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
		server := servers[(next.Add(1)-1)%uint32(len(servers))]
		return dial(ctx, network, server)
	}}, nil
}
