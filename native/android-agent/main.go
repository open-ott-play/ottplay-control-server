package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
)

//go:embed roots.pem
var rootsPEM []byte

type Request struct {
	ID      string          `json:"id"`
	Action  string          `json:"action"`
	Params  json.RawMessage `json:"params"`
	Expires float64         `json:"expires_at"`
}
type Result struct {
	ID     string `json:"id"`
	Status string `json:"status"`
	Data   any    `json:"data"`
}
type Entry struct {
	Action  string `json:"action"`
	Result  Result `json:"result"`
	Expires int64  `json:"expires"`
	State   string `json:"state"`
}
type Agent struct {
	serverTime    float64
	cfg           Config
	client        *http.Client
	runtime       string
	journal       map[string]Entry
	journalPath   string
	effects       map[string]func() error
	cdp           *CDP
	logs          []map[string]any
	watchdog      Watchdog
	suspended     bool
	lastOperation map[string]any
}

func newHTTPClient() *http.Client {
	roots, _ := x509.SystemCertPool()
	if roots == nil {
		roots = x509.NewCertPool()
	}
	roots.AppendCertsFromPEM(rootsPEM)
	return &http.Client{Timeout: 10 * time.Second, Transport: &http.Transport{DialContext: androidDialContext, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots, VerifyConnection: verifyPeerCertificates}, ResponseHeaderTimeout: 6 * time.Second}, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirects disabled") }}
}
func (a *Agent) event(kind string) {
	a.logs = append(a.logs, map[string]any{"time": time.Now().Unix(), "event": kind})
	if len(a.logs) > 50 {
		a.logs = a.logs[len(a.logs)-50:]
	}
}
func (a *Agent) api(ctx context.Context, path string, value any, out any) error {
	var body io.Reader
	method := "GET"
	if value != nil {
		b, e := json.Marshal(value)
		if e != nil {
			return e
		}
		body = bytes.NewReader(b)
		method = "POST"
	}
	r, e := http.NewRequestWithContext(ctx, method, strings.TrimRight(a.cfg.Server, "/")+path, body)
	if e != nil {
		return e
	}
	r.Header.Set("Authorization", "Bearer "+a.cfg.Token)
	r.Header.Set("User-Agent", "ottplay-android-agent/"+version)
	r.Header.Set("Content-Type", "application/json")
	resp, e := a.client.Do(r)
	if e != nil {
		return errors.New("controller unavailable")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("controller HTTP %d", resp.StatusCode)
	}
	b, e := io.ReadAll(io.LimitReader(resp.Body, 2*1024*1024+1))
	if e != nil {
		return e
	}
	if len(b) > 2*1024*1024 {
		return errors.New("oversized controller response")
	}
	if out != nil {
		return json.Unmarshal(b, out)
	}
	var ack struct {
		Status string `json:"status"`
	}
	if json.Unmarshal(b, &ack) != nil || ack.Status != "ok" {
		return errors.New("invalid controller acknowledgement")
	}
	return nil
}
func (a *Agent) remember(id string, e Entry) error {
	next := map[string]Entry{}
	for k, v := range a.journal {
		if float64(v.Expires) > a.serverTime {
			next[k] = v
		}
	}
	if len(next) >= 100 {
		if _, exists := next[id]; !exists {
			return errors.New("journal full")
		}
	}
	next[id] = e
	encoded, err := json.Marshal(next)
	if err != nil || len(encoded) > 4*1024*1024 {
		return errors.New("journal byte limit exceeded")
	}
	if err := atomicFile(a.journalPath, encoded, 0600); err != nil {
		return err
	}
	a.journal = next
	return nil
}
func rejected(id, reason string) Result {
	return Result{id, "rejected", map[string]any{"error": reason}}
}
func ok(id string, data any) Result { return Result{id, "ok", data} }
func (a *Agent) deliver(ctx context.Context, r Request, e Entry) {
	result := e.Result
	effect := a.effects[r.ID]
	if e.State == "started" || (e.State == "prepared" && effect == nil) {
		result = rejected(r.ID, "Execution interrupted; inspect Android status before retrying")
	}
	if err := a.api(ctx, "/api/responses", result, nil); err != nil {
		a.event("result_ack_failed")
		return
	}
	if e.State != "prepared" || effect == nil || ctx.Err() != nil {
		return
	}
	// Claim durably before the effect. A crash or lost reply never repeats a reboot.
	e.State = "claimed"
	if a.remember(r.ID, e) != nil {
		a.event("effect_claim_failed")
		return
	}
	delete(a.effects, r.ID)
	a.lastOperation = map[string]any{"request_id": r.ID, "operation": r.Action, "state": "executing"}
	if effect() != nil {
		e.State = "failed"
		a.event("effect_failed")
	} else {
		e.State = "completed"
	}
	a.lastOperation["state"] = e.State
	_ = a.remember(r.ID, e)
}
func (a *Agent) process(ctx context.Context, r Request, serverTime float64, received time.Time) {
	left := r.Expires - serverTime - time.Since(received).Seconds()
	if !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(r.ID) || left <= 0 || left > 3600 {
		return
	}
	budget := time.Duration(left * float64(time.Second))
	if budget > 35*time.Second {
		budget = 35 * time.Second
	}
	call, cancel := context.WithTimeout(ctx, budget)
	defer cancel()
	if cached, exists := a.journal[r.ID]; exists {
		a.deliver(call, r, cached)
		return
	}
	e := Entry{Action: r.Action, Result: rejected(r.ID, "Execution not confirmed"), Expires: int64(r.Expires) + 2, State: "started"}
	if a.remember(r.ID, e) != nil {
		a.event("journal_write_failed")
		return
	}
	result, effect := a.execute(call, r)
	if r.Action == "screenshot" {
		e.Result = rejected(r.ID, "Capture already attempted; image is not retained on disk")
		e.State = "completed"
		if a.remember(r.ID, e) == nil {
			_ = a.api(call, "/api/responses", result, nil)
		}
		return
	}
	e.Result = result
	e.State = "completed"
	if effect != nil {
		e.State = "prepared"
	}
	if a.remember(r.ID, e) != nil {
		a.event("result_write_failed")
		_ = os.Remove(dataDir + "/update-" + r.ID + ".apk")
		return
	}
	if effect != nil {
		a.effects[r.ID] = effect
	}
	a.deliver(call, r, e)
}

// The server may save a response and lose the HTTP ACK. It then removes the
// request from polls, so retry prepared receipts independently of redelivery.
func (a *Agent) flushPending(ctx context.Context, serverTime float64) {
	started := time.Now()
	for id := range a.effects {
		e, exists := a.journal[id]
		remaining := float64(e.Expires) - 2 - serverTime - time.Since(started).Seconds()
		if !exists || e.State != "prepared" || remaining <= 0 {
			continue
		}
		budget := time.Duration(remaining * float64(time.Second))
		if budget > 10*time.Second {
			budget = 10 * time.Second
		}
		call, cancel := context.WithTimeout(ctx, budget)
		a.deliver(call, Request{ID: id, Action: e.Action}, e)
		cancel()
	}
}
func (a *Agent) run(ctx context.Context) {
	timer := time.NewTicker(2 * time.Second)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}
		var poll struct {
			Protocol   int       `json:"request_protocol"`
			ServerTime float64   `json:"server_time"`
			Requests   []Request `json:"requests"`
		}
		started := time.Now()
		if e := a.api(ctx, "/api/webhook/commands?delivery=ack", nil, &poll); e != nil {
			a.event("poll_failed")
		} else if poll.Protocol != 1 || poll.ServerTime <= 0 || len(poll.Requests) > 50 {
			a.event("invalid_poll")
		} else {
			a.serverTime = poll.ServerTime
			a.flushPending(ctx, poll.ServerTime)
			_ = os.Remove(dataDir + "/update.pending")
			for _, r := range poll.Requests {
				// Keep unloading operations ordered until their ACK is settled.
				if len(a.effects) > 0 {
					break
				}
				a.process(ctx, r, poll.ServerTime, started)
			}
		}
		// The local watchdog continues even when the controller is unreachable.
		a.watch(ctx)
		for id := range a.effects {
			if e, ok := a.journal[id]; !ok || float64(e.Expires) <= a.serverTime {
				delete(a.effects, id)
				_ = os.Remove(dataDir + "/update-" + id + ".apk")
			}
		}
	}
}
func runCommand(ctx context.Context, name string, args ...string) ([]byte, error) {
	return exec.CommandContext(ctx, name, args...).Output()
}
func property(name string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	b, _ := runCommand(ctx, "/system/bin/getprop", name)
	return strings.TrimSpace(string(b))
}
func main() {
	if len(os.Args) == 2 && os.Args[1] == "--version" {
		fmt.Println(version)
		return
	}
	configPath := dataDir + "/config.json"
	checkOnly := len(os.Args) == 3 && os.Args[1] == "--check-config"
	if checkOnly {
		configPath = os.Args[2]
	}
	cfg, e := loadConfig(configPath)
	if e != nil {
		fmt.Fprintln(os.Stderr, "Invalid native configuration")
		os.Exit(2)
	}
	if os.Getuid() != 0 || property("ro.product.model") != cfg.Model || property("ro.serialno") != cfg.Serial || property("ro.build.version.sdk") != "19" {
		fmt.Fprintln(os.Stderr, "Unsupported Android installation")
		os.Exit(2)
	}
	if checkOnly {
		return
	}
	lock, e := os.OpenFile(dataDir+"/agent.lock", os.O_CREATE|os.O_RDWR, 0600)
	if e != nil || syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		fmt.Fprintln(os.Stderr, "Agent already running or lock unavailable")
		os.Exit(2)
	}
	defer lock.Close()
	journal := map[string]Entry{}
	if b, e := os.ReadFile(dataDir + "/journal.json"); e == nil {
		if len(b) > 4*1024*1024 || json.Unmarshal(b, &journal) != nil || len(journal) > 100 {
			fmt.Fprintln(os.Stderr, "Invalid operation journal")
			os.Exit(2)
		}
	} else if !os.IsNotExist(e) {
		os.Exit(2)
	}
	a := &Agent{cfg: cfg, client: newHTTPClient(), runtime: "android-" + randomID(), journal: journal, journalPath: dataDir + "/journal.json", effects: map[string]func() error{}, cdp: &CDP{Origin: cfg.Origin}}
	b, _ := os.ReadFile(dataDir + "/suspended.json")
	_ = json.Unmarshal(b, &a.suspended)
	// Any staged operation from the old process lost its execution closure.
	stages, _ := filepath.Glob(dataDir + "/update-*.apk")
	for _, stage := range stages {
		_ = os.Remove(stage)
	}
	a.watchdog.Progress = time.Now()
	a.watchdog.LastAction = time.Now()
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	a.run(ctx)
}
