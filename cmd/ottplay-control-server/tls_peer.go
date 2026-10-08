package main

import (
	"crypto/tls"
	"errors"
	"net"
	"net/http"
	"time"
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

// Own the transport while preserving the standard Go HTTP transport defaults.
// Do not mutate or depend on the replaceable process-wide DefaultTransport.
func newHealthcheckTransport() *http.Transport {
	return &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           (&net.Dialer{Timeout: 30 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          100,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ExpectContinueTimeout: time.Second,
		TLSClientConfig:       &tls.Config{MinVersion: tls.VersionTLS12, VerifyConnection: verifyPeerCertificates},
	}
}
