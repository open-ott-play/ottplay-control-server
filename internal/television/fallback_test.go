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
	failurePath  string
	nullReadings bool
	onAfterReads int
	onAfterCalls int
	wakeCalls    int
	wakeReads    int
	wakePending  bool
	// Invoked with mu held, before writing a state response.
	stateReadHook func()
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
			if h.stateReadHook != nil {
				h.stateReadHook()
			}
			if h.wakePending {
				h.wakeReads++
				if h.wakeReads >= h.onAfterReads {
					h.power = "on"
					h.wakePending = false
				}
			}
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
		if h.postFailure || h.failurePath == r.URL.Path {
			http.Error(w, "uncertain", 503)
			return
		}
		switch r.URL.Path {
		case "/api/services/media_player/volume_set":
			h.volume = data["volume_level"].(float64)
		case "/api/services/media_player/volume_mute":
			h.muted = data["is_volume_muted"].(bool)
		case "/api/services/media_player/turn_on":
			h.wakeCalls++
			if h.wakeCalls < h.onAfterCalls {
				break
			}
			if h.onAfterReads > 0 {
				h.wakePending = true
			} else {
				h.power = "on"
			}
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

func TestNamedAppIsResolvedBeforeWakeOrFallback(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	f.config.MAC = "02:03:04:05:06:07"
	f.config.AppAliases = map[string]string{"plex": "cdp-30"}
	m := f.manager(t)
	wakes := 0
	m.sendWake = func(context.Context, string, string, int) error { wakes++; return nil }
	for _, command := range []Command{{Action: "go", App: "unknown"}, {Action: "launch", App: "cdp-30"}} {
		_, err := m.Execute(context.Background(), "living", command)
		assertCode(t, err, "app_not_configured", false)
	}
	for _, command := range []Command{{Action: "off", App: "plex"}, {Action: "on", App: "plex"}, {Action: "launch", App: "https://example.org"}} {
		_, err := m.Execute(context.Background(), "living", command)
		assertCode(t, err, "invalid_command", false)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if wakes != 0 || h.verified || len(h.posts) != 0 || f.count(launchApp) != 0 {
		t.Fatal("invalid or unknown application reached wake/native/fallback transport")
	}
}

func TestFallbackUsesSelectedNamedAppAndPreservesDefault(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	h.power = "off"
	f.config.AppAliases = map[string]string{"plex": "cdp-30"}
	m := f.manager(t)
	r, err := m.Execute(context.Background(), "living", Command{Action: "go", App: "plex"})
	if err != nil || r.Outcome != "confirmed" || r.Transport != "home_assistant" || r.State == nil || r.State.AppID != "cdp-30" {
		t.Fatalf("fallback ignored selected app: result=%+v error=%v", r, err)
	}
	r, err = m.Execute(context.Background(), "living", Command{Action: "launch"})
	if err != nil || r.State == nil || r.State.AppID != "de.benzac.msx" {
		t.Fatalf("fallback replaced default app: result=%+v error=%v", r, err)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.posts) != 3 || h.posts[0].path != "/api/services/media_player/turn_on" || h.posts[1].data["payload"].(map[string]any)["id"] != "cdp-30" || h.posts[2].data["payload"].(map[string]any)["id"] != "de.benzac.msx" {
		t.Fatalf("unexpected wake/launch requests: %v", h.posts)
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
	m.sendWake = func(context.Context, string, string, int) error { return nil }
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
	m.sendWake = func(context.Context, string, string, int) error {
		f.mu.Lock()
		f.power = "Active"
		f.mu.Unlock()
		return nil
	}
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
	m.sendWake = func(context.Context, string, string, int) error { return nil }
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
	m.sendWake = func(context.Context, string, string, int) error { return nil }
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

func TestLongWakeBudgetsUseCallerDeadline(t *testing.T) {
	m, err := New(nil)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Second)
	defer cancel()
	native, nativeCancel := m.wakeProbeContext(ctx, true)
	defer nativeCancel()
	deadline, _ := native.Deadline()
	if remaining := time.Until(deadline); remaining < 35*time.Second || remaining > 40*time.Second {
		t.Fatalf("native wake did not reserve meaningful half-budget: %s", remaining)
	}
	withoutFallback, noFallbackCancel := m.wakeProbeContext(ctx, false)
	defer noFallbackCancel()
	deadline, _ = withoutFallback.Deadline()
	if remaining := time.Until(deadline); remaining < 75*time.Second || remaining > 80*time.Second {
		t.Fatalf("direct wake ignored long caller deadline: %s", remaining)
	}
	ha, haCancel := fallbackReadinessContext(ctx)
	defer haCancel()
	deadline, _ = ha.Deadline()
	if remaining := time.Until(deadline); remaining < 75*time.Second || remaining > 80*time.Second {
		t.Fatalf("HA readiness retained an early fixed cutoff: %s", remaining)
	}
}

func TestDelayedNativeWakeWithFallbackLaunchesOnlyOnce(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	if err := writeCredentials(f.config.ClientKeyFile, credentials{ClientKey: "fixture-paired-key"}); err != nil {
		t.Fatal(err)
	}
	f.power = "Power Off"
	f.config.MAC = "02:03:04:05:06:07"
	m := f.manager(t)
	m.wakeWait = 150 * time.Millisecond
	m.wakeInterval = time.Millisecond
	var timer *time.Timer
	m.sendWake = func(context.Context, string, string, int) error {
		timer = time.AfterFunc(25*time.Millisecond, func() { f.mu.Lock(); f.power = "Active"; f.mu.Unlock() })
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	r, err := m.Execute(ctx, "living", Command{Action: "go"})
	if timer != nil {
		timer.Stop()
	}
	if err != nil || r.Transport != "webos" || r.Outcome != "confirmed" || f.count(launchApp) != 1 {
		t.Fatalf("delayed native wake failed: result=%+v error=%v", r, err)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.verified || len(h.posts) != 0 {
		t.Fatal("fallback ran before native wake budget was used")
	}
}

func TestFallbackGoWaitsForDelayedReadinessBeforeOneLaunch(t *testing.T) {
	f := newTV(t, false)
	h := newHA(t, f)
	h.power = "off"
	h.onAfterReads = 4
	client := &fallbackClient{c: *f.config.Fallback, token: "ha-fixture-token", http: h.server.Client(), readyPollInterval: time.Millisecond}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := client.verifyNative(ctx); err != nil {
		t.Fatal(err)
	}
	r := Result{Version: 1, DeviceID: "living", Action: "go", Transport: "home_assistant", Outcome: "accepted"}
	if err := client.execute(ctx, f.config, Command{Action: "go"}, &r); err != nil {
		t.Fatal(err)
	}
	if r.Outcome != "confirmed" || r.State.AppID != f.config.AppID {
		t.Fatalf("delayed HA readiness failed: %+v", r)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.wakeReads < 4 || len(h.posts) != 2 || h.posts[1].path != "/api/services/webostv/command" {
		t.Fatalf("unexpected wake/launch order: reads=%d posts=%v", h.wakeReads, h.posts)
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

func TestFallbackWOLRetryRequiresOptInAndStopsAtObservedWake(t *testing.T) {
	for _, retry := range []bool{false, true} {
		t.Run(map[bool]string{false: "default-single-request", true: "opted-in-retry"}[retry], func(t *testing.T) {
			f := newTV(t, false)
			h := newHA(t, f)
			h.power = "off"
			h.onAfterCalls = 3
			f.config.Fallback.WOLRetry = retry
			// Make every post-wake probe eligible for retry; cancellation is
			// driven by requests, not the speed of the CI runner.
			client := &fallbackClient{c: *f.config.Fallback, token: "ha-fixture-token", http: h.server.Client(), readyPollInterval: time.Millisecond, wakeRetryInterval: time.Nanosecond}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if !retry {
				reads := 0
				h.stateReadHook = func() {
					if h.wakeCalls > 0 {
						reads++
						if reads == 4 {
							cancel()
						}
					}
				}
			}
			if err := client.verifyNative(ctx); err != nil {
				t.Fatal(err)
			}
			r := Result{Version: 1, DeviceID: "living", Action: "go", Transport: "home_assistant", Outcome: "accepted"}
			err := client.execute(ctx, f.config, Command{Action: "go"}, &r)
			if retry {
				if err != nil || r.Outcome != "confirmed" || r.State == nil || r.State.AppID != f.config.AppID {
					t.Fatalf("retry did not wake and launch: result=%+v error=%v", r, err)
				}
			} else {
				assertCode(t, err, "not_ready", false)
				if ctx.Err() != context.Canceled {
					t.Fatal("safety deadline expired before fixture cancellation")
				}
			}
			h.mu.Lock()
			count := len(h.posts)
			wakes := h.wakeCalls
			last := h.posts[count-1].path
			h.mu.Unlock()
			if retry && (wakes != 3 || count != 4 || last != "/api/services/webostv/command") {
				t.Fatalf("unexpected retry/launch order: wakes=%d posts=%d last=%s", wakes, count, last)
			}
			if !retry && (wakes != 1 || count != 1) {
				t.Fatalf("retried without administrator opt-in: wakes=%d posts=%d", wakes, count)
			}
			time.Sleep(15 * time.Millisecond)
			h.mu.Lock()
			defer h.mu.Unlock()
			if len(h.posts) != count {
				t.Fatal("fallback continued sending after command returned")
			}
		})
	}
}

func TestFallbackWOLRetryStopsOnCancellationOrAmbiguousMutation(t *testing.T) {
	for _, failure := range []string{"cancel", "wake", "launch"} {
		t.Run(failure, func(t *testing.T) {
			f := newTV(t, false)
			h := newHA(t, f)
			h.power = "off"
			f.config.Fallback.WOLRetry = true
			if failure == "cancel" {
				h.onAfterCalls = 1000
			} else if failure == "wake" {
				h.failurePath = "/api/services/media_player/turn_on"
			} else {
				h.onAfterCalls = 2
				h.failurePath = "/api/services/webostv/command"
			}
			client := &fallbackClient{c: *f.config.Fallback, token: "ha-fixture-token", http: h.server.Client(), readyPollInterval: time.Millisecond, wakeRetryInterval: time.Nanosecond}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if failure == "cancel" {
				h.stateReadHook = func() {
					if h.wakeCalls == 3 {
						cancel()
					}
				}
			}
			if err := client.verifyNative(ctx); err != nil {
				t.Fatal(err)
			}
			r := Result{Version: 1, DeviceID: "living", Action: "go", Transport: "home_assistant", Outcome: "accepted"}
			err := client.execute(ctx, f.config, Command{Action: "go"}, &r)
			if failure == "cancel" {
				assertCode(t, err, "not_ready", false)
				if ctx.Err() != context.Canceled {
					t.Fatal("safety deadline expired before fixture cancellation")
				}
			} else {
				assertCode(t, err, "fallback_unavailable", false)
			}
			if !client.mutationAttempted {
				t.Fatal("uncertain mutation was not recorded for the response wrapper")
			}
			h.mu.Lock()
			count := len(h.posts)
			wakes := h.wakeCalls
			h.mu.Unlock()
			if failure == "wake" && count != 1 {
				t.Fatal("ambiguous wake was replayed")
			}
			if failure == "launch" && (wakes != 2 || count != 3) {
				t.Fatalf("launch was replayed: wakes=%d posts=%d", wakes, count)
			}
			if failure == "cancel" && (wakes != 3 || count != 3) {
				t.Fatalf("cancellation did not stop after third wake: wakes=%d posts=%d", wakes, count)
			}
			time.Sleep(15 * time.Millisecond)
			h.mu.Lock()
			defer h.mu.Unlock()
			if len(h.posts) != count {
				t.Fatal("fallback continued sending after cancellation or ambiguous mutation")
			}
		})
	}
}
