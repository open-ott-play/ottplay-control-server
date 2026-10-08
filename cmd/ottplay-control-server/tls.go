package main

import (
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"errors"
)

// loadServerTLSConfig checks only the configured certificate chain's key sizes.
// Clients remain responsible for trust, hostname and validity verification.
func loadServerTLSConfig(certFile, keyFile string) (*tls.Config, error) {
	config := &tls.Config{MinVersion: tls.VersionTLS12}
	if certFile == "" {
		return config, nil
	}
	certificate, err := tls.LoadX509KeyPair(certFile, keyFile)
	if err != nil {
		return nil, err
	}
	for _, der := range certificate.Certificate {
		parsed, err := x509.ParseCertificate(der)
		if err != nil {
			return nil, err
		}
		if !strongCertificateKey(parsed.PublicKey) {
			return nil, errors.New("TLS certificate chain contains an unsupported or undersized key")
		}
	}
	config.Certificates = []tls.Certificate{certificate}
	return config, nil
}

func strongCertificateKey(key any) bool {
	switch key := key.(type) {
	case *rsa.PublicKey:
		return key.N.BitLen() >= 2048
	case *ecdsa.PublicKey:
		return key.Curve.Params().BitSize >= 224
	case ed25519.PublicKey:
		return len(key) == ed25519.PublicKeySize
	default:
		return false
	}
}
