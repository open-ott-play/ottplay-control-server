// Package control implements an authenticated, ephemeral per-device command queue.
package control

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const MaxBodyBytes = 16 * 1024
const MaxQueueBytes = 2 * 1024 * 1024

type entry struct {
	id      string
	data    json.RawMessage
	expires time.Time
}
type bucket struct {
	start time.Time
	count int
}

func (b *bucket) allow(now time.Time, limit int) bool {
	if b.start.IsZero() || now.Sub(b.start) >= time.Minute {
		b.start, b.count = now, 0
	}
	if b.count >= limit {
		return false
	}
	b.count++
	return true
}

type device struct {
	id       string
	token    [32]byte
	queue    []entry
	lastSeen time.Time
	rate     bucket
}

type Server struct {
	mu                    sync.Mutex
	admin                 [32]byte
	devices               []*device
	origins               map[string]bool
	allowNull             bool
	ttl                   time.Duration
	maxPending, bytes     int
	lastTS                float64
	globalRate, adminRate bucket
	now                   func() time.Time
}

func New(c config.Config) (*Server, error) {
	c.Defaults()
	if err := c.Validate(); err != nil {
		return nil, err
	}
	s := &Server{admin: sha256.Sum256([]byte(c.AdminToken)), origins: make(map[string]bool), allowNull: c.AllowNullOrigin, ttl: time.Duration(c.CommandTTLSeconds) * time.Second, maxPending: c.MaxPendingPerDevice, now: time.Now}
	for _, origin := range c.AllowedOrigins {
		s.origins[origin] = true
	}
	for _, d := range c.Devices {
		s.devices = append(s.devices, &device{id: d.ID, token: sha256.Sum256([]byte(d.Token))})
	}
	return s, nil
}

func reply(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func failure(w http.ResponseWriter, status int, message string) {
	reply(w, status, map[string]string{"error": message})
}

func (s *Server) cors(w http.ResponseWriter, r *http.Request, methods string, deviceRoute bool) bool {
	w.Header().Add("Vary", "Origin")
	origins := r.Header.Values("Origin")
	origin := r.Header.Get("Origin")
	if len(origins) > 1 || (len(origins) == 1 && (!s.origins[origin] && !(s.allowNull && deviceRoute && origin == "null"))) {
		failure(w, 403, "origin is not allowed")
		return false
	}
	if origin != "" {
		w.Header().Set("Access-Control-Allow-Origin", origin)
	}
	if r.Method != http.MethodOptions {
		return true
	}
	w.Header().Add("Vary", "Access-Control-Request-Method")
	w.Header().Add("Vary", "Access-Control-Request-Headers")
	wanted := r.Header.Get("Access-Control-Request-Method")
	if origin == "" || wanted == "" || !strings.Contains(","+methods+",", ","+wanted+",") {
		failure(w, 403, "preflight is not allowed")
		return false
	}
	for _, h := range strings.Split(r.Header.Get("Access-Control-Request-Headers"), ",") {
		h = strings.ToLower(strings.TrimSpace(h))
		if h != "" && h != "authorization" && h != "content-type" {
			failure(w, 403, "preflight header is not allowed")
			return false
		}
	}
	w.Header().Set("Access-Control-Allow-Methods", strings.ReplaceAll(methods, ",", ", "))
	w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
	w.Header().Set("Access-Control-Max-Age", "600")
	w.WriteHeader(http.StatusNoContent)
	return false
}

func (s *Server) authenticate(r *http.Request) (admin bool, d *device) {
	values := r.Header.Values("Authorization")
	if len(values) != 1 || !strings.HasPrefix(values[0], "Bearer ") {
		return false, nil
	}
	token := strings.TrimPrefix(values[0], "Bearer ")
	if len(token) < 32 || len(token) > 256 || strings.ContainsAny(token, " ,\t\r\n") {
		return false, nil
	}
	hash := sha256.Sum256([]byte(token))
	admin = subtle.ConstantTimeCompare(hash[:], s.admin[:]) == 1
	for _, candidate := range s.devices {
		if subtle.ConstantTimeCompare(hash[:], candidate.token[:]) == 1 {
			d = candidate
		}
	}
	return
}

func query(r *http.Request, allowed ...string) (url.Values, bool) {
	q, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return nil, false
	}
	for key, values := range q {
		ok := false
		for _, a := range allowed {
			if key == a {
				ok = true
			}
		}
		if !ok || len(values) != 1 || values[0] == "" {
			return nil, false
		}
	}
	return q, true
}

func readBody(w http.ResponseWriter, r *http.Request) ([]byte, bool) {
	if len(r.TransferEncoding) != 0 || r.Header.Get("Transfer-Encoding") != "" || r.Header.Get("Expect") != "" {
		failure(w, 400, "unsupported request framing")
		return nil, false
	}
	if r.ContentLength > MaxBodyBytes {
		failure(w, 413, "request body is too large")
		return nil, false
	}
	contentType := strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0])
	if contentType != "application/json" {
		failure(w, 415, "Content-Type must be application/json")
		return nil, false
	}
	b, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBodyBytes))
	if err != nil {
		failure(w, 413, "cannot read bounded request body")
		return nil, false
	}
	return b, true
}

func (s *Server) expire(now time.Time) {
	for _, d := range s.devices {
		kept := d.queue[:0]
		for _, e := range d.queue {
			if now.Before(e.expires) {
				kept = append(kept, e)
			} else {
				s.bytes -= len(e.data)
			}
		}
		clear(d.queue[len(kept):])
		d.queue = kept
	}
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	path := strings.TrimRight(r.URL.Path, "/")
	methods := ""
	switch path {
	case "/healthz", "/readyz", "/api/devices", "/webhook/poll":
		methods = "GET"
	case "/api/webhook/commands":
		methods = "GET,POST"
	case "/webhook/notify", "/api/webhook/commands/ack":
		methods = "POST"
	default:
		failure(w, 404, "not found")
		return
	}
	intendedMethod := r.Method
	if intendedMethod == http.MethodOptions {
		intendedMethod = r.Header.Get("Access-Control-Request-Method")
	}
	deviceRoute := path == "/webhook/poll" || path == "/api/webhook/commands/ack" || (path == "/api/webhook/commands" && intendedMethod == http.MethodGet)
	if !s.cors(w, r, methods, deviceRoute) {
		return
	}
	if !strings.Contains(","+methods+",", ","+r.Method+",") {
		w.Header().Set("Allow", strings.ReplaceAll(methods, ",", ", ")+", OPTIONS")
		failure(w, 405, "method is not allowed")
		return
	}
	if path == "/healthz" || path == "/readyz" {
		reply(w, 200, map[string]string{"status": "ok"})
		return
	}
	now := s.now()
	s.mu.Lock()
	// Each configured player can use its full allowance without exhausting a
	// smaller shared budget; unauthenticated traffic still has a fixed bound.
	if !s.globalRate.allow(now, len(s.devices)*120+240) {
		s.mu.Unlock()
		w.Header().Set("Retry-After", "60")
		failure(w, 429, "request rate exceeded")
		return
	}
	s.mu.Unlock()
	admin, d := s.authenticate(r)
	if !admin && d == nil {
		w.Header().Set("WWW-Authenticate", `Bearer realm="ottplay-control"`)
		failure(w, 401, "valid Bearer credentials are required")
		return
	}
	requiresAdmin := path == "/api/devices" || (r.Method == "POST" && path != "/api/webhook/commands/ack")
	if requiresAdmin != admin {
		failure(w, 403, "credential role is not allowed")
		return
	}
	s.mu.Lock()
	allowed := false
	if admin {
		allowed = s.adminRate.allow(now, 240)
	} else {
		allowed = d.rate.allow(now, 120)
	}
	s.mu.Unlock()
	if !allowed {
		w.Header().Set("Retry-After", "60")
		failure(w, 429, "credential rate exceeded")
		return
	}
	if path == "/api/devices" {
		s.list(w, r, now)
		return
	}
	q, ok := query(r, "device_id", "delivery")
	if !ok || (r.Method != "GET" && q.Has("delivery")) {
		failure(w, 400, "unsupported query parameters")
		return
	}
	if admin {
		for _, candidate := range s.devices {
			if candidate.id == q.Get("device_id") {
				d = candidate
			}
		}
		if d == nil {
			failure(w, 400, "a configured device_id is required")
			return
		}
	} else if q.Has("device_id") && q.Get("device_id") != d.id {
		failure(w, 403, "device_id does not match credentials")
		return
	}
	if path == "/api/webhook/commands/ack" {
		s.ack(w, r, d, now)
		return
	}
	if r.Method == "GET" {
		if q.Has("delivery") && q.Get("delivery") != "ack" {
			failure(w, 400, "unsupported delivery mode")
			return
		}
		s.poll(w, d, now, q.Get("delivery") == "ack")
		return
	}
	s.enqueue(w, r, d, now)
}

func (s *Server) enqueue(w http.ResponseWriter, r *http.Request, d *device, now time.Time) {
	b, ok := readBody(w, r)
	if !ok {
		return
	}
	m, err := validateCommand(b)
	if err != nil {
		failure(w, 400, err.Error())
		return
	}
	var random [16]byte
	if _, err = rand.Read(random[:]); err != nil {
		failure(w, 503, "cannot create command identifier")
		return
	}
	id := hex.EncodeToString(random[:])
	// Retention starts after the bounded request body has been received and checked.
	now = s.now()
	s.mu.Lock()
	s.expire(now)
	ts := float64(now.UnixNano()) / 1e9
	if ts <= s.lastTS {
		ts = s.lastTS + 0.001
	}
	m["id"], _ = json.Marshal(id)
	m["ts"], _ = json.Marshal(ts)
	expires := now.Add(s.ttl)
	m["expires_at"], _ = json.Marshal(float64(expires.UnixNano()) / 1e9)
	data, _ := json.Marshal(m)
	if len(d.queue) >= s.maxPending || s.bytes+len(data) > MaxQueueBytes {
		s.mu.Unlock()
		failure(w, 429, "command queue is full")
		return
	}
	s.lastTS = ts
	d.queue = append(d.queue, entry{id: id, data: data, expires: expires})
	s.bytes += len(data)
	s.mu.Unlock()
	reply(w, 202, map[string]any{"status": "queued", "id": id, "ts": ts, "expires_at": float64(expires.UnixNano()) / 1e9})
}

func (s *Server) poll(w http.ResponseWriter, d *device, now time.Time, ack bool) {
	s.mu.Lock()
	s.expire(now)
	d.lastSeen = now
	commands := make([]json.RawMessage, 0, len(d.queue))
	for _, e := range d.queue {
		commands = append(commands, e.data)
		if !ack {
			s.bytes -= len(e.data)
		}
	}
	if !ack {
		clear(d.queue)
		d.queue = nil
	}
	s.mu.Unlock()
	if ack {
		reply(w, 200, map[string]any{"commands": commands, "server_time": float64(now.UnixNano()) / 1e9})
	} else {
		reply(w, 200, commands)
	}
}

var commandID = regexp.MustCompile(`^[0-9a-f]{32}$`)

func (s *Server) ack(w http.ResponseWriter, r *http.Request, d *device, now time.Time) {
	b, ok := readBody(w, r)
	if !ok {
		return
	}
	m, err := decodeObject(b)
	var ids []string
	if err != nil || len(m) != 1 || string(m["ids"]) == "null" || json.Unmarshal(m["ids"], &ids) != nil || len(ids) > 50 {
		failure(w, 400, "ids must be an array of at most 50 command ids")
		return
	}
	set := map[string]bool{}
	for _, id := range ids {
		if !commandID.MatchString(id) {
			failure(w, 400, "invalid command id")
			return
		}
		set[id] = true
	}
	s.mu.Lock()
	s.expire(now)
	d.lastSeen = now
	kept := d.queue[:0]
	removed := 0
	for _, e := range d.queue {
		if set[e.id] {
			s.bytes -= len(e.data)
			removed++
		} else {
			kept = append(kept, e)
		}
	}
	clear(d.queue[len(kept):])
	d.queue = kept
	s.mu.Unlock()
	reply(w, 200, map[string]any{"status": "ok", "acknowledged": removed})
}

func (s *Server) list(w http.ResponseWriter, r *http.Request, now time.Time) {
	if _, ok := query(r); !ok {
		failure(w, 400, "unsupported query parameters")
		return
	}
	s.mu.Lock()
	s.expire(now)
	devices := make([]map[string]any, 0, len(s.devices))
	for _, d := range s.devices {
		var seen any
		if !d.lastSeen.IsZero() {
			seen = d.lastSeen.UTC().Format(time.RFC3339Nano)
		}
		devices = append(devices, map[string]any{"id": d.id, "pending": len(d.queue), "last_seen": seen})
	}
	s.mu.Unlock()
	reply(w, 200, map[string]any{"devices": devices})
}
