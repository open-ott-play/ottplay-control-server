package television

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/coder/websocket"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type fallbackClient struct {
	c                 config.TelevisionFallback
	token             string
	http              *http.Client
	mutationAttempted bool
	readyPollInterval time.Duration
}

func executeFallback(ctx context.Context, c config.Television, cmd Command, r Result) (Result, error) {
	r.Transport = "home_assistant"
	r.Outcome = "accepted"
	if cmd.Action == "apps" || cmd.Action == "pair" {
		return r, failure("unsupported")
	}
	token, err := readToken(c.Fallback.TokenFile)
	if err != nil {
		return r, err
	}
	transport := &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 3 * time.Second}).DialContext, TLSHandshakeTimeout: 3 * time.Second, ResponseHeaderTimeout: 4 * time.Second}
	defer transport.CloseIdleConnections()
	f := &fallbackClient{c: *c.Fallback, token: token, http: &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, readyPollInterval: 500 * time.Millisecond}
	// Checking the entity registry prevents a server -> OttPlay HA entity ->
	// server loop. A matching friendly name or media_player domain is not proof.
	if err = f.verifyNative(ctx); err != nil {
		return r, err
	}
	err = f.execute(ctx, c, cmd, &r)
	if err != nil && f.mutationAttempted {
		r.Outcome = "uncertain"
		return r, asUncertain(err)
	}
	return r, err
}

func (f *fallbackClient) endpoint(path string) string { return strings.TrimRight(f.c.URL, "/") + path }

func (f *fallbackClient) verifyNative(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()
	u, err := url.Parse(f.endpoint("/api/websocket"))
	if err != nil {
		return failure("fallback_unavailable")
	}
	if u.Scheme == "https" {
		u.Scheme = "wss"
	} else {
		u.Scheme = "ws"
	}
	ws, _, err := websocket.Dial(ctx, u.String(), &websocket.DialOptions{HTTPClient: f.http, CompressionMode: websocket.CompressionDisabled})
	if err != nil {
		return failure("fallback_unavailable")
	}
	defer ws.CloseNow()
	ws.SetReadLimit(256 * 1024)
	read := func() (map[string]json.RawMessage, error) {
		kind, b, err := ws.Read(ctx)
		if err != nil || kind != websocket.MessageText {
			return nil, failure("fallback_unavailable")
		}
		var p map[string]json.RawMessage
		if json.Unmarshal(b, &p) != nil {
			return nil, failure("fallback_unavailable")
		}
		return p, nil
	}
	write := func(value any) error {
		b, _ := json.Marshal(value)
		if ws.Write(ctx, websocket.MessageText, b) != nil {
			return failure("fallback_unavailable")
		}
		return nil
	}
	p, err := read()
	if err != nil {
		return err
	}
	if stringField(p, "type", 64) != "auth_required" {
		return failure("fallback_unavailable")
	}
	if err = write(map[string]any{"type": "auth", "access_token": f.token}); err != nil {
		return err
	}
	p, err = read()
	if err != nil {
		return err
	}
	if stringField(p, "type", 64) != "auth_ok" {
		return failure("fallback_unavailable")
	}
	if err = write(map[string]any{"id": 1, "type": "config/entity_registry/get", "entity_id": f.c.EntityID}); err != nil {
		return err
	}
	p, err = read()
	if err != nil {
		return err
	}
	var id int
	var success bool
	var entity map[string]json.RawMessage
	if json.Unmarshal(p["id"], &id) != nil || id != 1 || json.Unmarshal(p["success"], &success) != nil || !success || json.Unmarshal(p["result"], &entity) != nil || stringField(entity, "platform", 64) != "webostv" {
		return failure("fallback_target_invalid")
	}
	return nil
}

func (f *fallbackClient) request(ctx context.Context, method, path string, payload any, result any) error {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	var body io.Reader
	if payload != nil {
		b, err := json.Marshal(payload)
		if err != nil {
			return failure("fallback_unavailable")
		}
		body = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, f.endpoint(path), body)
	if err != nil {
		return failure("fallback_unavailable")
	}
	req.Header.Set("Authorization", "Bearer "+f.token)
	req.Header.Set("Content-Type", "application/json")
	if method == http.MethodPost {
		f.mutationAttempted = true
	}
	resp, err := f.http.Do(req)
	if err != nil {
		return failure("fallback_unavailable")
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(io.LimitReader(resp.Body, 256*1024+1))
	if err != nil || len(b) > 256*1024 {
		return failure("fallback_unavailable")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return failure("fallback_unavailable")
	}
	if result != nil && json.Unmarshal(b, result) != nil {
		return failure("fallback_unavailable")
	}
	return nil
}

func (f *fallbackClient) state(ctx context.Context) (*State, error) {
	var raw struct {
		State      string                     `json:"state"`
		Attributes map[string]json.RawMessage `json:"attributes"`
	}
	if err := f.request(ctx, http.MethodGet, "/api/states/"+f.c.EntityID, nil, &raw); err != nil {
		return nil, err
	}
	s := &State{Power: "unknown"}
	switch raw.State {
	case "off":
		s.Power = "off"
	case "on", "playing", "paused", "idle", "buffering":
		s.Power = "on"
	}
	var level *float64
	if json.Unmarshal(raw.Attributes["volume_level"], &level) == nil && level != nil && *level >= 0 && *level <= 1 {
		volume := int(math.Round(*level * 100))
		s.Volume = &volume
	}
	var muted *bool
	if json.Unmarshal(raw.Attributes["is_volume_muted"], &muted) == nil && muted != nil {
		s.Muted = muted
	}
	s.AppID = stringField(raw.Attributes, "app_id", 256)
	return s, nil
}

func (f *fallbackClient) service(ctx context.Context, domain, service string, data map[string]any) error {
	if data == nil {
		data = map[string]any{}
	}
	data["entity_id"] = f.c.EntityID
	return f.request(ctx, http.MethodPost, "/api/services/"+domain+"/"+service, data, nil)
}

func (f *fallbackClient) execute(ctx context.Context, c config.Television, cmd Command, r *Result) error {
	state, err := f.state(ctx)
	if err != nil {
		return err
	}
	r.State = state
	switch cmd.Action {
	case "status":
		r.Outcome = "confirmed"
		return nil
	case "volume":
		if cmd.Volume == nil && cmd.Delta == nil {
			if state.Volume == nil {
				return failure("unsupported")
			}
			r.Outcome = "confirmed"
			return nil
		}
		level := 0
		if cmd.Volume != nil {
			level = *cmd.Volume
		} else {
			if state.Volume == nil {
				return failure("unsupported")
			}
			level = max(0, min(100, *state.Volume+*cmd.Delta))
		}
		if err = f.service(ctx, "media_player", "volume_set", map[string]any{"volume_level": float64(level) / 100}); err != nil {
			return err
		}
		r.Outcome = "accepted"
		r.State = nil
		if observed, err := f.state(ctx); err == nil {
			r.State = observed
			if observed.Volume != nil && *observed.Volume == level {
				r.Outcome = "confirmed"
			}
		}
		return nil
	case "mute":
		if err = f.service(ctx, "media_player", "volume_mute", map[string]any{"is_volume_muted": *cmd.Muted}); err != nil {
			return err
		}
		r.Outcome = "accepted"
		r.State = nil
		if observed, err := f.state(ctx); err == nil {
			r.State = observed
			if observed.Muted != nil && *observed.Muted == *cmd.Muted {
				r.Outcome = "confirmed"
			}
		}
		return nil
	case "off":
		if state.Power == "off" {
			r.Outcome = "confirmed"
			return nil
		}
		if err = f.service(ctx, "media_player", "turn_off", nil); err != nil {
			return err
		}
		r.Outcome = "accepted"
		r.State = nil
		if observed, err := f.state(ctx); err == nil {
			r.State = observed
			if observed.Power == "off" {
				r.Outcome = "confirmed"
			}
		}
		return nil
	case "on", "go":
		if state.Power != "on" {
			if err = f.service(ctx, "media_player", "turn_on", nil); err != nil {
				return err
			}
			r.Outcome = "accepted"
			r.State = nil
			probe, cancel := fallbackReadinessContext(ctx)
			defer cancel()
			pollInterval := f.readyPollInterval
			if pollInterval <= 0 {
				pollInterval = 500 * time.Millisecond
			}
			for probe.Err() == nil {
				if observed, err := f.state(probe); err == nil {
					state = observed
					r.State = observed
					if state.Power == "on" {
						break
					}
				}
				if !wait(probe, pollInterval) {
					break
				}
			}
		}
		if state.Power != "on" {
			if cmd.Action == "go" {
				return failure("not_ready")
			}
			return nil
		}
		if cmd.Action == "on" {
			r.Outcome = "confirmed"
			return nil
		}
		return f.launch(ctx, c, r)
	case "launch":
		return f.launch(ctx, c, r)
	}
	return failure("unsupported")
}

func fallbackReadinessContext(ctx context.Context) (context.Context, context.CancelFunc) {
	// Slow webOS boots can take a minute. Use the caller's remaining budget,
	// rather than returning early while an accepted wake is still in progress.
	return context.WithTimeout(ctx, 90*time.Second)
}

func (f *fallbackClient) launch(ctx context.Context, c config.Television, r *Result) error {
	command := launchApp
	payload := map[string]any{"id": c.AppID}
	if c.LaunchURL != "" {
		command = openURL
		payload = map[string]any{"target": c.LaunchURL}
	}
	if err := f.service(ctx, "webostv", "command", map[string]any{"command": command, "payload": payload}); err != nil {
		return err
	}
	r.Outcome = "accepted"
	r.State = nil
	if observed, err := f.state(ctx); err == nil {
		r.State = observed
		if c.AppID != "" && observed.AppID == c.AppID {
			r.Outcome = "confirmed"
		}
	}
	return nil
}
