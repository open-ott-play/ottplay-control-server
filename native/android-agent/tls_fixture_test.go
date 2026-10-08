package main

import (
	"crypto"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
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
