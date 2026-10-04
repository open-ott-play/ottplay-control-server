package control

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const diagnosticsPrefix = "/api/v2/diagnostics"
const diagSafeInteger = 9007199254740991

var diagLabel = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,80}$`)

type diagBudget struct {
	at   time.Time
	used int
}

func (b *diagBudget) allow(now time.Time, cost, limit int) bool {
	if b.at.IsZero() || now.Sub(b.at) >= time.Minute {
		b.at = now
		b.used = 0
	}
	if cost > limit-b.used {
		return false
	}
	b.used += cost
	return true
}

type diagConsent struct {
	Granted *bool  `json:"granted"`
	Epoch   string `json:"epoch,omitempty"`
}

func (c *diagConsent) UnmarshalJSON(b []byte) error {
	type plain diagConsent
	var value plain
	if !diagDecode(b, &value, []string{"granted"}, "epoch") {
		return errors.New("invalid consent fields")
	}
	var fields map[string]json.RawMessage
	if e := json.Unmarshal(b, &fields); e != nil {
		return e
	}
	if value.Granted != nil && !*value.Granted {
		if _, present := fields["epoch"]; present {
			return errors.New("false consent must omit epoch")
		}
	}
	*c = diagConsent(value)
	return nil
}
func (c diagConsent) valid() bool {
	return c.Granted != nil && ((*c.Granted && diagLabel.MatchString(c.Epoch)) || (!*c.Granted && c.Epoch == ""))
}

type diagOperator struct {
	id               string
	digest           [32]byte
	devices, actions map[string]bool
	rate             diagBudget
}
type diagRuntime struct {
	id, device, instance, boot, uuid      string
	digest                                [32]byte
	capabilities                          []string
	consent                               bool
	consentEpoch                          string
	expires, lastSeen                     time.Time
	pollSeq                               uint64
	pollHash                              [32]byte
	revision                              uint64
	active                                string
	pendingRepair                         string
	controlRate, eventRate, eventByteRate diagBudget
}
type diagControl struct {
	RequestID    string `json:"request_id"`
	SessionID    string `json:"session_id"`
	Action       string `json:"action"`
	Revision     uint64 `json:"-"`
	ConsentEpoch string `json:"consent_epoch"`
	LeaseMS      int64  `json:"lease_ms"`
	Profile      string `json:"profile,omitempty"`
}
type diagEvent struct {
	seq  uint64
	data json.RawMessage
	at   time.Time
}
type diagSession struct {
	id, device, runtime, consentEpoch, state string
	deadline, retainUntil                    time.Time
	control                                  diagControl
	confirmed                                bool
	results                                  map[string][32]byte
	events                                   []diagEvent
	eventBytes                               int
	accepted, dropped, truncated             uint64
	lastFirst                                uint64
	lastHash                                 [32]byte
	lastBatch                                bool
	stopKey                                  string
	stopReserved                             bool
}
type diagIdempotency struct {
	fingerprint [32]byte
	reply       map[string]any
	until       time.Time
}
type diagnosticsState struct {
	mu                          sync.Mutex
	epoch                       string
	cfg                         config.Diagnostics
	configured                  bool
	enabled                     map[string]bool
	operators                   []*diagOperator
	runtimes                    map[string]*diagRuntime
	sessions                    map[string]*diagSession
	repairs                     map[string]*diagRepair
	idempotency                 map[string]diagIdempotency
	registrations               map[string]*diagBudget
	globalControl, globalEvents diagBudget
	eventBytes                  int
	reservedStops               int
	random                      io.Reader
	controlSlots, eventSlots    chan struct{}
}

func diagRandom(r io.Reader, n int) (string, error) {
	b := make([]byte, n)
	if _, e := io.ReadFull(r, b); e != nil {
		return "", errors.New("diagnostic entropy unavailable")
	}
	return hex.EncodeToString(b), nil
}
func newDiagnostics(c config.Config) (*diagnosticsState, error) {
	epoch, e := diagRandom(rand.Reader, 16)
	if e != nil {
		return nil, e
	}
	d := &diagnosticsState{epoch: epoch, enabled: map[string]bool{}, runtimes: map[string]*diagRuntime{}, sessions: map[string]*diagSession{}, repairs: map[string]*diagRepair{}, idempotency: map[string]diagIdempotency{}, registrations: map[string]*diagBudget{}, random: rand.Reader, controlSlots: make(chan struct{}, 64), eventSlots: make(chan struct{}, 16)}
	d.cfg.Defaults()
	if c.Diagnostics != nil {
		d.configured = true
		d.cfg = *c.Diagnostics
	}
	for _, v := range c.Devices {
		d.enabled[v.ID] = d.configured && v.Diagnostics != nil && v.Diagnostics.Enabled
		d.registrations[v.ID] = &diagBudget{}
	}
	for _, v := range d.cfg.Operators {
		b, _ := hex.DecodeString(v.CredentialSHA256)
		o := &diagOperator{id: v.ID, devices: map[string]bool{}, actions: map[string]bool{}}
		copy(o.digest[:], b)
		for _, id := range v.DeviceIDs {
			o.devices[id] = true
		}
		for _, a := range v.Actions {
			o.actions[a] = true
		}
		d.operators = append(d.operators, o)
	}
	return d, nil
}
func diagError(code string) map[string]any {
	return map[string]any{"error": map[string]string{"code": code}}
}
func (d *diagnosticsState) encode(status int, value map[string]any, limit int) (int, []byte) {
	value["diagnostics_protocol"] = 2
	value["server_epoch"] = d.epoch
	b, e := json.Marshal(value)
	if e != nil || len(b) > limit {
		status = 503
		b, _ = json.Marshal(map[string]any{"diagnostics_protocol": 2, "server_epoch": d.epoch, "error": map[string]string{"code": "response_limit"}})
	}
	return status, b
}
func (d *diagnosticsState) write(w http.ResponseWriter, status int, value map[string]any) {
	status, b := d.encode(status, value, 8192)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(b)
}
func diagMethods(path string) (string, bool) {
	switch path {
	case diagnosticsPrefix + "/runtimes":
		return "GET,POST", false
	case diagnosticsPrefix + "/poll", diagnosticsPrefix + "/results", diagnosticsPrefix + "/events", diagnosticsPrefix + "/repairs/poll", diagnosticsPrefix + "/repairs/results":
		return "POST", true
	case diagnosticsPrefix + "/sessions", diagnosticsPrefix + "/repairs":
		return "POST", false
	}
	parts := strings.Split(strings.TrimPrefix(path, diagnosticsPrefix+"/"), "/")
	if len(parts) == 2 && commandID.MatchString(parts[1]) {
		if parts[0] == "runtimes" {
			return "DELETE", false
		}
		if parts[0] == "sessions" || parts[0] == "repairs" {
			return "GET", false
		}
	}
	if len(parts) == 3 && parts[0] == "sessions" && commandID.MatchString(parts[1]) {
		if parts[2] == "stop" {
			return "POST", false
		}
		if parts[2] == "events" {
			return "GET", false
		}
	}
	return "", false
}
func (s *Server) serveDiagnostics(w http.ResponseWriter, r *http.Request, path string) {
	d := s.diagnostics
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-OTT-Diagnostics-Epoch", d.epoch)
	w.Header().Add("Vary", "Origin")
	methods, runtimeRoute := diagMethods(path)
	if methods == "" {
		d.write(w, 404, diagError("not_found"))
		return
	}
	intended := r.Method
	if intended == "OPTIONS" {
		intended = r.Header.Get("Access-Control-Request-Method")
	}
	if path == diagnosticsPrefix+"/runtimes" && intended == "POST" {
		runtimeRoute = true
	}
	origins := r.Header.Values("Origin")
	origin := r.Header.Get("Origin")
	if len(origins) > 1 || (len(origins) == 1 && (!s.origins[origin] && !(s.allowNull && runtimeRoute && origin == "null"))) {
		d.write(w, 403, diagError("origin_denied"))
		return
	}
	if origin != "" {
		w.Header().Set("Access-Control-Allow-Origin", origin)
		w.Header().Set("Access-Control-Expose-Headers", "X-OTT-Diagnostics-Epoch")
	}
	if r.Method == "OPTIONS" {
		w.Header().Add("Vary", "Access-Control-Request-Method")
		w.Header().Add("Vary", "Access-Control-Request-Headers")
		if origin == "" || intended == "" || !strings.Contains(","+methods+",", ","+intended+",") {
			d.write(w, 403, diagError("preflight_denied"))
			return
		}
		for _, h := range strings.Split(r.Header.Get("Access-Control-Request-Headers"), ",") {
			h = strings.ToLower(strings.TrimSpace(h))
			if h != "" && h != "authorization" && h != "content-type" && h != "x-ott-diagnostics-epoch" {
				d.write(w, 403, diagError("preflight_denied"))
				return
			}
		}
		w.Header().Set("Access-Control-Allow-Methods", strings.ReplaceAll(methods, ",", ", "))
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, X-OTT-Diagnostics-Epoch")
		d.write(w, 200, map[string]any{})
		return
	}
	if !strings.Contains(","+methods+",", ","+r.Method+",") {
		w.Header().Set("Allow", methods+", OPTIONS")
		d.write(w, 405, diagError("method_denied"))
		return
	}
	now := s.now()
	d.mu.Lock()
	d.expire(now)
	status, code := d.preauthenticate(s, r, path)
	d.mu.Unlock()
	if status != 0 {
		d.write(w, status, diagError(code))
		return
	}
	slots := d.controlSlots
	if path == diagnosticsPrefix+"/events" {
		slots = d.eventSlots
	}
	select {
	case slots <- struct{}{}:
		defer func() { <-slots }()
	default:
		d.write(w, 429, diagError("concurrency_limit"))
		return
	}
	d.mu.Lock()
	b := &d.globalControl
	limit := len(s.devices)*4*240 + 240
	if path == diagnosticsPrefix+"/events" {
		b = &d.globalEvents
	}
	allowed := b.allow(now, 1, limit)
	d.mu.Unlock()
	if !allowed {
		w.Header().Set("Retry-After", "60")
		d.write(w, 429, diagError("rate_limited"))
		return
	}
	var body []byte
	if r.Method == "POST" {
		limit := int64(4096)
		if path == diagnosticsPrefix+"/events" {
			limit = 16384
		}
		if len(r.TransferEncoding) != 0 || r.Header.Get("Transfer-Encoding") != "" || r.Header.Get("Expect") != "" {
			d.write(w, 400, diagError("invalid_framing"))
			return
		}
		if strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0]) != "application/json" {
			d.write(w, 415, diagError("invalid_content_type"))
			return
		}
		if r.ContentLength > limit {
			d.write(w, 413, diagError("body_limit"))
			return
		}
		var e error
		body, e = io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
		if e != nil {
			d.write(w, 413, diagError("body_limit"))
			return
		}
		if !diagStrictJSON(body) {
			d.write(w, 400, diagError("invalid_json"))
			return
		}
	} else if r.ContentLength > 0 || len(r.TransferEncoding) > 0 {
		d.write(w, 400, diagError("unexpected_body"))
		return
	}
	now = s.now()
	d.mu.Lock()
	d.expire(now)
	status, value := d.handle(s, r, path, body, now)
	outLimit := 8192
	if r.Method == "GET" && strings.HasSuffix(path, "/events") {
		outLimit = 16384
	}
	status, out := d.encode(status, value, outLimit)
	d.mu.Unlock()
	if status == 429 {
		w.Header().Set("Retry-After", "60")
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(out)
}

// Reject duplicate keys at every depth before decoding a typed request.
func diagStrictJSON(b []byte) bool {
	if !utf8.Valid(b) {
		return false
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	var walk func(int) bool
	walk = func(depth int) bool {
		if depth > 8 {
			return false
		}
		t, e := dec.Token()
		if e != nil {
			return false
		}
		v, ok := t.(json.Delim)
		if !ok {
			return true
		}
		if v == '{' {
			seen := map[string]bool{}
			for dec.More() {
				k, e := dec.Token()
				key, ok := k.(string)
				if e != nil || !ok || seen[key] {
					return false
				}
				seen[key] = true
				if !walk(depth + 1) {
					return false
				}
			}
			t, e = dec.Token()
			return e == nil && t == json.Delim('}')
		}
		if v == '[' {
			for dec.More() {
				if !walk(depth + 1) {
					return false
				}
			}
			t, e = dec.Token()
			return e == nil && t == json.Delim(']')
		}
		return false
	}
	if !walk(0) {
		return false
	}
	_, e := dec.Token()
	return e == io.EOF
}
func diagDecode(b []byte, v any, required []string, optional ...string) bool {
	m, e := decodeObject(b)
	if e != nil {
		return false
	}
	allowed := map[string]bool{}
	for _, k := range required {
		if m[k] == nil || string(m[k]) == "null" {
			return false
		}
		allowed[k] = true
	}
	for _, k := range optional {
		allowed[k] = true
	}
	for k, val := range m {
		if !allowed[k] || string(val) == "null" {
			return false
		}
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	return dec.Decode(v) == nil
}
func diagBearer(r *http.Request) ([32]byte, bool) {
	values := r.Header.Values("Authorization")
	if len(values) != 1 || !strings.HasPrefix(values[0], "Bearer ") {
		return [32]byte{}, false
	}
	value := strings.TrimPrefix(values[0], "Bearer ")
	if len(value) < 32 || len(value) > 256 || strings.ContainsAny(value, " ,\t\r\n") {
		return [32]byte{}, false
	}
	return sha256.Sum256([]byte(value)), true
}
func (d *diagnosticsState) operator(hash [32]byte) *diagOperator {
	for _, o := range d.operators {
		if subtle.ConstantTimeCompare(o.digest[:], hash[:]) == 1 {
			return o
		}
	}
	return nil
}
func (d *diagnosticsState) runtime(hash [32]byte) *diagRuntime {
	for _, rt := range d.runtimes {
		if subtle.ConstantTimeCompare(rt.digest[:], hash[:]) == 1 {
			return rt
		}
	}
	return nil
}
func diagCredential(r io.Reader) (string, [32]byte, error) {
	b := make([]byte, 32)
	if _, e := io.ReadFull(r, b); e != nil {
		return "", [32]byte{}, e
	}
	v := base64.RawURLEncoding.EncodeToString(b)
	return v, sha256.Sum256([]byte(v)), nil
}
