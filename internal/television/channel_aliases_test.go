package television

import (
	"context"
	"reflect"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func TestChannelAliasSharesCanonicalIdentityKeyGateAndConsent(t *testing.T) {
	f := newTV(t, true)
	f.config.ChannelPlayerIDs = []string{"native"}
	f.config.PlayerChannelControl = true
	m := f.manager(t)
	if m.devices["native"] != m.devices["living"] {
		t.Fatal("alias does not share the physical TV owner")
	}
	b := m.Binding("native")
	expected := []string{"channels", "channel_current", "channel_program", "channel_select", "pair"}
	if b.DeviceID != "native" || !b.Configured || !b.PlayerChannelControl || !reflect.DeepEqual(b.Capabilities, expected) || b.FallbackConfigured {
		t.Fatalf("alias metadata exposes incorrect scope: %+v", b)
	}
	if !m.Binding("living").Configured || !m.Binding("native").ChannelPairingRequired {
		t.Fatal("canonical or shared consent metadata lost")
	}
	_, err := m.Execute(context.Background(), "native", Command{Action: "channels"})
	assertCode(t, err, "channel_pairing_required", false)
	r, err := m.Execute(context.Background(), "native", Command{Action: "pair"})
	if err != nil || r.DeviceID != "native" || r.Pairing == nil || !r.Pairing.Paired {
		t.Fatalf("alias administrator pairing failed: %+v %v", r, err)
	}
	if m.Binding("living").ChannelPairingRequired || m.Binding("native").ChannelPairingRequired {
		t.Fatal("consent was not shared through the canonical key")
	}
	f.hook = func(conn *websocket.Conn, q tvRequest) bool {
		switch q.URI {
		case "ssap://tv/getChannelList":
			respond(conn, q, map[string]any{"returnValue": true, "channelList": []any{map[string]any{"channelId": "opaque", "channelName": "News", "channelNumber": "1"}}})
		case "ssap://tv/openChannel":
			respond(conn, q, map[string]any{"returnValue": true})
		case "ssap://tv/getCurrentChannel":
			respond(conn, q, map[string]any{"returnValue": true, "channelId": "opaque", "channelName": "News", "channelNumber": "1"})
		default:
			return false
		}
		return true
	}
	r, err = m.Execute(context.Background(), "native", Command{Action: "channels"})
	if err != nil || len(r.Channels) != 1 || r.DeviceID != "native" {
		t.Fatalf("alias list %+v %v", r, err)
	}
	r, err = m.Execute(context.Background(), "native", Command{Action: "channel_select", ChannelID: "opaque"})
	if err != nil || r.Outcome != "confirmed" || r.DeviceID != "native" {
		t.Fatalf("alias tune %+v %v", r, err)
	}
	// Both identities serialize against exactly the same gate; no second session/key.
	shared := m.devices["living"]
	<-shared.gate
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	_, err = m.Execute(ctx, "native", Command{Action: "pair"})
	shared.gate <- struct{}{}
	assertCode(t, err, "deadline", false)
}

func TestChannelAliasCannotAcquireCanonicalTVPrivileges(t *testing.T) {
	f := newTV(t, true)
	f.config.ChannelPlayerIDs = []string{"native"}
	m := f.manager(t)
	for _, cmd := range []Command{{Action: "status"}, {Action: "apps"}, {Action: "volume"}, {Action: "mute", Muted: boolPointer(true)}, {Action: "off"}, {Action: "on"}, {Action: "launch"}, {Action: "go"}} {
		_, err := m.Execute(context.Background(), "native", cmd)
		assertCode(t, err, "channel_alias_restricted", false)
	}
	f.mu.Lock()
	requests := len(f.requests)
	f.mu.Unlock()
	if requests != 0 {
		t.Fatal("restricted alias reached television transport")
	}
	if m.Binding("native").PlayerChannelControl {
		t.Fatal("alias silently enabled ordinary player access")
	}
	r, err := m.Execute(context.Background(), "living", Command{Action: "status"})
	if err != nil || r.State == nil || r.State.Volume == nil {
		t.Fatalf("canonical controls regressed: %+v %v", r, err)
	}
	// New detaches caller-owned alias lists, just like its credential configuration.
	f.config.ChannelPlayerIDs[0] = "replacement"
	if !m.Binding("native").Configured || m.Binding("replacement").Configured {
		t.Fatal("caller mutated active alias mapping")
	}
}
