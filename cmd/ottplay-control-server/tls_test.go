package main

import (
	"context"
	"crypto"
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

type serverCertificateFixture struct {
	cert, key string
	root      *x509.Certificate
}

func certificateFixture(t *testing.T, keys []crypto.Signer) serverCertificateFixture {
	t.Helper()
	var chain [][]byte
	var parent, root *x509.Certificate
	var parentKey crypto.Signer
	for i, key := range keys {
		template := &x509.Certificate{
			SerialNumber: big.NewInt(int64(i + 1)),
			Subject:      pkix.Name{CommonName: "Synthetic local TLS test"},
			NotBefore:    time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
			BasicConstraintsValid: true, IsCA: true,
			KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
			ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
			IPAddresses: []net.IP{net.ParseIP("127.0.0.1")},
		}
		issuer, issuerKey := parent, parentKey
		if issuer == nil {
			issuer, issuerKey = template, key
		}
		der, err := x509.CreateCertificate(rand.Reader, template, issuer, key.Public(), issuerKey)
		if err != nil {
			t.Fatal(err)
		}
		parent, err = x509.ParseCertificate(der)
		if err != nil {
			t.Fatal(err)
		}
		if root == nil {
			root = parent
		}
		parentKey = key
		chain = append([][]byte{der}, chain...)
	}
	directory := t.TempDir()
	fixture := serverCertificateFixture{filepath.Join(directory, "certificate.pem"), filepath.Join(directory, "key.pem"), root}
	var certificates []byte
	for _, der := range chain {
		certificates = append(certificates, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})...)
	}
	privateKey, err := x509.MarshalPKCS8PrivateKey(keys[len(keys)-1])
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(fixture.cert, certificates, 0600); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(fixture.key, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateKey}), 0600); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestServerCertificatePolicyBinary(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "control-server")
	if runtime.GOOS == "windows" {
		binary += ".exe"
	}
	build := exec.Command("go", "build", "-mod=readonly", "-o", binary, ".")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build binary: %v\n%s", err, output)
	}
	strong, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	weak, err := rsa.GenerateKey(rand.Reader, 1024)
	if err != nil {
		t.Fatal(err)
	}
	ecdsaKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	_, edKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name   string
		keys   []crypto.Signer // Root to leaf, including any intermediate.
		accept bool
	}{
		{"weak leaf", []crypto.Signer{strong, weak}, false},
		{"weak intermediate", []crypto.Signer{strong, weak, strong}, false},
		{"weak included root", []crypto.Signer{weak, strong}, false},
		{"RSA2048", []crypto.Signer{strong}, true},
		{"P256", []crypto.Signer{ecdsaKey}, true},
		{"Ed25519", []crypto.Signer{edKey}, true},
		{"strong mixed chain", []crypto.Signer{strong, ecdsaKey, edKey}, true},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			fixture := certificateFixture(t, test.keys)
			configPath := filepath.Join(t.TempDir(), "config.json")
			if output, err := exec.Command(binary, "init", "--config", configPath, "--device-id", "synthetic-tv").CombinedOutput(); err != nil {
				t.Fatalf("initialize synthetic config: %v\n%s", err, output)
			}
			if !test.accept {
				ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
				defer cancel()
				command := exec.CommandContext(ctx, binary, "serve", "--config", configPath, "--listen", "127.0.0.1:0", "--tls-cert", fixture.cert, "--tls-key", fixture.key)
				output, err := command.CombinedOutput()
				if ctx.Err() != nil || err == nil || strings.TrimSpace(string(output)) != "Invalid TLS certificate or key." {
					t.Fatalf("weak configured chain was not rejected before serving: err=%v deadline=%v output=%q", err, ctx.Err(), output)
				}
				return
			}
			assertBinaryHTTPS(t, binary, configPath, fixture)
		})
	}
}

func assertBinaryHTTPS(t *testing.T, binary, configPath string, fixture serverCertificateFixture) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	if err = listener.Close(); err != nil {
		t.Fatal(err)
	}
	command := exec.Command(binary, "serve", "--config", configPath, "--listen", address, "--tls-cert", fixture.cert, "--tls-key", fixture.key)
	if err = command.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = command.Process.Kill(); _ = command.Wait() })
	roots := x509.NewCertPool()
	roots.AddCert(fixture.root)
	transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots}, DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: time.Second}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		response, err := client.Get("https://" + address + "/healthz")
		if err == nil {
			_ = response.Body.Close()
			if response.StatusCode != http.StatusOK || response.TLS == nil || len(response.TLS.VerifiedChains) == 0 {
				t.Fatal("health endpoint did not complete normal verified HTTPS")
			}
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("accepted certificate did not serve the health endpoint")
}
