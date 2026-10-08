package main

import (
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/rsa"
	"crypto/tls"
	"errors"
)

var errPeerCertificateKey = errors.New("TLS peer certificate chain does not meet minimum key strength")

// verifyPeerCertificates runs after normal trust and hostname validation,
// including resumed connections. One complete strong verified path is enough.
func verifyPeerCertificates(state tls.ConnectionState) error {
	for _, chain := range state.VerifiedChains {
		strong := len(chain) != 0
		for _, certificate := range chain {
			if certificate == nil || !strongCertificateKey(certificate.PublicKey) {
				strong = false
				break
			}
		}
		if strong {
			return nil
		}
	}
	return errPeerCertificateKey
}

// Kept local because the maintenance agent is an independently built Go module.
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
