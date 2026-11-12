package control

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/television"
)

type channelDelegationFake struct {
	fakeTelevisionControl
	enabled   bool
	enabledID string
}

func (f *channelDelegationFake) Binding(id string) television.Binding {
	return television.Binding{Version: 1, DeviceID: id, Configured: true, PlayerChannelControl: f.enabled && (f.enabledID == "" || f.enabledID == id)}
}

func TestPlayerChannelsRequireExplicitOwnDeviceDelegation(t *testing.T) {
	s := newTestServer(t)
	f := &channelDelegationFake{}
	s.televisions = f
	expect(t, request(s, "POST", televisionChannelsPath, firstToken, `{"action":"channels"}`, nil), 403)
	f.enabled = true
	f.enabledID = "first"
	expect(t, request(s, "POST", televisionChannelsPath, secondToken, `{"action":"channels"}`, nil), 403)
	for _, token := range []string{"", adminToken} {
		w := request(s, "POST", televisionChannelsPath, token, `{"action":"channels"}`, nil)
		if w.Code != 401 && w.Code != 403 {
			t.Fatalf("invalid role admitted: %d", w.Code)
		}
	}
	f.execute = func(_ context.Context, id string, c television.Command) (television.Result, error) {
		if id != "first" || c.Action != "channels" {
			t.Fatalf("wrong scope: %s %+v", id, c)
		}
		return television.Result{DeviceID: id, Action: c.Action}, nil
	}
	expect(t, request(s, "POST", televisionChannelsPath, firstToken, `{"action":"channels"}`, nil), 200)
	if f.calls.Load() != 1 {
		t.Fatal("invalid admission reached TV")
	}
	if len(s.devices[0].queue) != 0 || !s.devices[0].lastSeen.IsZero() {
		t.Fatal("channel request altered player state")
	}
}

func TestPlayerChannelsRejectPrivilegeEscalationAndAmbiguity(t *testing.T) {
	s := newTestServer(t)
	f := &channelDelegationFake{enabled: true}
	s.televisions = f
	for _, body := range []string{
		`{"action":"pair"}`, `{"action":"off"}`, `{"action":"launch","app":"plex"}`,
		`{"action":"channels","device_id":"second"}`, `{"action":"channels","action":"channel_select"}`,
		`{"action":"channel_select"}`, `{"action":"channel_select","channel_id":"one","channel_number":"2"}`,
		`{"action":"channels","channel_number":"2"}`, `{"action":"channels","timeout_ms":10001}`,
		`{"action":"channels","timeout_ms":null}`, `{"action":"channels","channel_id":null}`,
	} {
		expect(t, request(s, "POST", televisionChannelsPath, firstToken, body, nil), 400)
	}
	for _, path := range []string{televisionChannelsPath + "/", televisionChannelsPath + "?device_id=second", televisionChannelsPath + "?"} {
		expect(t, request(s, "POST", path, firstToken, `{"action":"channels"}`, nil), 400)
	}
	expect(t, request(s, "GET", televisionChannelsPath, firstToken, "", nil), 405)
	expect(t, request(s, "POST", televisionChannelsPath, firstToken, `{"action":"channels"}`, map[string]string{"Origin": "https://attacker.example"}), 403)
	if f.calls.Load() != 0 {
		t.Fatal("rejected request reached TV")
	}
}

func TestPlayerChannelSelectionDoesNotRetryUnknownOutcome(t *testing.T) {
	s := newTestServer(t)
	f := &channelDelegationFake{enabled: true}
	s.televisions = f
	f.execute = func(_ context.Context, id string, c television.Command) (television.Result, error) {
		if id != "second" || c.Action != "channel_select" || c.ChannelID != "selected-channel" {
			t.Fatalf("wrong target: %s %+v", id, c)
		}
		return television.Result{}, errors.New("private transport details")
	}
	w := request(s, "POST", televisionChannelsPath, secondToken, `{"action":"channel_select","channel_id":"selected-channel"}`, nil)
	expect(t, w, 200)
	if f.calls.Load() != 1 || !strings.Contains(w.Body.String(), `"uncertain":true`) || strings.Contains(w.Body.String(), "private") {
		t.Fatal(w.Body.String())
	}
}

func TestPlayerChannelsShareCapacityWithAdministrativeTVControl(t *testing.T) {
	s := newTestServer(t)
	f := &channelDelegationFake{enabled: true}
	s.televisions = f
	s.televisionSlots = make(chan struct{}, 1)
	s.televisionSlots <- struct{}{}
	expect(t, request(s, "POST", televisionChannelsPath, firstToken, `{"action":"channels"}`, nil), 503)
	if f.calls.Load() != 0 {
		t.Fatal("capacity bypass")
	}
}
