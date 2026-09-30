package control

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"time"
)

const maxResponseBytes = 2 * 1024 * 1024
const maxResultBytes = 16 * 1024 * 1024

type requestResult struct {
	data    json.RawMessage
	expires time.Time
}

// Requests are an opt-in extension to the v1 command wire contract. Legacy
// clients ignore them, and the CLI times out rather than claiming execution.
func (s *Server) requests(w http.ResponseWriter, r *http.Request, d *device, now time.Time) {
	q, ok := query(r, "device_id", "id")
	if !ok || (r.Method == "POST" && q.Has("id")) {
		failure(w, 400, "unsupported query parameters")
		return
	}
	if d == nil {
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
	if r.URL.Path == "/api/responses" || r.URL.Path == "/api/responses/" {
		s.receiveResult(w, r, d)
		return
	}
	if r.Method == "GET" {
		if !commandID.MatchString(q.Get("id")) {
			failure(w, 400, "a request id is required")
			return
		}
		s.mu.Lock()
		s.expire(now)
		result, found := d.results[q.Get("id")]
		pending := false
		for _, e := range d.queue {
			if e.rpc && e.id == q.Get("id") {
				pending = true
			}
		}
		s.mu.Unlock()
		if found {
			// The envelope was already validated and bounded on receipt. Encoding
			// it again expands HTML characters and can exceed the same wire limit.
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(result.data)
		} else if pending {
			reply(w, 202, map[string]string{"status": "pending"})
		} else {
			failure(w, 404, "request expired or not found")
		}
		return
	}
	b, ok := readBody(w, r)
	if !ok {
		return
	}
	m, err := decodeObject(b)
	var action string
	if err != nil || len(m) != 2 || json.Unmarshal(m["action"], &action) != nil {
		failure(w, 400, "action and params are required")
		return
	}
	params, err := decodeObject(m["params"])
	if err != nil {
		failure(w, 400, "params must be an object")
		return
	}
	switch action {
	case "status", "providers":
		ok = len(params) == 0
	case "play", "provider":
		var value string
		ok = len(params) == 1 && json.Unmarshal(params["query"], &value) == nil && value != "" && len(value) <= 1024
	case "vportal", "vportal_search":
		_, validQuery := textValue(params["query"], 1024)
		ok = len(params) == 1 && validQuery
	case "channels", "programs":
		ok = len(params) == 0
		if len(params) == 1 {
			var search string
			ok = json.Unmarshal(params["search"], &search) == nil && len(search) <= 1024
		}
	case "command":
		_, err = validateCommand(m["params"])
		ok = err == nil
	case "provider_settings":
		// Provider-specific validation and parental/distribution policy live in
		// the player. No credentials are returned by status or provider listing.
		var provider string
		_, fieldsErr := decodeObject(params["settings"])
		ok = len(params) == 2 && json.Unmarshal(params["provider"], &provider) == nil && fieldsErr == nil && (provider == "m3u" || provider == "xtream" || provider == "stalker" || provider == "ottclub")
	default:
		ok = false
	}
	if !ok {
		failure(w, 400, "invalid request action or parameters")
		return
	}
	var random [16]byte
	if _, err = rand.Read(random[:]); err != nil {
		failure(w, 503, "cannot create request identifier")
		return
	}
	id := hex.EncodeToString(random[:])
	now = s.now()
	expires := now.Add(s.ttl)
	m["id"], _ = json.Marshal(id)
	m["expires_at"], _ = json.Marshal(float64(expires.UnixNano()) / 1e9)
	data, _ := json.Marshal(m)
	s.mu.Lock()
	s.expire(now)
	if len(d.queue) >= s.maxPending || s.bytes+len(data) > MaxQueueBytes || len(d.results) >= 50 {
		s.mu.Unlock()
		failure(w, 429, "request queue is full")
		return
	}
	d.queue = append(d.queue, entry{id: id, data: data, expires: expires, rpc: true})
	s.bytes += len(data)
	s.mu.Unlock()
	reply(w, 202, map[string]string{"status": "queued", "id": id})
}

func (s *Server) receiveResult(w http.ResponseWriter, r *http.Request, d *device) {
	b, ok := readLimitedBody(w, r, maxResponseBytes)
	if !ok {
		return
	}
	m, err := decodeObject(b)
	var id, status string
	if err != nil || len(m) != 3 || json.Unmarshal(m["id"], &id) != nil || !commandID.MatchString(id) || json.Unmarshal(m["status"], &status) != nil || (status != "ok" && status != "rejected" && status != "unsupported") || m["data"] == nil {
		failure(w, 400, "invalid result envelope")
		return
	}
	now := s.now()
	s.mu.Lock()
	s.expire(now)
	if _, exists := d.results[id]; exists {
		s.mu.Unlock()
		reply(w, 200, map[string]string{"status": "ok"})
		return
	}
	for i, e := range d.queue {
		if !e.rpc || e.id != id {
			continue
		}
		if s.resultBytes+len(b) > maxResultBytes || len(d.results) >= 50 {
			s.mu.Unlock()
			failure(w, 429, "result storage is full")
			return
		}
		if d.results == nil {
			d.results = make(map[string]requestResult)
		}
		d.results[id] = requestResult{data: b, expires: now.Add(60 * time.Second)}
		s.resultBytes += len(b)
		s.bytes -= len(e.data)
		copy(d.queue[i:], d.queue[i+1:])
		d.queue[len(d.queue)-1] = entry{}
		d.queue = d.queue[:len(d.queue)-1]
		d.lastSeen = now
		s.mu.Unlock()
		reply(w, 200, map[string]string{"status": "ok"})
		return
	}
	s.mu.Unlock()
	failure(w, 404, "request expired or not found")
}
