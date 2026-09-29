package control

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base32"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"time"
)

const pairingTTL = 10 * time.Minute
const maxPairings = 64

type pairing struct {
	id, device, code string
	secret           [32]byte
	server           discoveredServer
	expires          time.Time
	approved         bool
	attempts         int
}

func (s *Server) expirePairings(now time.Time) {
	for id, p := range s.pairings {
		if !now.Before(p.expires) {
			delete(s.pairings, id)
		}
	}
}

func (s *Server) bootstrap(w http.ResponseWriter, r *http.Request, path string) {
	q, ok := query(r, "id")
	if !ok || (path != "/api/pairings" && len(q) != 0) || (r.Method == "POST" && len(q) != 0) || (r.Method == "DELETE" && !q.Has("id")) {
		failure(w, 400, "unsupported query parameters")
		return
	}
	public := path == "/api/discovery" || (path == "/api/pairings" && (r.Method == "POST" || q.Has("id")))
	admin, _ := s.authenticate(r)
	if !public && !admin {
		failure(w, 403, "administrator credentials required")
		return
	}
	s.mu.Lock()
	allowed := false
	if public {
		allowed = s.publicRate.allow(s.now(), len(s.devices)*30+60)
	} else {
		allowed = s.adminRate.allow(s.now(), 240)
	}
	s.mu.Unlock()
	if !allowed {
		w.Header().Set("Retry-After", "60")
		failure(w, 429, "pairing request rate exceeded")
		return
	}
	switch {
	case path == "/api/discovery":
		servers, err := s.discovery.resolve(r.Context(), false)
		if err != nil {
			failure(w, 503, "DNS discovery unavailable")
			return
		}
		reply(w, 200, map[string]any{"version": 1, "servers": servers, "pairing_url": s.discovery.publicURL + "/api/pairings"})
	case path == "/api/pairings/approve":
		s.approvePairing(w, r)
	case r.Method == "DELETE":
		s.cancelPairing(w, r, q.Get("id"))
	case r.Method == "POST":
		s.createPairing(w, r)
	case q.Has("id"):
		s.claimPairing(w, r, q.Get("id"))
	default:
		s.mu.Lock()
		now := s.now()
		s.expirePairings(now)
		list := []map[string]any{}
		for _, p := range s.pairings {
			if !p.approved {
				list = append(list, map[string]any{"id": p.id, "device_id": p.device, "code": p.code, "address": p.server.Address, "expires_in": int(p.expires.Sub(now).Seconds())})
			}
		}
		s.mu.Unlock()
		sort.Slice(list, func(i, j int) bool { return list[i]["id"].(string) < list[j]["id"].(string) })
		reply(w, 200, map[string]any{"pairings": list})
	}
}

func (s *Server) createPairing(w http.ResponseWriter, r *http.Request) {
	b, ok := readBody(w, r)
	if !ok {
		return
	}
	fields, err := decodeObject(b)
	var deviceID, serverID string
	if err != nil || (len(fields) != 1 && len(fields) != 2) || json.Unmarshal(fields["device_id"], &deviceID) != nil {
		failure(w, 400, "device_id and optional server_id are required")
		return
	}
	if len(fields) == 2 && (json.Unmarshal(fields["server_id"], &serverID) != nil || len(serverID) == 0 || len(serverID) > 254) {
		failure(w, 400, "invalid server_id")
		return
	}
	s.mu.Lock()
	now := s.now()
	s.expirePairings(now)
	rate, registered := s.pairRates[deviceID]
	allowed := registered && rate.allow(now, 3) && s.createRate.allow(now, 30)
	s.mu.Unlock()
	if !registered {
		failure(w, 400, "device is not registered")
		return
	}
	if !allowed {
		failure(w, 429, "pairing creation rate exceeded")
		return
	}
	servers, err := s.discovery.resolve(r.Context(), true)
	if err != nil {
		failure(w, 503, "DNS discovery unavailable")
		return
	}
	var selected discoveredServer
	if serverID == "" && len(servers) == 1 {
		selected = servers[0]
	}
	if serverID != "" {
		for _, server := range servers {
			if server.ID == serverID {
				selected = server
			}
		}
	}
	if selected.ID == "" || selected.Address != s.discovery.publicURL {
		failure(w, 409, "select the matching discovered controller")
		return
	}
	var random [53]byte
	if _, err = rand.Read(random[:]); err != nil {
		failure(w, 503, "cannot create pairing")
		return
	}
	id := hex.EncodeToString(random[:16])
	secret := base64.RawURLEncoding.EncodeToString(random[16:48])
	code := base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(random[48:])
	s.mu.Lock()
	now = s.now()
	s.expirePairings(now)
	count := 0
	for _, p := range s.pairings {
		if p.device == deviceID {
			count++
		}
	}
	if len(s.pairings) >= maxPairings || count >= 2 {
		s.mu.Unlock()
		failure(w, 429, "pairing queue is full")
		return
	}
	s.pairings[id] = &pairing{id: id, device: deviceID, code: code, secret: sha256.Sum256([]byte(secret)), server: selected, expires: now.Add(pairingTTL)}
	s.mu.Unlock()
	reply(w, 201, map[string]any{"id": id, "secret": secret, "code": code, "expires_in": 600})
}

func (s *Server) currentPairingServer(r *http.Request, expected discoveredServer) bool {
	servers, err := s.discovery.resolve(r.Context(), true)
	if err != nil || expected.Address != s.discovery.publicURL {
		return false
	}
	for _, server := range servers {
		if server == expected {
			return true
		}
	}
	return false
}

func (s *Server) approvePairing(w http.ResponseWriter, r *http.Request) {
	b, ok := readBody(w, r)
	if !ok {
		return
	}
	fields, err := decodeObject(b)
	var id, code string
	if err != nil || len(fields) != 2 || json.Unmarshal(fields["id"], &id) != nil || !commandID.MatchString(id) || json.Unmarshal(fields["code"], &code) != nil || len(code) != 8 {
		failure(w, 400, "pairing id and exact 8-character code are required")
		return
	}
	s.mu.Lock()
	s.expirePairings(s.now())
	p := s.pairings[id]
	if p == nil {
		s.mu.Unlock()
		failure(w, 404, "pairing expired or not found")
		return
	}
	if p.approved {
		s.mu.Unlock()
		failure(w, 409, "pairing already approved")
		return
	}
	if subtle.ConstantTimeCompare([]byte(p.code), []byte(code)) != 1 {
		p.attempts++
		if p.attempts >= 5 {
			delete(s.pairings, id)
		}
		s.mu.Unlock()
		failure(w, 403, "pairing code does not match")
		return
	}
	expected := p.server
	s.mu.Unlock()
	if !s.currentPairingServer(r, expected) {
		s.mu.Lock()
		if s.pairings[id] == p {
			delete(s.pairings, id)
		}
		s.mu.Unlock()
		failure(w, 409, "discovered controller changed; start pairing again")
		return
	}
	s.mu.Lock()
	s.expirePairings(s.now())
	if s.pairings[id] != p {
		s.mu.Unlock()
		failure(w, 404, "pairing expired or not found")
		return
	}
	if p.approved {
		s.mu.Unlock()
		failure(w, 409, "pairing already approved")
		return
	}
	p.approved = true
	s.mu.Unlock()
	reply(w, 200, map[string]string{"status": "approved"})
}

func (s *Server) claimPairing(w http.ResponseWriter, r *http.Request, id string) {
	values := r.Header.Values("Authorization")
	if !commandID.MatchString(id) || len(values) != 1 || !strings.HasPrefix(values[0], "Bearer ") {
		failure(w, 401, "valid pairing claim required")
		return
	}
	secret := strings.TrimPrefix(values[0], "Bearer ")
	if len(secret) != 43 {
		failure(w, 401, "valid pairing claim required")
		return
	}
	hash := sha256.Sum256([]byte(secret))
	s.mu.Lock()
	s.expirePairings(s.now())
	p := s.pairings[id]
	if p == nil || subtle.ConstantTimeCompare(hash[:], p.secret[:]) != 1 {
		s.mu.Unlock()
		failure(w, 401, "valid pairing claim required")
		return
	}
	approved, expected := p.approved, p.server
	s.mu.Unlock()
	if !approved {
		reply(w, 202, map[string]string{"status": "pending"})
		return
	}
	if !s.currentPairingServer(r, expected) {
		s.mu.Lock()
		if s.pairings[id] == p {
			delete(s.pairings, id)
		}
		s.mu.Unlock()
		failure(w, 409, "discovered controller changed; start pairing again")
		return
	}
	s.mu.Lock()
	s.expirePairings(s.now())
	if s.pairings[id] != p {
		s.mu.Unlock()
		failure(w, 401, "valid pairing claim required")
		return
	}
	result := map[string]string{"status": "approved", "device_id": p.device, "address": p.server.Address, "token": s.pairTokens[p.device]}
	s.mu.Unlock()
	reply(w, 200, result)
}

// A caller may retire only its own claim, including after successful storage.
// Cancellation never requires DNS and never accepts an administrator shortcut.
func (s *Server) cancelPairing(w http.ResponseWriter, r *http.Request, id string) {
	values := r.Header.Values("Authorization")
	if !commandID.MatchString(id) || len(values) != 1 || !strings.HasPrefix(values[0], "Bearer ") {
		failure(w, 401, "valid pairing claim required")
		return
	}
	secret := strings.TrimPrefix(values[0], "Bearer ")
	if len(secret) != 43 {
		failure(w, 401, "valid pairing claim required")
		return
	}
	hash := sha256.Sum256([]byte(secret))
	s.mu.Lock()
	s.expirePairings(s.now())
	p := s.pairings[id]
	if p == nil || subtle.ConstantTimeCompare(hash[:], p.secret[:]) != 1 {
		s.mu.Unlock()
		failure(w, 401, "valid pairing claim required")
		return
	}
	delete(s.pairings, id)
	s.mu.Unlock()
	reply(w, 200, map[string]string{"status": "cancelled"})
}
