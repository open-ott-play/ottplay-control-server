package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"image"
	"image/png"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func digest(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }
func TestSignedUpdate(t *testing.T) {
	pub, key, _ := ed25519.GenerateKey(rand.Reader)
	m := Update{1, "agent", "0.1.1", packageName, "https://example.test/a", strings.Repeat("a", 64), 100}
	wrap := func(m Update) []byte {
		b, _ := json.Marshal(m)
		raw, _ := json.Marshal(map[string]string{"payload": base64.StdEncoding.EncodeToString(b), "signature": base64.StdEncoding.EncodeToString(ed25519.Sign(key, b))})
		return raw
	}
	raw := wrap(m)
	pk := base64.StdEncoding.EncodeToString(pub)
	if _, e := verifyUpdate(raw, digest(raw), pk); e != nil {
		t.Fatal(e)
	}
	for _, mutate := range []func(*Update){func(u *Update) { u.URL = "http://example.test" }, func(u *Update) { u.URL = "https://user:pass@example.test/a" }, func(u *Update) { u.Kind = "shell" }, func(u *Update) { u.Package = "another.app" }, func(u *Update) { u.Size = 65 * 1024 * 1024 }} {
		bad := m
		mutate(&bad)
		raw := wrap(bad)
		if _, e := verifyUpdate(raw, digest(raw), pk); e == nil {
			t.Fatal("accepted invalid signed payload")
		}
	}
	if _, e := verifyUpdate(raw, strings.Repeat("b", 64), pk); e == nil {
		t.Fatal("accepted bad digest")
	}
	other, _, _ := ed25519.GenerateKey(rand.Reader)
	if _, e := verifyUpdate(raw, digest(raw), base64.StdEncoding.EncodeToString(other)); e == nil {
		t.Fatal("accepted other key")
	}
	raw[len(raw)-8] ^= 1
	if _, e := verifyUpdate(raw, digest(raw), pk); e == nil {
		t.Fatal("accepted tampering")
	}
}
func TestDeferredACKRetryAndCrash(t *testing.T) {
	calls := 0
	reject := true
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if reject {
			w.WriteHeader(503)
		} else {
			w.Write([]byte(`{}`))
		}
	}))
	defer server.Close()
	a := &Agent{cfg: Config{Server: server.URL}, client: server.Client(), journal: map[string]Entry{}, effects: map[string]func() error{}, journalPath: filepath.Join(t.TempDir(), "journal.json")}
	r := Request{ID: strings.Repeat("a", 32)}
	effects := 0
	e := Entry{Result: ok(r.ID, map[string]any{"accepted": true}), Expires: time.Now().Add(time.Minute).Unix(), State: "prepared"}
	a.remember(r.ID, e)
	a.effects[r.ID] = func() error { effects++; return nil }
	a.deliver(context.Background(), r, e)
	if effects != 0 {
		t.Fatal("effect before ACK")
	}
	reject = false
	a.deliver(context.Background(), r, a.journal[r.ID])
	a.deliver(context.Background(), r, a.journal[r.ID])
	if effects != 1 || calls != 3 {
		t.Fatal("duplicate or missing effect")
	}
	// A restored journal cannot resurrect a closure after process death.
	a.remember(r.ID, e)
	delete(a.effects, r.ID)
	a.deliver(context.Background(), r, e)
	if effects != 1 {
		t.Fatal("effect replayed after crash")
	}
}
func TestExpiredRequestsAndValidation(t *testing.T) {
	a := &Agent{journal: map[string]Entry{}, effects: map[string]func() error{}, journalPath: filepath.Join(t.TempDir(), "journal.json")}
	a.process(context.Background(), Request{ID: strings.Repeat("a", 32), Expires: 1}, 2, time.Now())
	if len(a.journal) != 0 {
		t.Fatal("expired request processed")
	}
	for _, r := range []Request{
		{Action: "maintenance", Params: json.RawMessage(`{"operation":"shell","command":"id"}`)},
		{Action: "lifecycle", Params: json.RawMessage(`{"operation":"restart_app","operation":"reboot_device"}`)},
		{Action: "playback", Params: json.RawMessage(`{"operation":"seek","position":null}`)},
		{Action: "vportal_queue", Params: json.RawMessage(`{"operation":"play","ids":[1,1],"loop":true}`)},
		{Action: "vportal_queue", Params: json.RawMessage(`{"operation":"play","ids":[1],"loop":false}`)},
		{Action: "screenshot", Params: json.RawMessage(`{"runtime":"wrong"}`)},
	} {
		result, effect := a.execute(context.Background(), r)
		if result.Status != "rejected" || effect != nil {
			t.Fatal("invalid request admitted")
		}
	}
}
func TestWatchdog(t *testing.T) {
	now := time.Now()
	w := Watchdog{Progress: now, LastAction: now}
	p := map[string]any{"kiosk": map[string]any{"enabled": true, "state": "locked"}, "video": map[string]any{"source": "a", "position": float64(1)}}
	w.observe(now, p, true, false)
	if w.observe(now.Add(121*time.Second), p, true, true) != "" {
		t.Fatal("watchdog cancelled pause")
	}
	if w.observe(now.Add(242*time.Second), p, true, false) != "recover" {
		t.Fatal("no surface recovery")
	}
	if w.observe(now.Add(243*time.Second), p, true, false) != "" {
		t.Fatal("recovery without cooldown")
	}
	if w.observe(now.Add(364*time.Second), nil, false, false) != "restart_app" {
		t.Fatal("no unresponsive app escalation")
	}
}
func TestScreenshotPreservesAspectAndBounds(t *testing.T) {
	var b bytes.Buffer
	png.Encode(&b, image.NewRGBA(image.Rect(0, 0, 1280, 800)))
	s, e := boundedImage(b.Bytes())
	if e != nil {
		t.Fatal(e)
	}
	if s.width != 1152 || s.height != 720 || len(s.data) > 1024*1024 {
		t.Fatal("invalid screenshot scaling")
	}
	if _, e = boundedImage([]byte("not png")); e == nil {
		t.Fatal("invalid image accepted")
	}
}
func TestNoCredentialToDownloadAndNoRedirect(t *testing.T) {
	got := false
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = true
		if r.Header.Get("Authorization") != "" {
			t.Error("credential leaked")
		}
		http.Redirect(w, r, "/other", 302)
	}))
	defer s.Close()
	a := Agent{cfg: Config{Token: "private"}, client: s.Client()}
	a.client.CheckRedirect = newHTTPClient().CheckRedirect
	if _, e := a.download(context.Background(), s.URL, 1000); e == nil || !got {
		t.Fatal("redirect followed")
	}
}
func TestConfigPermissionsAndOrigins(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	c := Config{Server: "https://control.test/path", Token: strings.Repeat("a", 32), Device: "native", ParentDevice: "web", Origin: "https://player.test", UpdatePublicKey: base64.StdEncoding.EncodeToString(make([]byte, 32)), Model: "Q1001L4B2", Serial: "12345678"}
	b, _ := json.Marshal(c)
	os.WriteFile(path, b, 0600)
	if _, e := loadConfig(path); e != nil {
		t.Fatal(e)
	}
	os.Chmod(path, 0644)
	if _, e := loadConfig(path); e == nil {
		t.Fatal("world-readable config accepted")
	}
	os.Chmod(path, 0600)
	os.WriteFile(path, append(b, []byte(` {}`)...), 0600)
	if _, e := loadConfig(path); e == nil {
		t.Fatal("trailing config accepted")
	}
	for _, bad := range []string{"https://player.test.evil/", "https://u@player.test/", "http://player.test/", "https://other.test/"} {
		if sameOrigin(c.Origin, bad) {
			t.Fatal("wrong origin accepted")
		}
	}
}
