package main

import (
	"crypto"
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestOutboundPeerKeyPolicy(t *testing.T) {
	strong, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	weak, err := rsa.GenerateKey(rand.Reader, 1024)
	if err != nil {
		t.Fatal(err)
	}
	ec, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	ec224, err := ecdsa.GenerateKey(elliptic.P224(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	_, ed, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name   string
		keys   []crypto.Signer
		accept bool
	}{
		{"weak leaf", []crypto.Signer{strong, weak}, false},
		{"weak intermediate", []crypto.Signer{strong, weak, ec}, false},
		{"weak root", []crypto.Signer{weak, ec}, false},
		{"RSA2048", []crypto.Signer{strong}, true},
		{"P256", []crypto.Signer{ec}, true},
		{"Ed25519", []crypto.Signer{ed}, true},
		{"P224 CA", []crypto.Signer{ec224, ec}, true},
	}
	for _, test := range cases {
		for _, version := range []uint16{tls.VersionTLS12, tls.VersionTLS13} {
			t.Run(test.name+"/"+tls.VersionName(version), func(t *testing.T) {
				fixture := certificateFixture(t, test.keys)
				pair, err := tls.LoadX509KeyPair(fixture.cert, fixture.key)
				if err != nil {
					t.Fatal(err)
				}
				var requests atomic.Int32
				peer := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests.Add(1)
					if r.Header.Get("Authorization") != "Bearer synthetic-fixture" {
						t.Error("missing synthetic authorization")
					}
					w.WriteHeader(http.StatusNoContent)
				}))
				peer.Config.ErrorLog = log.New(io.Discard, "", 0)
				peer.TLS = &tls.Config{MinVersion: version, MaxVersion: version, Certificates: []tls.Certificate{pair}}
				peer.StartTLS()
				defer peer.Close()
				client, transport := peerTestClient(t)
				transport.TLSClientConfig.MinVersion = version
				transport.TLSClientConfig.MaxVersion = version
				transport.TLSClientConfig.RootCAs = x509.NewCertPool()
				transport.TLSClientConfig.RootCAs.AddCert(fixture.root)
				request, err := http.NewRequest(http.MethodGet, peer.URL, nil)
				if err != nil {
					t.Fatal(err)
				}
				request.Header.Set("Authorization", "Bearer synthetic-fixture")
				response, err := client.Do(request)
				if !test.accept {
					if response != nil {
						response.Body.Close()
					}
					if !errors.Is(err, errPeerCertificateKey) || requests.Load() != 0 {
						t.Fatalf("weak peer received a request or was not rejected: requests=%d error=%v", requests.Load(), err)
					}
					return
				}
				if err != nil {
					t.Fatal(err)
				}
				response.Body.Close()
				if response.StatusCode != http.StatusNoContent || response.TLS == nil || len(response.TLS.VerifiedChains) == 0 || requests.Load() != 1 {
					t.Fatal("strong peer did not complete verified HTTPS")
				}
			})
		}
	}
}

func TestOutboundResumedWeakPeerRejected(t *testing.T) {
	strong, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	weak, err := rsa.GenerateKey(rand.Reader, 1024)
	if err != nil {
		t.Fatal(err)
	}
	fixture := certificateFixture(t, []crypto.Signer{strong, weak})
	pair, err := tls.LoadX509KeyPair(fixture.cert, fixture.key)
	if err != nil {
		t.Fatal(err)
	}
	var requests atomic.Int32
	peer := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { requests.Add(1); w.WriteHeader(http.StatusNoContent) }))
	peer.Config.ErrorLog = log.New(io.Discard, "", 0)
	peer.TLS = &tls.Config{MinVersion: tls.VersionTLS12, MaxVersion: tls.VersionTLS12, Certificates: []tls.Certificate{pair}}
	peer.StartTLS()
	defer peer.Close()
	cache := tls.NewLRUClientSessionCache(1)
	roots := x509.NewCertPool()
	roots.AddCert(fixture.root)
	first, oldTransport := peerTestClient(t)
	oldTransport.DisableKeepAlives = true
	oldTransport.TLSClientConfig.MaxVersion = tls.VersionTLS12
	oldTransport.TLSClientConfig.RootCAs = roots
	oldTransport.TLSClientConfig.ClientSessionCache = cache
	oldTransport.TLSClientConfig.VerifyConnection = nil // A session established before enforcing this policy.
	response, err := first.Get(peer.URL)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	oldTransport.CloseIdleConnections()
	second, transport := peerTestClient(t)
	transport.TLSClientConfig.MaxVersion = tls.VersionTLS12
	transport.TLSClientConfig.RootCAs = roots
	transport.TLSClientConfig.ClientSessionCache = cache
	verify := transport.TLSClientConfig.VerifyConnection
	var resumed atomic.Bool
	transport.TLSClientConfig.VerifyConnection = func(state tls.ConnectionState) error { resumed.Store(state.DidResume); return verify(state) }
	response, err = second.Get(peer.URL)
	if response != nil {
		response.Body.Close()
	}
	if !resumed.Load() || !errors.Is(err, errPeerCertificateKey) || requests.Load() != 1 {
		t.Fatalf("resumed weak peer not rejected before second HTTP request: resumed=%v requests=%d error=%v", resumed.Load(), requests.Load(), err)
	}
}

func TestOutboundPeerTrustAndHostname(t *testing.T) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	fixture := certificateFixture(t, []crypto.Signer{key})
	pair, err := tls.LoadX509KeyPair(fixture.cert, fixture.key)
	if err != nil {
		t.Fatal(err)
	}
	var requests atomic.Int32
	peer := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { requests.Add(1) }))
	peer.Config.ErrorLog = log.New(io.Discard, "", 0)
	peer.TLS = &tls.Config{MinVersion: tls.VersionTLS12, Certificates: []tls.Certificate{pair}}
	peer.StartTLS()
	defer peer.Close()
	for _, mode := range []string{"untrusted", "hostname"} {
		t.Run(mode, func(t *testing.T) {
			client, transport := peerTestClient(t)
			transport.TLSClientConfig.RootCAs = x509.NewCertPool()
			if mode == "hostname" {
				transport.TLSClientConfig.RootCAs.AddCert(fixture.root)
				transport.TLSClientConfig.ServerName = "wrong.invalid"
			}
			response, err := client.Get(peer.URL)
			if response != nil {
				response.Body.Close()
			}
			var verification *tls.CertificateVerificationError
			if !errors.As(err, &verification) || errors.Is(err, errPeerCertificateKey) || requests.Load() != 0 {
				t.Fatalf("normal trust/hostname validation bypassed: %v", err)
			}
		})
	}
}

func TestOutboundPeerAlternateAndMissingChains(t *testing.T) {
	strong, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	weak, err := rsa.GenerateKey(rand.Reader, 1024)
	if err != nil {
		t.Fatal(err)
	}
	good := []*x509.Certificate{{PublicKey: strong.Public()}}
	bad := []*x509.Certificate{{PublicKey: weak.Public()}}
	if err := verifyPeerCertificates(tls.ConnectionState{VerifiedChains: [][]*x509.Certificate{bad, good}}); err != nil {
		t.Fatal(err)
	}
	for i, state := range []tls.ConnectionState{{}, {VerifiedChains: [][]*x509.Certificate{{}}}, {VerifiedChains: [][]*x509.Certificate{{nil}}}, {VerifiedChains: [][]*x509.Certificate{bad}}, {VerifiedChains: [][]*x509.Certificate{{{PublicKey: "unsupported"}}}}} {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			if !errors.Is(verifyPeerCertificates(state), errPeerCertificateKey) {
				t.Fatal("invalid verified chain accepted")
			}
		})
	}
}

func peerTestClient(t *testing.T) (*http.Client, *http.Transport) {
	t.Helper()
	transport := newHealthcheckTransport()
	t.Cleanup(transport.CloseIdleConnections)
	return &http.Client{Transport: transport, Timeout: time.Second}, transport
}
