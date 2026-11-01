package television

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type haFixture struct {
	server   *httptest.Server
	mu       sync.Mutex
	platform string
	verified bool
	volume   float64
	muted    bool
	power    string
	app      string
	posts    []struct {
		path string
		data map[string]any
	}
	postFailure  bool
	nullReadings bool
}

func newHA(t *testing.T, tv *tvFixture) *haFixture {
	t.Helper()
	h := &haFixture{platform: "webostv", volume: 0.11, power: "on", app: "de.benzac.msx"}
	h.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/websocket" {
			conn, err := websocket.Accept(w, r, nil)
			if err != nil {
				return
			}
			defer conn.CloseNow()
			_ = sendJSON(conn, map[string]any{"type": "auth_required"})
			var req map[string]any
			if readJSON(conn, &req) != nil {
				return
			}
			if req["access_token"] != "ha-fixture-token" {
				t.Error("missing HA token")
				return
			}
			_ = sendJSON(conn, map[string]any{"type": "auth_ok"})
			if readJSON(conn, &req) != nil {
				return
			}
			if req["type"] != "config/entity_registry/get" || req["entity_id"] != "media_player.native_tv" {
				t.Errorf("unexpected registry request: %v", req)
				return
			}
			h.mu.Lock()
			h.verified = true
			platform := h.platform
			h.mu.Unlock()
			_ = sendJSON(conn, map[string]any{"id": 1, "type": "result", "success": true, "result": map[string]any{"platform": platform}})
			return
		}
		if r.Header.Get("Authorization") != "Bearer ha-fixture-token" {
			t.Error("missing HA bearer")
			http.Error(w, "unauthorized", 401)
			return
		}
		h.mu.Lock()
		defer h.mu.Unlock()
		if !h.verified {
			t.Error("HA request before native entity verification")
		}
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodGet && r.URL.Path == "/api/states/media_player.native_tv" {
			attributes := map[string]any{"volume_level": h.volume, "is_volume_muted": h.muted, "app_id": h.app}
			if h.nullReadings {
				attributes["volume_level"] = nil
				attributes["is_volume_muted"] = nil
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"state": h.power, "attributes": attributes})
			return
		}
		if r.Method != http.MethodPost || !strings.HasPrefix(r.URL.Path, "/api/services/") {
			t.Errorf("unexpected path %s", r.URL.Path)
			http.NotFound(w, r)
			return
		}
		var data map[string]any
		if json.NewDecoder(r.Body).Decode(&data) != nil {
			t.Error("invalid request body")
			return
		}
		if data["entity_id"] != "media_player.native_tv" {
			t.Error("wrong entity")
		}
		h.posts = append(h.posts, struct {
			path string
			data map[string]any
		}{r.URL.Path, data})
		if h.postFailure {
			http.Error(w, "uncertain", 503)
			return
		}
		switch r.URL.Path {
		case "/api/services/media_player/volume_set":
			h.volume = data["volume_level"].(float64)
		case "/api/services/media_player/volume_mute":
			h.muted = data["is_volume_muted"].(bool)
		case "/api/services/media_player/turn_on":
			h.power = "on"
		case "/api/services/media_player/turn_off":
			h.power = "off"
		case "/api/services/webostv/command":
			if data["command"] == launchApp {
				h.app = data["payload"].(map[string]any)["id"].(string)
			} else if data["command"] == openURL {
				h.app = "com.webos.app.browser"
			} else {
				t.Error("unapproved command")
			}
		default:
			t.Errorf("unapproved service %s", r.URL.Path)
		}
		_, _ = w.Write([]byte("[]"))
	}))
	t.Cleanup(h.server.Close)
	path := filepath.Join(t.TempDir(), "ha-token")
	writePrivateFixture(t, path, []byte("ha-fixture-token"))
	tv.config.Fallback = &config.TelevisionFallback{URL: h.server.URL, TokenFile: path, EntityID: "media_player.native_tv"}
	// Missing native credentials forces a pre-mutation fallback without
	// depending on a network timeout or contacting any external endpoint.
	if err := os.Remove(tv.config.ClientKeyFile); err != nil {
		t.Fatal(err)
	}
	return h
}

func TestFallbackRequiresNativePlatformAndConvertsDeltaOnce(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	m := f.manager(t)
	r, err := m.Execute(context.Background(), "living", Command{Action: "volume", Delta: intPointer(5)})
	if err != nil || r.Transport != "home_assistant" || r.Outcome != "confirmed" || *r.State.Volume != 16 {
		t.Fatalf("volume=%+v err=%v", r, err)
	}
	h.mu.Lock()
	if len(h.posts) != 1 || h.posts[0].data["volume_level"] != 0.16 {
		t.Fatalf("relative command not converted once: %v", h.posts)
	}
	h.platform = "ottplay"
	h.verified = false
	h.mu.Unlock()
	_, err = m.Execute(context.Background(), "living", Command{Action: "volume", Delta: intPointer(5)})
	assertCode(t, err, "fallback_target_invalid", false)
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.posts) != 1 {
		t.Fatal("posted to recursive OttPlay entity")
	}
}

func TestFallbackOnOffMuteAndFixedLaunch(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	m := f.manager(t)
	for _, cmd := range []Command{{Action: "mute", Muted: boolPointer(true)}, {Action: "off"}, {Action: "go"}} {
		r, err := m.Execute(context.Background(), "living", cmd)
		if err != nil || r.Outcome != "confirmed" || r.Transport != "home_assistant" {
			t.Fatalf("%s=%+v err=%v", cmd.Action, r, err)
		}
	}
	h.mu.Lock()
	if len(h.posts) != 4 {
		t.Fatalf("posts=%v", h.posts)
	}
	last := h.posts[3]
	h.mu.Unlock()
	if last.path != "/api/services/webostv/command" || last.data["command"] != launchApp || last.data["payload"].(map[string]any)["id"] != f.config.AppID {
		t.Fatalf("launch not fixed to configured app: %v", last)
	}
	f.config.AppID = ""
	f.config.LaunchURL = "https://player.ottplay.here.now/"
	r, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "launch"})
	if err != nil || r.Outcome != "accepted" {
		t.Fatalf("browser falsely confirmed: %+v err=%v", r, err)
	}
}

func TestFallbackMutationFailureIsUncertainAndNotRetried(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	h.postFailure = true
	r, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "volume", Delta: intPointer(1)})
	assertCode(t, err, "fallback_unavailable", true)
	if r.Outcome != "uncertain" {
		t.Fatal(r.Outcome)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.posts) != 1 {
		t.Fatalf("replayed uncertain command %d times", len(h.posts))
	}
}

func TestFallbackRejectsRedirectWithoutSendingToken(t *testing.T) {
	f := newTV(t, false)
	_ = newHA(t, f)
	called := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		t.Error("redirect target received request")
	}))
	defer target.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()
	f.config.Fallback.URL = redirect.URL
	_, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "status"})
	assertCode(t, err, "fallback_unavailable", false)
	if called {
		t.Fatal("followed redirect")
	}
}

func TestWakeReservesShortCallerBudgetForFallback(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	h.power = "off"
	f.config.MAC = "02:03:04:05:06:07"
	m := f.manager(t)
	m.wakeInterval = time.Millisecond
	m.sendWake = func(string, string, int) error { return nil }
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	r, err := m.Execute(ctx, "living", Command{Action: "on"})
	if err != nil || r.Transport != "home_assistant" || r.Outcome != "confirmed" || r.State.Power != "on" {
		t.Fatalf("fallback starved after WOL: result=%+v error=%v", r, err)
	}
	if ctx.Err() != nil {
		t.Fatal("fallback exceeded caller deadline")
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.posts) != 1 || h.posts[0].path != "/api/services/media_player/turn_on" {
		t.Fatalf("wake posts=%v", h.posts)
	}
}

func TestSilentNativeReadReservesBudgetForFallback(t *testing.T) {
	f := newTV(t, false)
	_ = newHA(t, f)
	if err := writeCredentials(f.config.ClientKeyFile, credentials{ClientKey: "fixture-paired-key"}); err != nil {
		t.Fatal(err)
	}
	f.hook = func(conn *websocket.Conn, req tvRequest) bool { return req.URI == "ssap://"+getPower }
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	r, err := f.manager(t).Execute(ctx, "living", Command{Action: "status"})
	if err != nil || r.Transport != "home_assistant" || r.Outcome != "confirmed" {
		t.Fatalf("fallback starved after silent read: result=%+v error=%v", r, err)
	}
}

func TestWakeDoesNotReplayAfterLaunchMayHaveApplied(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	if err := writeCredentials(f.config.ClientKeyFile, credentials{ClientKey: "fixture-paired-key"}); err != nil {
		t.Fatal(err)
	}
	f.power = "Power Off"
	f.config.MAC = "02:03:04:05:06:07"
	f.hook = func(conn *websocket.Conn, req tvRequest) bool {
		if req.URI == "ssap://"+launchApp {
			_ = conn.CloseNow()
			return true
		}
		return false
	}
	m := f.manager(t)
	m.sendWake = func(string, string, int) error { f.mu.Lock(); f.power = "Active"; f.mu.Unlock(); return nil }
	r, err := m.Execute(context.Background(), "living", Command{Action: "go"})
	assertCode(t, err, "connection_failed", true)
	if r.Outcome != "uncertain" {
		t.Fatal(r.Outcome)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.verified || len(h.posts) != 0 {
		t.Fatal("HA fallback ran after native launch attempt")
	}
}

func TestGoKeepsWOLPartialEffectWhenFallbackCannotRun(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	h.platform = "ottplay"
	f.config.MAC = "02:03:04:05:06:07"
	m := f.manager(t)
	m.wakeWait = time.Millisecond
	m.wakeInterval = time.Millisecond
	m.sendWake = func(string, string, int) error { return nil }
	r, err := m.Execute(context.Background(), "living", Command{Action: "go"})
	assertCode(t, err, "fallback_target_invalid", true)
	if r.Outcome != "uncertain" {
		t.Fatal("WOL partial effect was lost on fallback failure")
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if !h.verified || len(h.posts) != 0 {
		t.Fatal("fallback routing or target rejection was bypassed")
	}
	if f.count(launchApp) != 0 {
		t.Fatal("launch attempted before readiness")
	}
}

func TestGoCanFinishThroughFallbackAfterWOL(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	h.power = "off"
	f.config.MAC = "02:03:04:05:06:07"
	m := f.manager(t)
	m.wakeWait = time.Millisecond
	m.wakeInterval = time.Millisecond
	m.sendWake = func(string, string, int) error { return nil }
	r, err := m.Execute(context.Background(), "living", Command{Action: "go"})
	if err != nil || r.Outcome != "confirmed" || r.Transport != "home_assistant" || r.State.AppID != f.config.AppID {
		t.Fatalf("successful fallback after WOL lost: result=%+v error=%v", r, err)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.posts) != 2 || h.posts[0].path != "/api/services/media_player/turn_on" || h.posts[1].path != "/api/services/webostv/command" {
		t.Fatalf("unexpected fallback requests: %v", h.posts)
	}
}

func TestFallbackNullReadingsCannotConfirmOrBecomeRelativeBaseline(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	h.nullReadings = true
	m := f.manager(t)
	_, err := m.Execute(context.Background(), "living", Command{Action: "volume", Delta: intPointer(1)})
	assertCode(t, err, "unsupported", false)
	h.mu.Lock()
	count := len(h.posts)
	h.mu.Unlock()
	if count != 0 {
		t.Fatal("used null as HA relative volume baseline")
	}
	r, err := m.Execute(context.Background(), "living", Command{Action: "volume", Volume: intPointer(0)})
	if err != nil || r.Outcome != "accepted" || r.State.Volume != nil {
		t.Fatalf("null falsely confirmed zero volume: %+v, %v", r, err)
	}
	r, err = m.Execute(context.Background(), "living", Command{Action: "mute", Muted: boolPointer(false)})
	if err != nil || r.Outcome != "accepted" || r.State.Muted != nil {
		t.Fatalf("null falsely confirmed unmute: %+v, %v", r, err)
	}
}
