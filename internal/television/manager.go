// Package television controls explicitly bound physical TVs independently of
// whether their OttPlay application is connected to the controller.
package television

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"strconv"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type Command struct {
	Action string `json:"action"`
	App    string `json:"app,omitempty"`
	Volume *int   `json:"volume,omitempty"`
	Delta  *int   `json:"delta,omitempty"`
	Muted  *bool  `json:"muted,omitempty"`
}

type State struct {
	Power  string `json:"power"`
	Volume *int   `json:"volume,omitempty"`
	Muted  *bool  `json:"muted,omitempty"`
	AppID  string `json:"app_id,omitempty"`
}

type App struct {
	ID    string `json:"id"`
	Title string `json:"title"`
}
type Pairing struct {
	Paired bool `json:"paired"`
}
type Result struct {
	Version   int      `json:"version"`
	DeviceID  string   `json:"device_id"`
	Action    string   `json:"action"`
	Transport string   `json:"transport"`
	Outcome   string   `json:"outcome"`
	State     *State   `json:"state,omitempty"`
	Apps      []App    `json:"apps"`
	Pairing   *Pairing `json:"pairing,omitempty"`
}

type Binding struct {
	Version            int      `json:"version"`
	DeviceID           string   `json:"device_id"`
	Configured         bool     `json:"configured"`
	Capabilities       []string `json:"capabilities"`
	Paired             bool     `json:"paired"`
	FallbackConfigured bool     `json:"fallback_configured"`
}

// Error is deliberately limited to stable codes: transport errors can contain
// credentials, private addresses or untrusted text from the remote device.
type Error struct {
	Code      string
	Uncertain bool
}

func (e *Error) Error() string    { return "television: " + e.Code }
func failure(code string) error   { return &Error{Code: code} }
func uncertain(code string) error { return &Error{Code: code, Uncertain: true} }

func (c Command) Validate() error {
	if c.App != "" && ((c.Action != "launch" && c.Action != "go") || !config.ValidTelevisionAppAlias(c.App)) {
		return failure("invalid_command")
	}
	if c.Volume != nil && (*c.Volume < 0 || *c.Volume > 100) {
		return failure("invalid_command")
	}
	if c.Delta != nil && (*c.Delta < -100 || *c.Delta > 100 || *c.Delta == 0) {
		return failure("invalid_command")
	}
	switch c.Action {
	case "volume":
		if c.Muted != nil || (c.Volume != nil && c.Delta != nil) {
			return failure("invalid_command")
		}
	case "mute":
		if c.Muted == nil || c.Volume != nil || c.Delta != nil {
			return failure("invalid_command")
		}
	case "status", "on", "off", "launch", "go", "apps", "pair":
		if c.Volume != nil || c.Delta != nil || c.Muted != nil {
			return failure("invalid_command")
		}
	default:
		return failure("invalid_command")
	}
	return nil
}

type boundTV struct {
	config config.Television
	gate   chan struct{}
}
type Manager struct {
	devices           map[string]*boundTV
	wakeWait          time.Duration
	wakeInterval      time.Duration
	wakeRetryInterval time.Duration
	sendWake          func(context.Context, string, string, int) error
}

const defaultWakeRetryInterval = 10 * time.Second

func New(configs []config.Television) (*Manager, error) {
	// Detach pointer fields from caller-owned configuration.
	data, err := json.Marshal(configs)
	if err != nil {
		return nil, failure("invalid_configuration")
	}
	var items []config.Television
	if json.Unmarshal(data, &items) != nil {
		return nil, failure("invalid_configuration")
	}
	for i := range items {
		items[i].Defaults()
	}
	if config.ValidateTelevisions(items) != nil {
		return nil, failure("invalid_configuration")
	}
	m := &Manager{devices: map[string]*boundTV{}, wakeWait: 90 * time.Second, wakeInterval: 500 * time.Millisecond, wakeRetryInterval: defaultWakeRetryInterval, sendWake: sendMagicPacket}
	for _, c := range items {
		gate := make(chan struct{}, 1)
		gate <- struct{}{}
		m.devices[c.DeviceID] = &boundTV{config: c, gate: gate}
	}
	return m, nil
}

func (m *Manager) Binding(id string) Binding {
	b := Binding{Version: 1, DeviceID: id, Capabilities: []string{}}
	if m == nil {
		return b
	}
	d := m.devices[id]
	if d == nil {
		return b
	}
	b.Configured = true
	b.FallbackConfigured = d.config.Fallback != nil
	key, err := readCredentials(d.config.ClientKeyFile)
	b.Paired = err == nil && key.ClientKey != ""
	if d.config.Secure() {
		b.Paired = b.Paired && (d.config.CertificateSHA256 != "" || key.CertificateSHA256 != "")
		if d.config.CertificateSHA256 != "" && key.CertificateSHA256 != "" && d.config.CertificateSHA256 != key.CertificateSHA256 {
			b.Paired = false
		}
	}
	b.Capabilities = []string{"status", "volume", "mute", "off", "apps", "pair"}
	if d.config.MAC != "" || d.config.Fallback != nil {
		b.Capabilities = append(b.Capabilities, "on")
	}
	if hasLaunch(d.config) || len(d.config.AppAliases) != 0 {
		b.Capabilities = append(b.Capabilities, "launch", "go")
	}
	return b
}

func hasLaunch(c config.Television) bool { return c.AppID != "" || c.LaunchURL != "" }

func (m *Manager) Execute(ctx context.Context, id string, cmd Command) (Result, error) {
	r := Result{Version: 1, DeviceID: id, Action: cmd.Action, Transport: "webos", Outcome: "accepted"}
	if err := cmd.Validate(); err != nil {
		return r, err
	}
	if m == nil || m.devices[id] == nil {
		return r, failure("not_configured")
	}
	d := m.devices[id]
	c := d.config
	if cmd.App != "" {
		app, ok := c.AppAliases[cmd.App]
		if !ok {
			return r, failure("app_not_configured")
		}
		// Resolve an administrator-owned name for this request only. Keep the
		// ordinary OttPlay target intact for subsequent unqualified commands.
		c.AppID, c.LaunchURL = app, ""
	}
	ctx, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()
	select {
	case <-d.gate:
		defer func() { d.gate <- struct{}{} }()
	case <-ctx.Done():
		return r, failure("deadline")
	}
	if (cmd.Action == "launch" || cmd.Action == "go") && !hasLaunch(c) {
		return r, failure("launch_not_configured")
	}
	if cmd.Action == "on" || cmd.Action == "go" {
		return m.wake(ctx, c, cmd, r)
	}
	nativeCtx, nativeCancel := context.WithCancel(ctx)
	if cmd.Action != "pair" && c.Fallback != nil {
		nativeCancel()
		nativeCtx, nativeCancel = phaseContext(ctx, 6*time.Second, 2)
	}
	defer nativeCancel()
	s, err := connect(nativeCtx, c, cmd.Action == "pair")
	if err == nil {
		defer s.close()
		if cmd.Action == "pair" {
			if err = writeCredentials(c.ClientKeyFile, s.credentials); err != nil {
				return r, err
			}
			r.Outcome = "confirmed"
			r.Pairing = &Pairing{Paired: true}
			return r, nil
		}
		err = s.execute(nativeCtx, c, cmd, &r)
		if err == nil {
			return r, nil
		}
		if s.mutationAttempted {
			r.Outcome = "uncertain"
			return r, asUncertain(err)
		}
	}
	nativeCancel()
	if cmd.Action != "pair" && c.Fallback != nil && ctx.Err() == nil {
		return executeFallback(ctx, c, cmd, r)
	}
	return r, err
}

func asUncertain(err error) error {
	var e *Error
	if errors.As(err, &e) {
		return &Error{Code: e.Code, Uncertain: true}
	}
	return uncertain("operation_uncertain")
}

func (m *Manager) wake(ctx context.Context, c config.Television, cmd Command, r Result) (Result, error) {
	ready := func(probe context.Context) (bool, error) {
		s, err := connect(probe, c, false)
		if err != nil {
			return false, err
		}
		defer s.close()
		state, err := s.powerState(probe)
		if err != nil {
			return false, err
		}
		if state.Power != "on" {
			return false, failure("not_ready")
		}
		r.Transport = "webos"
		r.State = state
		r.Outcome = "confirmed"
		if cmd.Action == "go" {
			err = s.execute(ctx, c, Command{Action: "launch"}, &r)
			if err != nil && s.mutationAttempted {
				r.Outcome = "uncertain"
				err = asUncertain(err)
			}
		}
		return true, err
	}
	initialCtx, initialCancel := context.WithCancel(ctx)
	if c.Fallback != nil {
		initialCancel()
		initialCtx, initialCancel = phaseContext(ctx, 3*time.Second, 4)
	}
	done, err := ready(initialCtx)
	initialCancel()
	if done {
		return r, err
	}
	woken := false
	finish := func(result Result, err error) (Result, error) {
		// WOL delivery cannot confirm readiness, but it may already have
		// powered the TV on. Preserve that partial effect if go cannot finish.
		if err != nil && woken && cmd.Action == "go" {
			result.Outcome = "uncertain"
			return result, asUncertain(err)
		}
		return result, err
	}
	if c.MAC != "" && ctx.Err() == nil {
		if m.sendWake(ctx, c.MAC, c.Broadcast, c.WOLPort) == nil {
			woken = true
			r.Transport = "wake_on_lan"
			r.Outcome = "accepted"
			r.State = &State{Power: "unknown"}
			probe, cancel := m.wakeProbeContext(ctx, c.Fallback != nil)
			retryInterval := m.wakeRetryInterval
			if retryInterval <= 0 {
				retryInterval = defaultWakeRetryInterval
			}
			nextWake := time.Now().Add(retryInterval)
			for probe.Err() == nil {
				if done, err := ready(probe); done {
					cancel()
					return finish(r, err)
				}
				// Repeat only an idempotent magic packet while this foreground
				// wake is still pending. No timer survives this request.
				if probe.Err() == nil && !time.Now().Before(nextWake) {
					_ = m.sendWake(probe, c.MAC, c.Broadcast, c.WOLPort)
					nextWake = time.Now().Add(retryInterval)
				}
				if !wait(probe, m.wakeInterval) {
					break
				}
			}
			cancel()
		}
	}
	if c.Fallback != nil && ctx.Err() == nil {
		result, err := executeFallback(ctx, c, cmd, r)
		return finish(result, err)
	}
	if woken && cmd.Action == "on" {
		return r, nil
	}
	return finish(r, failure("not_ready"))
}

func (m *Manager) wakeProbeContext(ctx context.Context, hasFallback bool) (context.Context, context.CancelFunc) {
	if hasFallback {
		return phaseContext(ctx, m.wakeWait, 2)
	}
	return context.WithTimeout(ctx, m.wakeWait)
}

// Reserve part of the caller's existing budget for the fallback. No phase may
// extend the original deadline, including when an asleep TV silently drops SYNs.
func phaseContext(ctx context.Context, limit time.Duration, divisor int) (context.Context, context.CancelFunc) {
	if deadline, ok := ctx.Deadline(); ok {
		limit = min(limit, time.Until(deadline)/time.Duration(divisor))
	}
	return context.WithTimeout(ctx, limit)
}

func wait(ctx context.Context, delay time.Duration) bool {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func sendMagicPacket(ctx context.Context, mac, broadcast string, port int) error {
	if ctx.Err() != nil {
		return failure("deadline")
	}
	address, err := net.ParseMAC(mac)
	if err != nil || len(address) != 6 {
		return failure("invalid_configuration")
	}
	packet := make([]byte, 6, 102)
	for i := range packet {
		packet[i] = 255
	}
	for i := 0; i < 16; i++ {
		packet = append(packet, address...)
	}
	conn, err := (&net.Dialer{Timeout: 2 * time.Second}).DialContext(ctx, "udp4", net.JoinHostPort(broadcast, strconv.Itoa(port)))
	if err != nil {
		return failure("wake_unavailable")
	}
	defer conn.Close()
	stop := context.AfterFunc(ctx, func() { _ = conn.Close() })
	defer stop()
	deadline := time.Now().Add(2 * time.Second)
	if bound, ok := ctx.Deadline(); ok && bound.Before(deadline) {
		deadline = bound
	}
	_ = conn.SetWriteDeadline(deadline)
	if ctx.Err() != nil {
		return failure("deadline")
	}
	if n, err := conn.Write(packet); err != nil || n != len(packet) {
		return failure("wake_unavailable")
	}
	return nil
}
