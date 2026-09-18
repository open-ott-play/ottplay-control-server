package main

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func TestCLIInitValidateAndNoSecrets(t *testing.T) {
	p := filepath.Join(t.TempDir(), "config.json")
	var out, errOut bytes.Buffer
	if run([]string{"init", "--config", p, "--device-id", "tv"}, &out, &errOut) != 0 {
		t.Fatal(errOut.String())
	}
	c, err := config.Load(p)
	if err != nil {
		t.Fatal(err)
	}
	if run([]string{"validate", "--config", p}, &out, &errOut) != 0 {
		t.Fatal(errOut.String())
	}
	if run([]string{"init", "--config", p, "--device-id", "tv"}, &out, &errOut) == 0 {
		t.Fatal("init replaced configuration")
	}
	if run([]string{"serve", "--config", p, "--tls-key", "missing"}, &out, &errOut) != 2 {
		t.Fatal("accepted unpaired TLS options")
	}
	for _, token := range []string{c.AdminToken, c.Devices[0].Token} {
		if strings.Contains(out.String()+errOut.String(), token) {
			t.Fatal("CLI disclosed credentials")
		}
	}
	if run([]string{"version"}, &out, &errOut) != 0 || !strings.Contains(out.String(), version) {
		t.Fatal("missing version")
	}
}

func TestCLIHealthcheckAndErrors(t *testing.T) {
	var out, errOut bytes.Buffer
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" {
			w.WriteHeader(200)
		} else {
			w.WriteHeader(503)
		}
	}))
	defer s.Close()
	if run([]string{"healthcheck", "--url", s.URL + "/healthz"}, &out, &errOut) != 0 {
		t.Fatal("healthy probe failed")
	}
	if run([]string{"healthcheck", "--url", s.URL + "/readyz"}, &out, &errOut) == 0 {
		t.Fatal("unhealthy probe passed")
	}
	for _, args := range [][]string{nil, {"unknown"}, {"validate"}, {"version", "extra"}, {"init", "--private-secret"}, {"healthcheck", "--url", "http://secret:password@example.com"}} {
		if run(args, &out, &errOut) == 0 {
			t.Fatalf("accepted invalid CLI arguments %v", args)
		}
	}
	if strings.Contains(errOut.String(), "password") || strings.Contains(errOut.String(), "private-secret") {
		t.Fatal("CLI error reflected secret input")
	}
}
