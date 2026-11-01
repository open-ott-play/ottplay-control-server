package control

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/television"
)

type fakeTelevisionControl struct {
	calls   atomic.Int32
	execute func(context.Context, string, television.Command) (television.Result, error)
}

func (f *fakeTelevisionControl) Binding(id string) television.Binding {
	return television.Binding{Version: 1, DeviceID: id, Configured: true, Paired: true, Capabilities: []string{"status", "volume", "off", "go"}}
}
func (f *fakeTelevisionControl) Execute(ctx context.Context, id string, cmd television.Command) (television.Result, error) {
	f.calls.Add(1)
	if f.execute != nil {
		return f.execute(ctx, id, cmd)
	}
	return television.Result{Version: 1, DeviceID: id, Action: cmd.Action, Transport: "webos", Outcome: "accepted"}, nil
}

func TestTelevisionAdmissionDoesNotDependOnPlayerPolling(t *testing.T) {
	s := newTestServer(t)
	fake := &fakeTelevisionControl{}
	s.televisions = fake
	for _, token := range []string{"", firstToken, secondToken} {
		w := request(s, "POST", "/api/televisions", token, `{"device_id":"first","action":"off"}`, nil)
		if w.Code != http.StatusUnauthorized && w.Code != http.StatusForbidden {
			t.Fatalf("unexpected admission: %d", w.Code)
		}
	}
	if fake.calls.Load() != 0 {
		t.Fatal("unprivileged request reached the physical TV")
	}
	w := request(s, "GET", "/api/televisions?device_id=first", adminToken, "", nil)
	expect(t, w, 200)
	if !strings.Contains(w.Body.String(), `"configured":true`) {
		t.Fatal(w.Body.String())
	}
	w = request(s, "POST", "/api/televisions", adminToken, `{"device_id":"first","action":"off"}`, nil)
	expect(t, w, 200)
	if fake.calls.Load() != 1 || !s.devices[0].lastSeen.IsZero() {
		t.Fatal("TV control incorrectly requires or changes player presence")
	}
	if len(s.devices[0].queue) != 0 {
		t.Fatal("native control leaked into the player queue")
	}
	s.devices = s.devices[1:]
	expect(t, request(s, "POST", "/api/televisions", adminToken, `{"device_id":"first","action":"off"}`, nil), 404)
	if fake.calls.Load() != 1 {
		t.Fatal("inactive identity reached physical control")
	}
}

func TestTelevisionRejectsAmbiguousOrArbitraryCommands(t *testing.T) {
	s := newTestServer(t)
	fake := &fakeTelevisionControl{}
	s.televisions = fake
	for _, body := range []string{
		`{"device_id":"first","action":"off","action":"on"}`,
		`{"device_id":"first","action":"launch","url":"http://attacker.example"}`,
		`{"device_id":"first","action":"launch","app":null}`,
		`{"device_id":"first","action":"launch","app":"plex","app":"other"}`,
		`{"device_id":"first","action":"launch","app":"http://attacker.example"}`,
		`{"device_id":"first","action":"off","app":"plex"}`,
		`{"device_id":"first","action":"volume","volume":101}`,
		`{"device_id":"first","action":"volume","volume":20,"delta":5}`,
		`{"device_id":"first","action":"volume","volume":null}`,
		`{"device_id":"first","action":"mute","muted":1}`,
		`{"device_id":"first","action":"off","timeout_ms":120001}`,
		`{"device_id":"first","action":"ssap://arbitrary"}`,
		`{"device_id":"first","action":"off"} {}`,
	} {
		expect(t, request(s, "POST", "/api/televisions", adminToken, body, nil), 400)
	}
	expect(t, request(s, "POST", "/api/televisions?device_id=second", adminToken, `{"device_id":"first","action":"off"}`, nil), 400)
	expect(t, request(s, "GET", "/api/televisions?device_id=first&device_id=second", adminToken, "", nil), 400)
	expect(t, request(s, "GET", "/api/televisions/?device_id=first", adminToken, "", nil), 400)
	expect(t, request(s, "GET", "/api/televisions?device_id=first", firstToken, "", nil), 403)
	expect(t, request(s, "POST", "/api/televisions", adminToken, `{"device_id":"first","action":"off"}`, map[string]string{"Origin": "https://attacker.example"}), 403)
	if fake.calls.Load() != 0 {
		t.Fatal("invalid input reached the TV")
	}
}

func TestTelevisionForwardsNamedAppWithoutChangingPlayerQueue(t *testing.T) {
	s := newTestServer(t)
	fake := &fakeTelevisionControl{execute: func(_ context.Context, id string, cmd television.Command) (television.Result, error) {
		if id != "first" || cmd.Action != "go" || cmd.App != "plex" {
			t.Fatalf("named app was not preserved: %q %+v", id, cmd)
		}
		return television.Result{Version: 1, DeviceID: id, Action: cmd.Action, Transport: "webos", Outcome: "accepted"}, nil
	}}
	s.televisions = fake
	expect(t, request(s, "POST", "/api/televisions", adminToken, `{"device_id":"first","action":"go","app":"plex"}`, nil), 200)
	if fake.calls.Load() != 1 || len(s.devices[0].queue) != 0 {
		t.Fatal("named application launch entered the player command queue")
	}
}

func TestTelevisionUncertaintyIsReturnedWithoutRetry(t *testing.T) {
	s := newTestServer(t)
	fake := &fakeTelevisionControl{execute: func(context.Context, string, television.Command) (television.Result, error) {
		return television.Result{}, &television.Error{Code: "operation_uncertain", Uncertain: true}
	}}
	s.televisions = fake
	w := request(s, "POST", "/api/televisions", adminToken, `{"device_id":"first","action":"volume","delta":5}`, nil)
	expect(t, w, 200)
	var result map[string]any
	if json.Unmarshal(w.Body.Bytes(), &result) != nil || result["uncertain"] != true || result["code"] != "operation_uncertain" {
		t.Fatal(w.Body.String())
	}
	if fake.calls.Load() != 1 {
		t.Fatal("uncertain mutation was retried")
	}
}

type televisionDeadlineRecorder struct {
	*httptest.ResponseRecorder
	deadline time.Time
}

func (w *televisionDeadlineRecorder) SetWriteDeadline(deadline time.Time) error {
	w.deadline = deadline
	return nil
}

func TestTelevisionLongRequestHasScopedDeadlineAndCapacity(t *testing.T) {
	s := newTestServer(t)
	fake := &fakeTelevisionControl{execute: func(ctx context.Context, _ string, _ television.Command) (television.Result, error) {
		deadline, ok := ctx.Deadline()
		if !ok || time.Until(deadline) > 60*time.Second {
			t.Error("request context is not bounded")
		}
		return television.Result{}, &television.Error{Code: "deadline"}
	}}
	s.televisions = fake
	w := &televisionDeadlineRecorder{ResponseRecorder: httptest.NewRecorder()}
	r := httptest.NewRequest("POST", "/api/televisions", strings.NewReader(`{"device_id":"first","action":"pair","timeout_ms":60000}`))
	r.Header.Set("Authorization", "Bearer "+adminToken)
	r.Header.Set("Content-Type", "application/json")
	s.ServeHTTP(w, r)
	if time.Until(w.deadline) < 59*time.Second || time.Until(w.deadline) > 62*time.Second {
		t.Fatal("pairing did not get its own bounded write deadline")
	}
	s.televisionSlots = make(chan struct{}, 1)
	s.televisionSlots <- struct{}{}
	expect(t, request(s, "POST", "/api/televisions", adminToken, `{"device_id":"first","action":"go"}`, nil), 503)
	if fake.calls.Load() != 1 {
		t.Fatal("capacity limit was bypassed")
	}
}

func TestTelevisionUnexpectedErrorDoesNotClaimNoEffect(t *testing.T) {
	s := newTestServer(t)
	s.televisions = &fakeTelevisionControl{execute: func(context.Context, string, television.Command) (television.Result, error) {
		return television.Result{}, errors.New("private transport detail must not escape")
	}}
	w := request(s, "POST", "/api/televisions", adminToken, `{"device_id":"first","action":"off"}`, nil)
	expect(t, w, 200)
	if !strings.Contains(w.Body.String(), `"uncertain":true`) || strings.Contains(w.Body.String(), "private") {
		t.Fatal("unexpected mutation failure was not safely reported")
	}
}
