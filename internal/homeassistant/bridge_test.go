package homeassistant

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	coderws "github.com/coder/websocket"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
	"golang.org/x/net/websocket"
)

const fixtureToken = "fixture_token_not_a_real_secret_123456789"

func makeToken(t *testing.T, token string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "ha-token")
	if err := os.WriteFile(path, []byte(token+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func runBridge(t *testing.T, c config.HomeAssistant, deliver func(Notification) DeliveryResult, adjust func(*Bridge)) (*Bridge, func()) {
	t.Helper()
	b := New(c, deliver)
	b.timing.handshake = time.Second
	b.timing.tokenPoll = 10 * time.Millisecond
	b.timing.backoff = func(int) time.Duration { return 10 * time.Millisecond }
	if adjust != nil {
		adjust(b)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); b.Run(ctx) }()
	stop := func() {
		cancel()
		select {
		case <-done:
		case <-time.After(time.Second):
			t.Error("bridge did not stop promptly")
		}
	}
	t.Cleanup(stop)
	return b, stop
}

func until(t *testing.T, check func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if check() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("condition was not reached")
}

func auth(t *testing.T, ws *websocket.Conn) (string, bool) {
	t.Helper()
	_ = ws.SetDeadline(time.Now().Add(3 * time.Second))
	if websocket.JSON.Send(ws, map[string]any{"type": "auth_required", "ha_version": "test"}) != nil {
		return "", false
	}
	var a struct {
		Type        string `json:"type"`
		AccessToken string `json:"access_token"`
	}
	if websocket.JSON.Receive(ws, &a) != nil {
		return "", false
	}
	if a.Type != "auth" {
		t.Error("expected authentication message")
		return "", false
	}
	return a.AccessToken, true
}

func subscribed(t *testing.T, ws *websocket.Conn, count int) bool {
	t.Helper()
	if _, ok := auth(t, ws); !ok {
		return false
	}
	if websocket.JSON.Send(ws, map[string]any{"type": "auth_ok"}) != nil {
		return false
	}
	for i := 0; i < count; i++ {
		var command struct {
			ID        int64  `json:"id"`
			Type      string `json:"type"`
			EventType string `json:"event_type"`
		}
		if websocket.JSON.Receive(ws, &command) != nil {
			return false
		}
		if command.ID != int64(i+1) || command.Type != "subscribe_events" || command.EventType == "" {
			t.Error("subscription must use distinct increasing ids and explicit event type")
			return false
		}
		if websocket.JSON.Send(ws, map[string]any{"id": command.ID, "type": "result", "success": true}) != nil {
			return false
		}
	}
	return true
}

func drain(ws *websocket.Conn) {
	for {
		var message envelope
		if websocket.JSON.Receive(ws, &message) != nil {
			return
		}
		if message.Type == "ping" {
			_ = websocket.JSON.Send(ws, map[string]any{"type": "pong", "id": message.ID})
		}
	}
}

func TestWebSocketHandshakeEventsPrefixAndCancellation(t *testing.T) {
	notices := make(chan Notification, 1)
	mux := http.NewServeMux()
	mux.Handle("/ha/api/websocket", websocket.Handler(func(ws *websocket.Conn) {
		if !subscribed(t, ws, 1) {
			return
		}
		_ = websocket.JSON.Send(ws, map[string]any{"type": "event", "id": 1, "event": stateEvent(time.Now(), "binary_sensor.door", state("off"), state("on"))})
		drain(ws)
	}))
	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)
	c := testConfig()
	c.URL, c.TokenFile = server.URL+"/ha/", makeToken(t, fixtureToken)
	b, stop := runBridge(t, c, func(n Notification) DeliveryResult { notices <- n; return DeliveryResult{Queued: 1} }, nil)
	select {
	case n := <-notices:
		if n.Message != "Front door: off → on" {
			t.Fatal("unexpected notification")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no notification after subscribed event")
	}
	until(t, func() bool { return b.Snapshot().NotificationsQueued == 1 })
	stop()
	if b.Snapshot().State != "stopped" {
		t.Fatal("cancellation state was not recorded")
	}
}

func TestRejectedAuthenticationParksUntilTokenChangesAndRedactsStatus(t *testing.T) {
	var connections atomic.Int64
	server := httptest.NewServer(websocket.Handler(func(ws *websocket.Conn) {
		connections.Add(1)
		token, ok := auth(t, ws)
		if !ok {
			return
		}
		if token == fixtureToken {
			_ = websocket.JSON.Send(ws, map[string]any{"type": "auth_invalid", "message": "SECRET: " + token})
			return
		}
		_ = websocket.JSON.Send(ws, map[string]any{"type": "auth_ok"})
		var sub envelope
		if websocket.JSON.Receive(ws, &sub) != nil {
			return
		}
		_ = websocket.JSON.Send(ws, map[string]any{"id": sub.ID, "type": "result", "success": true})
		drain(ws)
	}))
	t.Cleanup(server.Close)
	c := testConfig()
	c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
	b, _ := runBridge(t, c, nil, nil)
	until(t, func() bool { return b.Snapshot().State == "auth_failed" })
	time.Sleep(45 * time.Millisecond)
	if connections.Load() != 1 {
		t.Fatal("rejected token caused a reconnect storm")
	}
	encoded, _ := json.Marshal(b.Snapshot())
	if strings.Contains(string(encoded), fixtureToken) || strings.Contains(string(encoded), c.TokenFile) || strings.Contains(string(encoded), server.URL) || strings.Contains(string(encoded), "SECRET") {
		t.Fatal("status leaked a credential, endpoint, path or remote error")
	}
	if err := os.WriteFile(c.TokenFile+".new", []byte(fixtureToken+"_rotated"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(c.TokenFile+".new", c.TokenFile); err != nil {
		t.Fatal(err)
	}
	until(t, func() bool { return b.Snapshot().State == "connected" })
	if connections.Load() != 2 {
		t.Fatal("rotated token was not used for exactly one new connection")
	}
}

func TestSubscriptionResultMustMatchAndSucceed(t *testing.T) {
	server := httptest.NewServer(websocket.Handler(func(ws *websocket.Conn) {
		if _, ok := auth(t, ws); !ok {
			return
		}
		_ = websocket.JSON.Send(ws, map[string]any{"type": "auth_ok"})
		var sub envelope
		if websocket.JSON.Receive(ws, &sub) != nil {
			return
		}
		_ = websocket.JSON.Send(ws, map[string]any{"id": sub.ID + 100, "type": "result", "success": true})
		_ = websocket.JSON.Send(ws, map[string]any{"id": sub.ID, "type": "result", "success": false, "error": map[string]any{"message": fixtureToken}})
		drain(ws)
	}))
	t.Cleanup(server.Close)
	c := testConfig()
	c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
	b, _ := runBridge(t, c, nil, func(b *Bridge) { b.timing.backoff = func(int) time.Duration { return time.Second } })
	until(t, func() bool { return b.Snapshot().LastErrorCode == "subscription_rejected" })
	if b.Snapshot().State != "reconnecting" {
		t.Fatal("failed subscription was treated as connected")
	}
}

func TestTokenRotationBackToPreviouslyRejectedValueGetsFreshAttempt(t *testing.T) {
	var connections atomic.Int64
	var reconnectOnce sync.Once
	reconnect := make(chan struct{})
	server := httptest.NewServer(websocket.Handler(func(ws *websocket.Conn) {
		connection := connections.Add(1)
		token, ok := auth(t, ws)
		if !ok {
			return
		}
		if connection == 1 {
			_ = websocket.JSON.Send(ws, map[string]any{"type": "auth_invalid"})
			return
		}
		if connection == 2 && token != fixtureToken+"_rotated" || connection == 3 && token != fixtureToken {
			t.Error("connection did not use the current token file")
			return
		}
		_ = websocket.JSON.Send(ws, map[string]any{"type": "auth_ok"})
		var sub envelope
		if websocket.JSON.Receive(ws, &sub) != nil {
			return
		}
		_ = websocket.JSON.Send(ws, map[string]any{"id": sub.ID, "type": "result", "success": true})
		if connection == 2 {
			<-reconnect
			return
		}
		drain(ws)
	}))
	t.Cleanup(server.Close)
	t.Cleanup(func() { reconnectOnce.Do(func() { close(reconnect) }) })
	c := testConfig()
	c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
	b, _ := runBridge(t, c, nil, nil)
	until(t, func() bool { return b.Snapshot().State == "auth_failed" })
	if err := os.WriteFile(c.TokenFile, []byte(fixtureToken+"_rotated"), 0600); err != nil {
		t.Fatal(err)
	}
	until(t, func() bool { return connections.Load() == 2 && b.Snapshot().State == "connected" })
	if err := os.WriteFile(c.TokenFile, []byte(fixtureToken), 0600); err != nil {
		t.Fatal(err)
	}
	reconnectOnce.Do(func() { close(reconnect) })
	until(t, func() bool { return connections.Load() == 3 && b.Snapshot().State == "connected" })
}

func TestReconnectResubscribesWithoutReplayingNotification(t *testing.T) {
	var connections, notices atomic.Int64
	raw := stateEvent(time.Now(), "binary_sensor.door", state("off"), state("on"))
	server := httptest.NewServer(websocket.Handler(func(ws *websocket.Conn) {
		n := connections.Add(1)
		if !subscribed(t, ws, 1) {
			return
		}
		_ = websocket.JSON.Send(ws, map[string]any{"type": "event", "id": 1, "event": raw})
		if n == 1 {
			return // Lost transport forces another auth and confirmed subscription.
		}
		drain(ws)
	}))
	t.Cleanup(server.Close)
	c := testConfig()
	c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
	b, _ := runBridge(t, c, func(Notification) DeliveryResult { notices.Add(1); return DeliveryResult{Queued: 1} }, nil)
	until(t, func() bool { return b.Snapshot().EventsReceived >= 2 })
	if notices.Load() != 1 || b.Snapshot().Reconnects != 1 || b.Snapshot().EventsDropped != 1 {
		t.Fatal("reconnect replayed notification", notices.Load(), b.Snapshot())
	}
}

func TestHeartbeatRequiresMatchingPong(t *testing.T) {
	for _, matching := range []bool{false, true} {
		name := "wrong pong"
		if matching {
			name = "matching pong"
		}
		t.Run(name, func(t *testing.T) {
			var pings atomic.Int64
			server := httptest.NewServer(websocket.Handler(func(ws *websocket.Conn) {
				if !subscribed(t, ws, 1) {
					return
				}
				for {
					var message envelope
					if websocket.JSON.Receive(ws, &message) != nil {
						return
					}
					if message.Type == "ping" {
						pings.Add(1)
						id := message.ID
						if !matching {
							id++
						}
						_ = websocket.JSON.Send(ws, map[string]any{"type": "pong", "id": id})
					}
				}
			}))
			t.Cleanup(server.Close)
			c := testConfig()
			c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
			b, _ := runBridge(t, c, nil, func(b *Bridge) {
				b.timing.ping, b.timing.pong = 30*time.Millisecond, 250*time.Millisecond
				b.timing.backoff = func(int) time.Duration { return time.Second }
			})
			if matching {
				until(t, func() bool { return pings.Load() >= 3 })
				if b.Snapshot().State != "connected" || b.Snapshot().Reconnects != 0 {
					t.Fatal("valid pong did not keep connection healthy", b.Snapshot())
				}
			} else {
				until(t, func() bool { return b.Snapshot().LastErrorCode == "heartbeat_timeout" })
			}
		})
	}
}

func TestOversizedFrameClosesConnection(t *testing.T) {
	server := httptest.NewServer(websocket.Handler(func(ws *websocket.Conn) {
		if !subscribed(t, ws, 1) {
			return
		}
		_ = websocket.Message.Send(ws, strings.Repeat("X", 1024*1024+1))
		drain(ws)
	}))
	t.Cleanup(server.Close)
	c := testConfig()
	c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
	b, _ := runBridge(t, c, nil, func(b *Bridge) { b.timing.backoff = func(int) time.Duration { return time.Second } })
	until(t, func() bool { return b.Snapshot().LastErrorCode == "message_too_large" })
}

func TestCancellationInterruptsAuthenticationWait(t *testing.T) {
	connected := make(chan struct{}, 1)
	server := httptest.NewServer(websocket.Handler(func(ws *websocket.Conn) {
		connected <- struct{}{}
		var message envelope
		_ = websocket.JSON.Receive(ws, &message) // Deliberately withhold auth_required.
	}))
	t.Cleanup(server.Close)
	c := testConfig()
	c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
	_, stop := runBridge(t, c, nil, nil)
	select {
	case <-connected:
	case <-time.After(time.Second):
		t.Fatal("not connected")
	}
	stop()
}

func TestTokenReadBoundsAndDisabledBridge(t *testing.T) {
	for name, value := range map[string]string{"empty": "", "short": "secret", "spaces": strings.Repeat("x", 31) + " secret", "oversized": strings.Repeat("x", 4099)} {
		t.Run(name, func(t *testing.T) {
			if token, err := readToken(makeToken(t, value)); err == nil || token != "" {
				t.Fatal("invalid token file accepted")
			}
		})
	}
	if _, err := readToken(t.TempDir()); err == nil {
		t.Fatal("directory accepted as token")
	}
	b := New(config.HomeAssistant{}, nil)
	b.Run(context.Background())
	if b.Snapshot().State != "disabled" {
		t.Fatal("disabled bridge attempted a connection")
	}
}

func coderSubscription(t *testing.T, w http.ResponseWriter, r *http.Request) (*coderws.Conn, context.Context, context.CancelFunc, bool) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	ws, err := coderws.Accept(w, r, nil)
	if err != nil {
		cancel()
		return nil, nil, nil, false
	}
	if writeJSON(ctx, ws, map[string]any{"type": "auth_required"}) != nil {
		cancel()
		_ = ws.CloseNow()
		return nil, nil, nil, false
	}
	var command envelope
	if readJSON(ctx, ws, &command) != nil || command.Type != "auth" || writeJSON(ctx, ws, map[string]any{"type": "auth_ok"}) != nil || readJSON(ctx, ws, &command) != nil || command.Type != "subscribe_events" || writeJSON(ctx, ws, map[string]any{"id": command.ID, "type": "result", "success": true}) != nil {
		cancel()
		_ = ws.CloseNow()
		return nil, nil, nil, false
	}
	return ws, ctx, cancel, true
}

func TestFragmentedMessageWaitsForFinalFrame(t *testing.T) {
	firstFragment := make(chan struct{}, 1)
	finish := make(chan struct{})
	var notices atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, ctx, cancel, ok := coderSubscription(t, w, r)
		if !ok {
			return
		}
		defer cancel()
		defer ws.CloseNow()
		raw, _ := json.Marshal(map[string]any{"id": 1, "type": "event", "event": stateEvent(time.Now(), "binary_sensor.door", state("off"), state("on"))})
		writer, err := ws.Writer(ctx, coderws.MessageText)
		if err != nil {
			return
		}
		// Even valid JSON must not be handled before the logical message's FIN.
		if _, err = writer.Write(raw); err != nil {
			return
		}
		firstFragment <- struct{}{}
		select {
		case <-finish:
		case <-ctx.Done():
			return
		}
		_ = writer.Close()
		_, _, _ = ws.Read(ctx)
	}))
	t.Cleanup(server.Close)
	c := testConfig()
	c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
	b, _ := runBridge(t, c, func(Notification) DeliveryResult { notices.Add(1); return DeliveryResult{Queued: 1} }, nil)
	select {
	case <-firstFragment:
	case <-time.After(time.Second):
		t.Fatal("fragment was not written")
	}
	time.Sleep(30 * time.Millisecond)
	if notices.Load() != 0 || b.Snapshot().EventsReceived != 0 {
		t.Error("incomplete fragmented message was processed")
	}
	close(finish)
	until(t, func() bool { return notices.Load() == 1 })
}

func TestCumulativeFragmentedMessageLimit(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, ctx, cancel, ok := coderSubscription(t, w, r)
		if !ok {
			return
		}
		defer cancel()
		defer ws.CloseNow()
		writer, err := ws.Writer(ctx, coderws.MessageText)
		if err != nil {
			return
		}
		_, _ = writer.Write([]byte(`{"type":"event","padding":"`))
		// Each individual frame is below 1 MiB; the total message exceeds it.
		_, _ = writer.Write([]byte(strings.Repeat("x", 600000)))
		_, _ = writer.Write([]byte(strings.Repeat("x", 600000)))
		_, _ = writer.Write([]byte(`"}`))
		_ = writer.Close()
		_, _, _ = ws.Read(ctx)
	}))
	t.Cleanup(server.Close)
	c := testConfig()
	c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
	b, _ := runBridge(t, c, nil, func(b *Bridge) { b.timing.backoff = func(int) time.Duration { return time.Second } })
	until(t, func() bool { return b.Snapshot().LastErrorCode == "message_too_large" })
}

func TestWebSocketRedirectsAreNotFollowed(t *testing.T) {
	var reached atomic.Int64
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { reached.Add(1); w.WriteHeader(http.StatusForbidden) }))
	t.Cleanup(target.Close)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL+"/api/websocket", http.StatusTemporaryRedirect)
	}))
	t.Cleanup(server.Close)
	c := testConfig()
	c.URL, c.TokenFile = server.URL, makeToken(t, fixtureToken)
	b, _ := runBridge(t, c, nil, func(b *Bridge) { b.timing.backoff = func(int) time.Duration { return time.Second } })
	until(t, func() bool { return b.Snapshot().LastErrorCode == "connection_failed" })
	if reached.Load() != 0 {
		t.Fatal("configured HA endpoint redirected the connection")
	}
}
