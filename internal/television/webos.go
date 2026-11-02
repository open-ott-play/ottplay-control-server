package television

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/coder/websocket"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const (
	getPower  = "com.webos.service.tvpower/power/getPowerState"
	getApp    = "com.webos.applicationManager/getForegroundAppInfo"
	getApps   = "com.webos.applicationManager/listLaunchPoints"
	getVolume = "audio/getVolume"
	getMute   = "audio/getStatus"
	setVolume = "audio/setVolume"
	setMute   = "audio/setMute"
	turnOff   = "system/turnOff"
	launchApp = "system.launcher/launch"
	openURL   = "system.launcher/open"
)

type session struct {
	conn              *websocket.Conn
	credentials       credentials
	mutationAttempted bool
	seq               int
}

type message struct {
	ID      string          `json:"id"`
	Type    string          `json:"type"`
	Payload json.RawMessage `json:"payload"`
	Error   string          `json:"error"`
}

func connect(ctx context.Context, c config.Television, pair bool) (*session, error) {
	key, err := readCredentials(c.ClientKeyFile)
	if err != nil {
		if _, statErr := os.Lstat(c.ClientKeyFile); !pair || !os.IsNotExist(statErr) {
			return nil, err
		}
		key = credentials{}
	}
	pin := c.CertificateSHA256
	if pin == "" {
		pin = key.CertificateSHA256
	}
	if c.CertificateSHA256 != "" && key.CertificateSHA256 != "" && c.CertificateSHA256 != key.CertificateSHA256 {
		return nil, failure("certificate_mismatch")
	}
	if c.Secure() && pin == "" && !pair {
		return nil, failure("certificate_required")
	}
	observedPin := ""
	certificateMismatch := false
	transport := &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 3 * time.Second}).DialContext}
	defer transport.CloseIdleConnections()
	if c.Secure() {
		transport.TLSClientConfig = &tls.Config{
			MinVersion: tls.VersionTLS12,
			// LG televisions use self-signed certificates. Verification below
			// pins the leaf certificate; only explicit pair can enroll a new pin.
			InsecureSkipVerify: true, //nolint:gosec -- mandatory certificate pin below
			VerifyConnection: func(state tls.ConnectionState) error {
				if len(state.PeerCertificates) == 0 {
					certificateMismatch = true
					return errors.New("missing television certificate")
				}
				sum := sha256.Sum256(state.PeerCertificates[0].Raw)
				observedPin = hex.EncodeToString(sum[:])
				if pin != "" && subtle.ConstantTimeCompare([]byte(pin), []byte(observedPin)) != 1 {
					certificateMismatch = true
					return errors.New("television certificate mismatch")
				}
				return nil
			},
		}
	}
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	scheme := "ws"
	if c.Secure() {
		scheme = "wss"
	}
	dialCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	conn, _, err := websocket.Dial(dialCtx, scheme+"://"+net.JoinHostPort(c.Host, strconv.Itoa(c.Port)), &websocket.DialOptions{HTTPClient: client, CompressionMode: websocket.CompressionDisabled})
	cancel()
	if err != nil {
		if certificateMismatch {
			return nil, failure("certificate_mismatch")
		}
		return nil, failure("connection_failed")
	}
	conn.SetReadLimit(256 * 1024)
	s := &session{conn: conn, credentials: key}
	success := false
	defer func() {
		if !success {
			s.close()
		}
	}()
	handshakeCtx, done := context.WithTimeout(ctx, 3*time.Second)
	if err = s.write(handshakeCtx, map[string]any{"id": "hello", "type": "hello", "payload": map[string]any{}}, false); err == nil {
		var hello message
		hello, err = s.read(handshakeCtx)
		if err == nil && hello.Type != "hello" {
			err = failure("protocol_error")
		}
	}
	done()
	if err != nil {
		return nil, err
	}
	payload := map[string]any{
		"forcePairing": false, "pairingType": "PROMPT",
		"manifest": map[string]any{"manifestVersion": 1, "appVersion": "1.0", "permissions": []string{"CONTROL_AUDIO", "CONTROL_POWER", "LAUNCH", "LAUNCH_WEBAPP", "READ_APP_STATUS", "READ_INSTALLED_APPS", "READ_POWER_STATE", "READ_RUNNING_APPS"}},
	}
	if key.ClientKey != "" {
		payload["client-key"] = key.ClientKey
	}
	duration := 5 * time.Second
	if pair {
		duration = 60 * time.Second
	}
	registerCtx, stop := context.WithTimeout(ctx, duration)
	defer stop()
	if err = s.write(registerCtx, map[string]any{"id": "register_0", "type": "register", "payload": payload}, false); err != nil {
		return nil, err
	}
	for range 8 {
		msg, err := s.read(registerCtx)
		if err != nil {
			return nil, err
		}
		if msg.Type == "error" {
			return nil, failure("pairing_rejected")
		}
		var p struct {
			ClientKey   string `json:"client-key"`
			PairingType string `json:"pairingType"`
		}
		if json.Unmarshal(msg.Payload, &p) != nil {
			return nil, failure("protocol_error")
		}
		if msg.Type == "registered" {
			if p.ClientKey != "" {
				s.credentials.ClientKey = p.ClientKey
			}
			if !validKey(s.credentials.ClientKey) {
				return nil, failure("protocol_error")
			}
			if c.Secure() {
				s.credentials.CertificateSHA256 = observedPin
			}
			success = true
			return s, nil
		}
		if !pair && p.PairingType != "" {
			return nil, failure("pairing_required")
		}
	}
	return nil, failure("protocol_error")
}

func (s *session) close() {
	if s != nil && s.conn != nil {
		_ = s.conn.CloseNow()
	}
}

func (s *session) write(ctx context.Context, value any, mutation bool) error {
	b, err := json.Marshal(value)
	if err != nil {
		return failure("protocol_error")
	}
	if mutation {
		s.mutationAttempted = true
	}
	if err = s.conn.Write(ctx, websocket.MessageText, b); err != nil {
		return failure("connection_failed")
	}
	return nil
}

func (s *session) read(ctx context.Context) (message, error) {
	var msg message
	kind, b, err := s.conn.Read(ctx)
	if err != nil {
		if ctx.Err() != nil {
			return msg, failure("deadline")
		}
		return msg, failure("connection_failed")
	}
	if kind != websocket.MessageText || json.Unmarshal(b, &msg) != nil {
		return msg, failure("protocol_error")
	}
	return msg, nil
}

func (s *session) rpc(ctx context.Context, endpoint string, payload any, mutation bool) (map[string]json.RawMessage, error) {
	ctx, cancel := context.WithTimeout(ctx, 4*time.Second)
	defer cancel()
	s.seq++
	id := "ott-" + strconv.Itoa(s.seq)
	if payload == nil {
		payload = map[string]any{}
	}
	if err := s.write(ctx, map[string]any{"id": id, "type": "request", "uri": "ssap://" + endpoint, "payload": payload}, mutation); err != nil {
		return nil, err
	}
	for range 32 {
		msg, err := s.read(ctx)
		if err != nil {
			return nil, err
		}
		if msg.ID != id {
			continue
		}
		if msg.Type == "error" {
			if strings.HasPrefix(msg.Error, "404") {
				return nil, failure("unsupported")
			}
			return nil, failure("command_rejected")
		}
		var p map[string]json.RawMessage
		if msg.Type != "response" || json.Unmarshal(msg.Payload, &p) != nil || p == nil {
			return nil, failure("protocol_error")
		}
		var returned bool
		if json.Unmarshal(p["returnValue"], &returned) != nil || !returned {
			return nil, failure("command_rejected")
		}
		return p, nil
	}
	return nil, failure("protocol_error")
}

func stringField(p map[string]json.RawMessage, field string, limit int) string {
	var value string
	if json.Unmarshal(p[field], &value) != nil || len(value) > limit {
		return ""
	}
	for _, ch := range value {
		if ch < 32 || ch == 127 {
			return ""
		}
	}
	return value
}

func (s *session) volume(ctx context.Context) (*State, error) {
	p, err := s.rpc(ctx, getVolume, nil, false)
	if err != nil {
		return nil, err
	}
	if nested := p["volumeStatus"]; nested != nil {
		if json.Unmarshal(nested, &p) != nil {
			return nil, failure("protocol_error")
		}
	}
	state := &State{Power: "unknown"}
	var level *int
	if json.Unmarshal(p["volume"], &level) != nil || level == nil || *level < 0 || *level > 100 {
		return nil, failure("unsupported")
	}
	state.Volume = level
	for _, field := range []string{"muted", "mute", "muteStatus"} {
		var muted *bool
		if json.Unmarshal(p[field], &muted) == nil && muted != nil {
			state.Muted = muted
			break
		}
	}
	return state, nil
}

func (s *session) mute(ctx context.Context) (*bool, error) {
	p, err := s.rpc(ctx, getMute, nil, false)
	if err != nil {
		return nil, err
	}
	for _, field := range []string{"mute", "muted"} {
		var muted *bool
		if json.Unmarshal(p[field], &muted) == nil && muted != nil {
			return muted, nil
		}
	}
	return nil, failure("unsupported")
}

func (s *session) powerState(ctx context.Context) (*State, error) {
	state := &State{Power: "unknown"}
	p, powerErr := s.rpc(ctx, getPower, nil, false)
	if powerErr == nil {
		switch stringField(p, "state", 128) {
		case "Power Off", "Suspend", "Active Standby":
			state.Power = "off"
		case "Active", "Power On", "Screen Off", "Screen Saver":
			state.Power = "on"
		}
	}
	p, appErr := s.rpc(ctx, getApp, nil, false)
	if appErr == nil {
		state.AppID = stringField(p, "appId", 256)
		if powerErr != nil && state.AppID != "" {
			state.Power = "on"
		}
	}
	if powerErr != nil && appErr != nil {
		return nil, powerErr
	}
	return state, nil
}

func (s *session) state(ctx context.Context) (*State, error) {
	state, err := s.powerState(ctx)
	if err != nil {
		return nil, err
	}
	if volume, err := s.volume(ctx); err == nil {
		state.Volume = volume.Volume
		state.Muted = volume.Muted
	}
	if state.Muted == nil {
		if muted, err := s.mute(ctx); err == nil {
			state.Muted = muted
		}
	}
	return state, nil
}

func (s *session) execute(ctx context.Context, c config.Television, cmd Command, r *Result) error {
	switch cmd.Action {
	case "status":
		state, err := s.state(ctx)
		if err != nil {
			return err
		}
		r.State = state
		r.Outcome = "confirmed"
		return nil
	case "volume":
		if cmd.Volume == nil && cmd.Delta == nil {
			state, err := s.volume(ctx)
			if err != nil {
				return err
			}
			r.State = state
			r.Outcome = "confirmed"
			return nil
		}
		var level int
		if cmd.Volume != nil {
			level = *cmd.Volume
		} else {
			state, err := s.volume(ctx)
			if err != nil {
				return err
			}
			level = max(0, min(100, *state.Volume+*cmd.Delta))
		}
		if _, err := s.rpc(ctx, setVolume, map[string]any{"volume": level}, true); err != nil {
			return err
		}
		r.Outcome = "accepted"
		if state, err := s.volume(ctx); err == nil {
			r.State = state
			if *state.Volume == level {
				r.Outcome = "confirmed"
			}
		}
		return nil
	case "mute":
		if _, err := s.rpc(ctx, setMute, map[string]any{"mute": *cmd.Muted}, true); err != nil {
			return err
		}
		r.Outcome = "accepted"
		if muted, err := s.mute(ctx); err == nil {
			r.State = &State{Power: "unknown", Muted: muted}
			if *muted == *cmd.Muted {
				r.Outcome = "confirmed"
			}
		}
		return nil
	case "off":
		state, err := s.powerState(ctx)
		if err != nil {
			return err
		}
		if state.Power == "off" {
			r.State = state
			r.Outcome = "confirmed"
			return nil
		}
		if state.Power != "on" {
			return failure("not_ready")
		}
		// A normal shutdown can close the socket before any SSAP response.
		// Never interpret that as permission to replay power-off through HA.
		writeCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
		defer cancel()
		s.seq++
		if err = s.write(writeCtx, map[string]any{"id": "ott-" + strconv.Itoa(s.seq), "type": "request", "uri": "ssap://" + turnOff, "payload": map[string]any{}}, true); err != nil {
			return err
		}
		r.Outcome = "accepted"
		r.State = &State{Power: "unknown"}
		return nil
	case "launch":
		endpoint := launchApp
		payload := map[string]any{"id": c.AppID}
		if c.LaunchURL != "" {
			endpoint = openURL
			payload = map[string]any{"target": c.LaunchURL}
		}
		if _, err := s.rpc(ctx, endpoint, payload, true); err != nil {
			return err
		}
		r.Outcome = "accepted"
		if p, err := s.rpc(ctx, getApp, nil, false); err == nil {
			app := stringField(p, "appId", 256)
			r.State = &State{Power: "on", AppID: app}
			if c.AppID != "" && app == c.AppID {
				r.Outcome = "confirmed"
			}
		}
		return nil
	case "apps":
		p, err := s.rpc(ctx, getApps, nil, false)
		if err != nil {
			return err
		}
		var list []map[string]json.RawMessage
		if json.Unmarshal(p["launchPoints"], &list) != nil || list == nil || len(list) > 512 {
			return failure("protocol_error")
		}
		r.Apps = []App{}
		for _, item := range list {
			id := stringField(item, "id", 256)
			title := stringField(item, "title", 160)
			if id != "" && title != "" {
				r.Apps = append(r.Apps, App{ID: id, Title: title})
			}
		}
		r.Outcome = "confirmed"
		return nil
	}
	return failure("invalid_command")
}
