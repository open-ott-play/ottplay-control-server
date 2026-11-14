package television

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func enableChannels(t *testing.T, f *tvFixture) {
	t.Helper()
	key, err := readCredentials(f.config.ClientKeyFile)
	if err != nil {
		t.Fatal(err)
	}
	key.TVChannels = true
	if err = writeCredentials(f.config.ClientKeyFile, key); err != nil {
		t.Fatal(err)
	}
}
func TestChannelValidation(t *testing.T) {
	valid := []Command{{Action: "channels"}, {Action: "channel_program", ChannelID: "id/1"}, {Action: "channel_select", ChannelID: "id/1"}, {Action: "channel_select", ChannelNumber: "100-2", ChannelMode: "Terrestrial"}}
	for _, c := range valid {
		if err := c.Validate(); err != nil {
			t.Fatalf("valid command %+v: %v", c, err)
		}
	}
	invalid := []Command{{Action: "status", ChannelID: "id"}, {Action: "channel_select"}, {Action: "channel_select", ChannelID: "id", ChannelNumber: "1", ChannelMode: "Terrestrial"}, {Action: "channel_select", ChannelNumber: "1"}, {Action: "channel_select", ChannelNumber: "1", ChannelMode: "Cable"}, {Action: "channel_select", ChannelID: "\n"}, {Action: "channel_select", ChannelID: strings.Repeat("x", 513)}, {Action: "channel_current", ChannelNumber: "1"}, {Action: "channels", Muted: boolPointer(true)}}
	for _, c := range invalid {
		assertCode(t, c.Validate(), "invalid_command", false)
	}
}
func TestChannelScopeRequiresExplicitPair(t *testing.T) {
	f := newTV(t, true)
	m := f.manager(t)
	if !m.Binding("living").ChannelPairingRequired {
		t.Fatal("legacy key falsely channel-capable")
	}
	_, err := m.Execute(context.Background(), "living", Command{Action: "channels"})
	assertCode(t, err, "channel_pairing_required", false)
	if f.count("tv/getChannelList") != 0 {
		t.Fatal("requested catalogue before consent")
	}
	r, err := m.Execute(context.Background(), "living", Command{Action: "pair"})
	if err != nil || !r.Pairing.Paired {
		t.Fatalf("pair: %+v %v", r, err)
	}
	if m.Binding("living").ChannelPairingRequired {
		t.Fatal("scope not persisted")
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	registration := f.requests[len(f.requests)-1]
	if registration.Payload["forcePairing"] != true || registration.Payload["client-key"] != nil {
		t.Fatal("did not request fresh scope consent")
	}
	manifest := registration.Payload["manifest"].(map[string]any)
	permissions := manifest["permissions"].([]any)
	for _, p := range channelPermissions {
		found := false
		for _, x := range permissions {
			found = found || x == p
		}
		if !found {
			t.Fatalf("missing permission %s", p)
		}
	}
}
func TestSystemChannelListTuneAndProgramme(t *testing.T) {
	f := newTV(t, true)
	enableChannels(t, f)
	f.hook = func(conn *websocket.Conn, q tvRequest) bool {
		switch q.URI {
		case "ssap://tv/getChannelList":
			respond(conn, q, map[string]any{"returnValue": true, "channelList": []any{map[string]any{"channelId": "opaque/42", "channelName": "Channel", "channelNumber": "42", "channelMode": "Terrestrial"}}})
		case "ssap://tv/getCurrentChannel":
			respond(conn, q, map[string]any{"returnValue": true, "channelId": "opaque/42", "channelName": "Channel", "channelNumber": "42", "channelMode": "Terrestrial"})
		case "ssap://tv/getChannelCurrentProgramInfo":
			respond(conn, q, map[string]any{"returnValue": true, "programId": "show", "programName": "Current show", "startTime": "2026-11-12T11:00:00"})
		case "ssap://tv/openChannel":
			respond(conn, q, map[string]any{"returnValue": true})
		default:
			return false
		}
		return true
	}
	m := f.manager(t)
	ctx := context.Background()
	r, err := m.Execute(ctx, "living", Command{Action: "channels"})
	if err != nil || len(r.Channels) != 1 || r.Channels[0].ID != "opaque/42" {
		t.Fatalf("list %+v %v", r, err)
	}
	r, err = m.Execute(ctx, "living", Command{Action: "channel_select", ChannelID: "opaque/42"})
	if err != nil || r.Outcome != "confirmed" {
		t.Fatalf("tune %+v %v", r, err)
	}
	r, err = m.Execute(ctx, "living", Command{Action: "channel_select", ChannelNumber: "42", ChannelMode: "Terrestrial"})
	if err != nil || r.Outcome != "confirmed" {
		t.Fatalf("FAST tune %+v %v", r, err)
	}
	r, err = m.Execute(ctx, "living", Command{Action: "channel_program", ChannelID: "opaque/42"})
	if err != nil || r.Program.Title != "Current show" {
		t.Fatalf("program %+v %v", r, err)
	}
	_, err = m.Execute(ctx, "living", Command{Action: "channel_program", ChannelID: "other"})
	assertCode(t, err, "channel_not_current", false)
	if f.count("tv/openChannel") != 2 {
		t.Fatal("programme read tuned a channel")
	}
}
func TestUncertainTuneNeverReplayed(t *testing.T) {
	f := newTV(t, true)
	enableChannels(t, f)
	f.hook = func(conn *websocket.Conn, q tvRequest) bool {
		if q.URI == "ssap://tv/openChannel" {
			_ = conn.CloseNow()
			return true
		}
		return false
	}
	r, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "channel_select", ChannelID: "opaque"})
	if err == nil || r.Outcome != "uncertain" {
		t.Fatalf("result %+v %v", r, err)
	}
	if f.count("tv/openChannel") != 1 {
		t.Fatal("tune replayed")
	}
}
func TestTuneAcceptedDoesNotMeanCurrent(t *testing.T) {
	f := newTV(t, true)
	enableChannels(t, f)
	f.hook = func(conn *websocket.Conn, q tvRequest) bool {
		if q.URI == "ssap://tv/openChannel" {
			respond(conn, q, map[string]any{"returnValue": true})
			return true
		}
		if q.URI == "ssap://tv/getCurrentChannel" {
			respond(conn, q, map[string]any{"returnValue": true, "channelId": "old"})
			return true
		}
		return false
	}
	r, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "channel_select", ChannelID: "new"})
	if err != nil || r.Outcome != "accepted" || r.Channel.ID != "old" {
		t.Fatalf("result %+v %v", r, err)
	}
}

func TestEmptyChannelCatalogueAndMalformedRows(t *testing.T) {
	for _, kind := range []string{"empty", "missing_id", "duplicate_id"} {
		t.Run(kind, func(t *testing.T) {
			f := newTV(t, true)
			enableChannels(t, f)
			f.hook = func(conn *websocket.Conn, q tvRequest) bool {
				if q.URI != "ssap://tv/getChannelList" {
					return false
				}
				list := []any{}
				switch kind {
				case "missing_id":
					list = append(list, map[string]any{"channelName": "missing id"})
				case "duplicate_id":
					list = append(list, map[string]any{"channelId": "id"}, map[string]any{"channelId": "id"})
				}
				respond(conn, q, map[string]any{"returnValue": true, "channelList": list})
				return true
			}
			r, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "channels"})
			if kind != "empty" {
				assertCode(t, err, "protocol_error", false)
				return
			}
			if err != nil || r.Channels == nil || len(r.Channels) != 0 {
				t.Fatalf("empty %+v %v", r, err)
			}
		})
	}
}
func TestProgrammeRejectsChannelRace(t *testing.T) {
	f := newTV(t, true)
	enableChannels(t, f)
	reads := 0
	f.hook = func(conn *websocket.Conn, q tvRequest) bool {
		switch q.URI {
		case "ssap://tv/getCurrentChannel":
			reads++
			id := "first"
			if reads > 1 {
				id = "second"
			}
			respond(conn, q, map[string]any{"returnValue": true, "channelId": id})
		case "ssap://tv/getChannelCurrentProgramInfo":
			respond(conn, q, map[string]any{"returnValue": true, "programName": "Mismatched programme"})
		default:
			return false
		}
		return true
	}
	_, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "channel_program"})
	assertCode(t, err, "channel_changed", false)
}

// Channel reads have no HA fallback; reserving half the request budget for one
// cuts a valid multi-call programme read short even while its caller can wait.
func TestChannelProgrammeKeepsCallerBudgetWhenHAFallbackConfigured(t *testing.T) {
	f := newTV(t, true)
	key, err := readCredentials(f.config.ClientKeyFile)
	if err != nil {
		t.Fatal(err)
	}
	h := newHA(t, f)
	key.TVChannels = true
	if err := writeCredentials(f.config.ClientKeyFile, key); err != nil {
		t.Fatal(err)
	}
	f.hook = func(conn *websocket.Conn, q tvRequest) bool {
		switch q.URI {
		case "ssap://tv/getCurrentChannel":
			time.Sleep(400 * time.Millisecond)
			respond(conn, q, map[string]any{"returnValue": true, "channelId": "opaque/42"})
		case "ssap://tv/getChannelCurrentProgramInfo":
			time.Sleep(400 * time.Millisecond)
			respond(conn, q, map[string]any{"returnValue": true, "programName": "Current show"})
		default:
			return false
		}
		return true
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	result, err := f.manager(t).Execute(ctx, "living", Command{Action: "channel_program"})
	if err != nil || result.Program == nil || result.Program.Title != "Current show" {
		t.Fatalf("valid programme read lost its caller budget: %+v %v", result, err)
	}
	if ctx.Err() != nil {
		t.Fatal("programme exceeded caller deadline")
	}
	if f.count("tv/getCurrentChannel") != 2 || f.count("tv/getChannelCurrentProgramInfo") != 1 {
		t.Fatal("programme read did not retain its channel-race verification")
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.verified || len(h.posts) != 0 {
		t.Fatal("channel read attempted unsupported HA fallback")
	}
}
