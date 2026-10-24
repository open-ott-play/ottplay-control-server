// Package homeassistant subscribes to explicitly configured Home Assistant
// events. It has no service-call API and never exposes credentials or raw events.
package homeassistant

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type Notification struct {
	Title               string
	Message             string
	Severity            string
	DurationMS          int
	TTLSeconds          int
	Targets             []string
	Media               *CameraFrame
	TargetRuntimes      map[string]string
	MediaTargetRuntimes map[string]string
	ExpiresAt           time.Time
}

// Queued is deliberately not called delivered: a server enqueue cannot prove
// that the player displayed a banner. Skipped counts ineligible target devices.
type DeliveryResult struct {
	Queued  int
	Skipped int
}

type Snapshot struct {
	Enabled              bool       `json:"enabled"`
	State                string     `json:"state"`
	LastErrorCode        string     `json:"last_error_code,omitempty"`
	Reconnects           uint64     `json:"reconnects"`
	EventsReceived       uint64     `json:"events_received"`
	EventsMatched        uint64     `json:"events_matched"`
	NotificationsQueued  uint64     `json:"notifications_queued"`
	NotificationsSkipped uint64     `json:"notifications_skipped"`
	EventsDropped        uint64     `json:"events_dropped"`
	LastEventAt          *time.Time `json:"last_event_at,omitempty"`
	CameraAcquired       uint64     `json:"camera_acquired"`
	CameraFailed         uint64     `json:"camera_failed"`
	CameraSkipped        uint64     `json:"camera_skipped"`
}

type timings struct {
	handshake, ping, pong, tokenPoll time.Duration
	backoff                          func(int) time.Duration
}

type Bridge struct {
	config  config.HomeAssistant
	deliver func(Notification) DeliveryResult
	mu      sync.Mutex
	status  Snapshot
	timing  timings
	now     func() time.Time
	// The following maps are owned by Run's single event-processing goroutine.
	seen               map[[32]byte]time.Time
	cameraSeen         map[[32]byte]time.Time
	cooldown           map[string]time.Time
	rateStart          time.Time
	rateUsed           int
	cameraTargets      func(Notification) map[string]string
	cameraMediaTargets func(Notification) map[string]string
	cameraQueue        chan cameraJob
	cameraFetch        func(context.Context, config.HomeAssistant, string) (*CameraFrame, error)
}

func New(c config.HomeAssistant, deliver func(Notification) DeliveryResult) *Bridge {
	// Detach all slices so the running worker cannot be reconfigured by mutation.
	b, _ := json.Marshal(c)
	var cloned config.HomeAssistant
	_ = json.Unmarshal(b, &cloned)
	c = cloned
	c.Defaults()
	state := "disabled"
	if c.Enabled {
		state = "connecting"
	}
	return &Bridge{
		config: c, deliver: deliver, status: Snapshot{Enabled: c.Enabled, State: state},
		timing: timings{handshake: 15 * time.Second, ping: 20 * time.Second, pong: 10 * time.Second, tokenPoll: 5 * time.Second, backoff: reconnectDelay},
		now:    time.Now, seen: map[[32]byte]time.Time{}, cameraSeen: map[[32]byte]time.Time{}, cooldown: map[string]time.Time{},
		cameraQueue: make(chan cameraJob, 4), cameraFetch: AcquireCameraFrame,
	}
}

func (b *Bridge) Snapshot() Snapshot {
	b.mu.Lock()
	defer b.mu.Unlock()
	s := b.status
	if s.LastEventAt != nil {
		t := *s.LastEventAt
		s.LastEventAt = &t
	}
	return s
}

func (b *Bridge) setState(state, code string) {
	b.mu.Lock()
	b.status.State, b.status.LastErrorCode = state, code
	b.mu.Unlock()
}

// Run must be called once. Cancellation closes the active socket and interrupts
// connection attempts, backoff and auth-failure parking.
func (b *Bridge) Run(ctx context.Context) {
	if !b.config.Enabled {
		return
	}
	if b.config.Validate() != nil {
		b.setState("configuration_error", "invalid_configuration")
		return
	}
	stopCameras := b.startCameraWorkers(ctx)
	defer stopCameras()
	defer b.setState("stopped", "")
	var rejected [32]byte
	haveRejected, attempted := false, false
	attempt := 0
	for ctx.Err() == nil {
		token, err := readToken(b.config.TokenFile)
		if err != nil {
			b.setState("token_error", "token_file_unavailable")
			if !wait(ctx, b.timing.tokenPoll) {
				return
			}
			continue
		}
		fingerprint := sha256.Sum256([]byte(token))
		if haveRejected && fingerprint == rejected {
			b.setState("auth_failed", "authentication_rejected")
			if !wait(ctx, b.timing.tokenPoll) {
				return
			}
			continue
		}
		// The latch describes the current credential only. After rotation, a
		// later deliberate return to the old token must get one fresh attempt.
		haveRejected = false
		if attempted {
			b.mu.Lock()
			b.status.Reconnects++
			b.mu.Unlock()
		}
		attempted = true
		start := b.now()
		code := b.session(ctx, token)
		token = ""
		if ctx.Err() != nil {
			return
		}
		if code == "authentication_rejected" {
			rejected, haveRejected = fingerprint, true
			attempt = 0
			continue
		}
		if b.now().Sub(start) >= time.Minute {
			attempt = 0
		}
		b.setState("reconnecting", code)
		if !wait(ctx, b.timing.backoff(attempt)) {
			return
		}
		if attempt < 5 {
			attempt++
		}
	}
}

func readToken(path string) (string, error) {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 4098 {
		return "", errors.New("token unavailable")
	}
	f, err := os.Open(path)
	if err != nil {
		return "", errors.New("token unavailable")
	}
	defer f.Close()
	info, err = f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 4098 {
		return "", errors.New("token unavailable")
	}
	raw, err := io.ReadAll(io.LimitReader(f, 4099))
	if err != nil || len(raw) > 4098 {
		return "", errors.New("token unavailable")
	}
	token := strings.TrimSpace(string(raw))
	if len(token) < 32 || len(token) > 4096 {
		return "", errors.New("token unavailable")
	}
	for _, r := range token {
		if r < '!' || r > '~' {
			return "", errors.New("token unavailable")
		}
	}
	return token, nil
}

func wait(ctx context.Context, duration time.Duration) bool {
	t := time.NewTimer(duration)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func reconnectDelay(attempt int) time.Duration {
	seconds := min(1<<min(attempt, 5), 30)
	base := time.Duration(seconds) * time.Second
	return base + time.Duration(rand.Int64N(int64(base/4)+1))
}

type envelope struct {
	ID      int64           `json:"id"`
	Type    string          `json:"type"`
	Success bool            `json:"success"`
	Event   json.RawMessage `json:"event"`
}

func (b *Bridge) session(ctx context.Context, token string) string {
	u, _ := url.Parse(b.config.URL) // Validated before entering the loop.
	origin := u.Scheme + "://" + u.Host
	if u.Scheme == "https" {
		u.Scheme = "wss"
	} else {
		u.Scheme = "ws"
	}
	u.Path = strings.TrimRight(u.Path, "/")
	if !strings.HasSuffix(u.Path, "/api/websocket") {
		u.Path += "/api/websocket"
	}
	b.setState("connecting", "")
	// A private transport deliberately ignores ambient proxy settings. The
	// token must never follow an HTTP redirect or bypass certificate validation.
	transport := &http.Transport{TLSHandshakeTimeout: b.timing.handshake, ResponseHeaderTimeout: b.timing.handshake, DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	dialCtx, cancel := context.WithTimeout(ctx, b.timing.handshake)
	ws, _, err := websocket.Dial(dialCtx, u.String(), &websocket.DialOptions{HTTPClient: client, HTTPHeader: http.Header{"Origin": []string{origin}}, CompressionMode: websocket.CompressionDisabled})
	cancel()
	if err != nil {
		return "connection_failed"
	}
	ws.SetReadLimit(1024 * 1024) // Cumulative logical-message limit, including fragments.
	stopCancel := context.AfterFunc(ctx, func() { _ = ws.CloseNow() })
	defer stopCancel()
	defer ws.CloseNow()
	handshakeCtx, cancelHandshake := context.WithTimeout(ctx, b.timing.handshake)
	defer cancelHandshake()
	b.setState("authenticating", "")
	var msg envelope
	if readJSON(handshakeCtx, ws, &msg) != nil || msg.Type != "auth_required" {
		return "authentication_protocol_error"
	}
	if writeJSON(handshakeCtx, ws, map[string]any{"type": "auth", "access_token": token}) != nil {
		return "connection_failed"
	}
	if readJSON(handshakeCtx, ws, &msg) != nil {
		return "authentication_protocol_error"
	}
	if msg.Type == "auth_invalid" {
		return "authentication_rejected"
	}
	if msg.Type != "auth_ok" {
		return "authentication_protocol_error"
	}
	b.setState("subscribing", "")
	nextID := int64(1)
	subscriptions := map[int64]string{}
	eventTypes := map[string]bool{}
	for _, rule := range b.config.Rules {
		if eventTypes[rule.EventType] {
			continue
		}
		eventTypes[rule.EventType] = true
		subscriptions[nextID] = rule.EventType
		if writeJSON(handshakeCtx, ws, map[string]any{"id": nextID, "type": "subscribe_events", "event_type": rule.EventType}) != nil {
			return "connection_failed"
		}
		nextID++
	}
	pending := len(subscriptions)
	confirmed := map[int64]bool{}
	for pending > 0 {
		msg = envelope{}
		if readJSON(handshakeCtx, ws, &msg) != nil {
			return "subscription_protocol_error"
		}
		if msg.Type != "result" || subscriptions[msg.ID] == "" || confirmed[msg.ID] {
			continue
		}
		if !msg.Success {
			return "subscription_rejected"
		}
		confirmed[msg.ID] = true
		pending--
	}
	b.setState("connected", "")
	return b.receive(ctx, ws, subscriptions, nextID)
}

func (b *Bridge) receive(ctx context.Context, ws *websocket.Conn, subscriptions map[int64]string, nextID int64) string {
	type received struct {
		message envelope
		err     error
	}
	messages := make(chan received, 1)
	done := make(chan struct{})
	defer close(done)
	go func() {
		for {
			var msg envelope
			err := readJSON(ctx, ws, &msg)
			select {
			case messages <- received{msg, err}:
			case <-done:
				return
			case <-ctx.Done():
				return
			}
			if err != nil {
				return
			}
		}
	}()
	ping := time.NewTicker(b.timing.ping)
	defer ping.Stop()
	pong := time.NewTimer(time.Hour)
	pong.Stop()
	defer pong.Stop()
	var pongC <-chan time.Time
	var awaiting int64
	for {
		select {
		case <-ctx.Done():
			return ""
		case <-pongC:
			return "heartbeat_timeout"
		case <-ping.C:
			if awaiting != 0 {
				continue
			}
			awaiting = nextID
			nextID++
			writeCtx, cancel := context.WithTimeout(ctx, b.timing.handshake)
			err := writeJSON(writeCtx, ws, map[string]any{"id": awaiting, "type": "ping"})
			cancel()
			if err != nil {
				return "connection_failed"
			}
			pong.Reset(b.timing.pong)
			pongC = pong.C
		case received := <-messages:
			if received.err != nil {
				if errors.Is(received.err, websocket.ErrMessageTooBig) {
					return "message_too_large"
				}
				return "connection_lost"
			}
			msg := received.message
			if msg.Type == "pong" && awaiting != 0 && msg.ID == awaiting {
				awaiting = 0
				pong.Stop()
				pongC = nil
			} else if msg.Type == "event" && subscriptions[msg.ID] != "" {
				b.processEvent(msg.Event, subscriptions[msg.ID])
			}
		}
	}
}

func readJSON(ctx context.Context, ws *websocket.Conn, value any) error {
	typ, raw, err := ws.Read(ctx)
	if err != nil {
		return err
	}
	if typ != websocket.MessageText {
		return errors.New("expected text message")
	}
	return json.Unmarshal(raw, value)
}

func writeJSON(ctx context.Context, ws *websocket.Conn, value any) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return ws.Write(ctx, websocket.MessageText, raw)
}
