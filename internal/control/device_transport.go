package control

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/open-ott-play/ottplay-control-server/internal/wire"
)

const deviceTransportPath = "/api/device-transport"
const maxDeviceTransportBytes = maxResponseBytes + 8*1024
const smallDeviceTransportBytes = 64 * 1024

var deviceTransportToken = regexp.MustCompile(wire.TokenPattern)

// The envelope only transports existing device routes. It cannot submit
// commands, approve pairings, or address another HTTP server.
type deviceTransportEnvelope struct {
	Version int             `json:"version"`
	Method  string          `json:"method"`
	Path    string          `json:"path"`
	Token   *string         `json:"token,omitempty"`
	Body    json.RawMessage `json:"body,omitempty"`
	Headers json.RawMessage `json:"headers,omitempty"`
}

func deviceTransportRoute(method string, target *url.URL) bool {
	switch target.Path {
	case wire.CommandPath, wire.LegacyPollPath, "/api/notifications", "/api/notifications/media", "/api/discovery":
		return method == http.MethodGet
	case wire.AckPath, "/api/responses", playerStatePath, diagnosticsPrefix + "/runtimes", diagnosticsPrefix + "/poll", diagnosticsPrefix + "/results", diagnosticsPrefix + "/events", diagnosticsPrefix + "/repairs/poll", diagnosticsPrefix + "/repairs/results":
		return method == http.MethodPost
	case "/api/pairings":
		if method == http.MethodPost {
			return true // The existing handler rejects unexpected query parameters.
		}
		if method != http.MethodGet && method != http.MethodDelete {
			return false
		}
		// GET without an ID is the administrator listing, never a device route.
		q, err := url.ParseQuery(target.RawQuery)
		return err == nil && len(q) == 1 && len(q["id"]) == 1 && commandID.MatchString(q.Get("id"))
	}
	return false
}

func decodeDeviceTransport(raw []byte) (deviceTransportEnvelope, *url.URL, http.Header, bool) {
	var v deviceTransportEnvelope
	if !utf8.Valid(raw) || !diagDecode(raw, &v, []string{"version", "method", "path"}, "token", "body", "headers") || v.Version != 1 || len(v.Path) > 2048 {
		return v, nil, nil, false
	}
	target, err := url.ParseRequestURI(v.Path)
	if err != nil || target.Scheme != "" || target.Host != "" || target.User != nil || target.Opaque != "" || target.Fragment != "" || target.RawPath != "" || target.ForceQuery || !strings.HasPrefix(v.Path, "/") || strings.ContainsAny(v.Path, "\\#\r\n\t ") || !deviceTransportRoute(v.Method, target) {
		return v, nil, nil, false
	}
	headers := make(http.Header)
	if v.Token != nil {
		if !deviceTransportToken.MatchString(*v.Token) {
			return v, nil, nil, false
		}
		headers.Set("Authorization", "Bearer "+*v.Token)
	}
	if v.Method == http.MethodPost {
		// Preserve nested bytes. The destination owns schema and duplicate-key
		// validation; decoding/re-encoding here could change their meaning.
		if len(v.Body) == 0 || v.Body[0] != '{' {
			return v, nil, nil, false
		}
		headers.Set("Content-Type", "application/json")
	} else if len(v.Body) != 0 {
		return v, nil, nil, false
	}
	if len(v.Headers) != 0 {
		fields, err := decodeObject(v.Headers)
		if err != nil || len(fields) != 1 || !strings.HasPrefix(target.Path, diagnosticsPrefix+"/") {
			return v, nil, nil, false
		}
		var epoch string
		if json.Unmarshal(fields["X-OTT-Diagnostics-Epoch"], &epoch) != nil || !diagLabel.MatchString(epoch) {
			return v, nil, nil, false
		}
		headers.Set("X-OTT-Diagnostics-Epoch", epoch)
	}
	return v, target, headers, true
}

func (s *Server) serveDeviceTransport(w http.ResponseWriter, r *http.Request) {
	// Valid envelopes run the destination's CORS exactly once. Only envelope
	// errors and the outer POST preflight use CORS at this boundary.
	fail := func(status int, message string) {
		if s.cors(w, r, http.MethodPost, true) {
			failure(w, status, message)
		}
	}
	if r.Method == http.MethodOptions {
		s.cors(w, r, http.MethodPost, true)
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST, OPTIONS")
		fail(http.StatusMethodNotAllowed, "method is not allowed")
		return
	}
	if r.URL.RawQuery != "" || r.URL.ForceQuery || r.URL.RawPath != "" || len(r.Header.Values("Authorization")) != 0 {
		fail(http.StatusBadRequest, "invalid device transport request")
		return
	}
	if r.Context().Err() != nil {
		fail(http.StatusRequestTimeout, "device transport request was cancelled")
		return
	}
	// Physical ingress has a separate budget above the sum of the existing
	// device, four-runtime and public bootstrap ceilings. Logical allowances
	// remain owned by the original handler and are never charged twice.
	s.mu.Lock()
	allowed := s.transportRate.allow(s.now(), len(s.currentDevices())*1600+300)
	s.mu.Unlock()
	if !allowed {
		w.Header().Set("Retry-After", "60")
		fail(http.StatusTooManyRequests, "device transport rate exceeded")
		return
	}
	slots := s.transportSlots
	if r.ContentLength < 0 || r.ContentLength > smallDeviceTransportBytes {
		slots = s.transportLarge
	}
	select {
	case slots <- struct{}{}:
		defer func() { <-slots }()
	default:
		w.Header().Set("Retry-After", "1")
		fail(http.StatusTooManyRequests, "device transport is busy")
		return
	}
	if len(r.TransferEncoding) != 0 || r.Header.Get("Transfer-Encoding") != "" || r.Header.Get("Expect") != "" {
		fail(http.StatusBadRequest, "unsupported request framing")
		return
	}
	if strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0]) != "application/json" {
		fail(http.StatusUnsupportedMediaType, "Content-Type must be application/json")
		return
	}
	if r.ContentLength > maxDeviceTransportBytes {
		fail(http.StatusRequestEntityTooLarge, "request body is too large")
		return
	}
	limit := int64(maxDeviceTransportBytes)
	if slots == s.transportSlots {
		limit = smallDeviceTransportBytes
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
	if err != nil {
		fail(http.StatusRequestEntityTooLarge, "cannot read bounded request body")
		return
	}
	if r.ContentLength >= 0 && int64(len(raw)) != r.ContentLength {
		fail(http.StatusBadRequest, "request body length does not match Content-Length")
		return
	}
	v, target, headers, ok := decodeDeviceTransport(raw)
	if !ok {
		fail(http.StatusBadRequest, "invalid device transport envelope")
		return
	}
	if r.Context().Err() != nil {
		fail(http.StatusRequestTimeout, "device transport request was cancelled")
		return
	}
	// Retain cancellation and the real peer/TLS identity. No envelope property
	// can override browser Origin or supply proxy/admin headers.
	inner := r.Clone(r.Context())
	inner.Method, inner.URL, inner.RequestURI = v.Method, target, v.Path
	inner.Header = headers
	if origins, present := r.Header["Origin"]; present {
		inner.Header["Origin"] = append([]string(nil), origins...)
	}
	inner.Body = io.NopCloser(bytes.NewReader(v.Body))
	inner.GetBody = nil
	inner.ContentLength = int64(len(v.Body))
	inner.TransferEncoding = nil
	inner.Trailer = nil
	// No wrapper, redirects or outbound HTTP. The original handler writes its
	// bytes, status and headers directly to the original response.
	s.ServeHTTP(w, inner)
}
