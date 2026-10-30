package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const playerStatePath = "/api/player-state"
const playerStateBodyMax = 4096
const playerStateFresh = 45 * time.Second
const playerStateRetention = 24 * time.Hour
const playerStateHistoryMax = 64
const playerStateRuntimeMax = 4
const playerStateFenceMax = 64

var playerStateEpochPattern = regexp.MustCompile(`^[a-f0-9]{32}$`)
var playerStateMediaID = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`)

type statePlayer struct {
	Version  string `json:"version"`
	Platform string `json:"platform"`
	Standby  bool   `json:"standby"`
}
type statePlayback struct {
	State         string   `json:"state"`
	Kind          string   `json:"kind"`
	Title         *string  `json:"title"`
	Provider      *string  `json:"provider"`
	MediaID       *string  `json:"media_id"`
	ChannelNumber *int64   `json:"channel_number"`
	Position      *float64 `json:"position"`
	Duration      *float64 `json:"duration"`
	Volume        *float64 `json:"volume"`
}
type stateReport struct {
	Version  int           `json:"version"`
	Epoch    string        `json:"controller_epoch"`
	Runtime  string        `json:"runtime"`
	Sequence int64         `json:"sequence"`
	Player   statePlayer   `json:"player"`
	Playback statePlayback `json:"playback"`
}
type stateSnapshot struct {
	Runtime    string        `json:"runtime"`
	Sequence   int64         `json:"sequence"`
	ReceivedAt time.Time     `json:"received_at"`
	Player     statePlayer   `json:"player"`
	Playback   statePlayback `json:"playback"`
	Restored   bool          `json:"-"`
}
type stateEvent struct {
	Event      string        `json:"event"`
	ReceivedAt time.Time     `json:"received_at"`
	Runtime    string        `json:"runtime"`
	Sequence   int64         `json:"sequence"`
	Player     statePlayer   `json:"player"`
	Playback   statePlayback `json:"playback"`
}
type stateFence struct {
	Epoch    string    `json:"epoch"`
	Sequence int64     `json:"sequence"`
	Digest   string    `json:"digest"`
	Seen     time.Time `json:"seen"`
	Retired  bool      `json:"retired"`
}
type stateDevice struct {
	Authority string                `json:"authority"`
	Runtimes  []stateSnapshot       `json:"runtimes"`
	History   []stateEvent          `json:"history"`
	Fences    map[string]stateFence `json:"fences"`
}
type stateRow struct {
	Runtime    string        `json:"runtime"`
	Sequence   int64         `json:"sequence"`
	ReceivedAt time.Time     `json:"received_at"`
	AgeMS      int64         `json:"age_ms"`
	Stale      bool          `json:"stale"`
	Restored   bool          `json:"restored"`
	Player     statePlayer   `json:"player"`
	Playback   statePlayback `json:"playback"`
}

func stateText(s string, max int) bool {
	if len(s) == 0 || len(s) > max || !utf8.ValidString(s) || strings.TrimSpace(s) != s {
		return false
	}
	for _, r := range s {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}
func stateDisplay(s *string, max int) bool {
	if s == nil {
		return true
	}
	if !stateText(*s, max) {
		return false
	}
	var clean strings.Builder
	for _, r := range *s {
		// Graphic/Z are precisely the non-C categories, including assigned
		// separators; Cn is not present in Go's category-C table.
		if unicode.IsGraphic(r) || unicode.Is(unicode.Z, r) {
			clean.WriteRune(r)
		}
	}
	v := strings.TrimSpace(clean.String())
	if strings.Contains(v, "://") || strings.HasPrefix(v, "/") || strings.HasPrefix(v, `\`) || strings.HasPrefix(v, "~/") || strings.HasPrefix(v, `~\`) {
		return false
	}
	return !(len(v) >= 3 && ((v[0] >= 'a' && v[0] <= 'z') || (v[0] >= 'A' && v[0] <= 'Z')) && v[1] == ':' && (v[2] == '/' || v[2] == '\\'))
}
func stateNumber(v *float64, max float64) bool {
	return v == nil || (!math.IsNaN(*v) && !math.IsInf(*v, 0) && *v >= 0 && *v <= max)
}
func validStateContent(p statePlayer, b statePlayback) bool {
	if !stateDisplay(&p.Version, 64) || !stateDisplay(&p.Platform, 32) || !stateDisplay(b.Title, 256) || !stateDisplay(b.Provider, 64) {
		return false
	}
	if b.MediaID != nil && !playerStateMediaID.MatchString(*b.MediaID) {
		return false
	}
	if b.ChannelNumber != nil && (*b.ChannelNumber < 1 || *b.ChannelNumber > 2147483647) {
		return false
	}
	if !stateNumber(b.Position, 9007199254740991) || !stateNumber(b.Duration, 9007199254740991) || !stateNumber(b.Volume, 100) {
		return false
	}
	switch b.State {
	case "unknown", "idle", "loading", "playing", "paused", "buffering", "ended", "error", "standby":
	default:
		return false
	}
	switch b.Kind {
	case "none", "live", "archive", "vod", "unknown":
	default:
		return false
	}
	if p.Standby != (b.State == "standby") {
		return false
	}
	if b.State == "idle" || b.State == "ended" || b.State == "standby" {
		return b.Title == nil && b.MediaID == nil && b.ChannelNumber == nil
	}
	return true
}
func stateObject(raw []byte, names ...string) (map[string]json.RawMessage, bool) {
	m, err := decodeObject(raw)
	if err != nil || len(m) != len(names) {
		return nil, false
	}
	for _, name := range names {
		if _, ok := m[name]; !ok {
			return nil, false
		}
	}
	return m, true
}
func decodeStateReport(raw []byte) (stateReport, bool) {
	var v stateReport
	if len(raw) > playerStateBodyMax || !utf8.Valid(raw) || !diagStrictJSON(raw) {
		return v, false
	}
	m, ok := stateObject(raw, "version", "controller_epoch", "runtime", "sequence", "player", "playback")
	if !ok {
		return v, false
	}
	p, ok := stateObject(m["player"], "version", "platform", "standby")
	if !ok || string(bytes.TrimSpace(p["standby"])) == "null" {
		return v, false
	}
	if _, ok = stateObject(m["playback"], "state", "kind", "title", "provider", "media_id", "channel_number", "position", "duration", "volume"); !ok {
		return v, false
	}
	if json.Unmarshal(raw, &v) != nil || v.Version != 1 || !playerStateEpochPattern.MatchString(v.Epoch) || !notificationRuntime.MatchString(v.Runtime) || v.Sequence < 1 || v.Sequence > 9007199254740991 || !validStateContent(v.Player, v.Playback) {
		return v, false
	}
	return v, true
}
func (s *Server) playerStateAdvertisement() map[string]any {
	return map[string]any{"version": 1, "controller_epoch": s.playerStateEpoch, "report_path": playerStatePath, "heartbeat_ms": 15000, "stale_after_ms": 45000, "max_body_bytes": playerStateBodyMax}
}
func (s *Server) stateAuthority(d *device) string {
	generation := "static"
	if v := s.registryView.Load(); v != nil {
		generation = v.identities[d.id].Generation
	}
	sum := sha256.Sum256([]byte(d.id + "\x00" + generation + "\x00" + hex.EncodeToString(d.token[:])))
	return hex.EncodeToString(sum[:])
}
func stateExpired(t, now time.Time) bool { return !now.Before(t) && now.Sub(t) >= playerStateRetention }
func stateStale(v stateSnapshot, now time.Time) bool {
	return v.Restored || now.Before(v.ReceivedAt) || now.Sub(v.ReceivedAt) >= playerStateFresh
}
func stateSemanticEqual(a, b stateSnapshot) bool {
	a.Playback.Position = nil
	b.Playback.Position = nil
	return a.Player == b.Player && reflect.DeepEqual(a.Playback, b.Playback)
}
func cloneStateDevice(v stateDevice, now time.Time) stateDevice {
	out := stateDevice{Authority: v.Authority, Runtimes: []stateSnapshot{}, History: []stateEvent{}, Fences: map[string]stateFence{}}
	for _, r := range v.Runtimes {
		if !stateExpired(r.ReceivedAt, now) {
			out.Runtimes = append(out.Runtimes, r)
		}
	}
	for _, r := range v.History {
		if !stateExpired(r.ReceivedAt, now) {
			out.History = append(out.History, r)
		}
	}
	for id, f := range v.Fences {
		if !stateExpired(f.Seen, now) {
			out.Fences[id] = f
		}
	}
	return out
}
func (s *Server) servePlayerState(w http.ResponseWriter, r *http.Request, d *device) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method == http.MethodGet {
		s.readPlayerState(w, r)
		return
	}
	if _, ok := query(r); !ok {
		failure(w, 400, "unsupported query parameters")
		return
	}
	if strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0]) != "application/json" {
		failure(w, 415, "Content-Type must be application/json")
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, playerStateBodyMax))
	if err != nil {
		failure(w, 413, "player state body is too large")
		return
	}
	v, ok := decodeStateReport(raw)
	if !ok {
		failure(w, 400, "invalid player state")
		return
	}
	if v.Epoch != s.playerStateEpoch {
		failure(w, 409, "controller epoch changed")
		return
	}
	canonical, _ := json.Marshal(v)
	sum := sha256.Sum256(canonical)
	digest := hex.EncodeToString(sum[:])
	store := s.playerState
	if store == nil {
		failure(w, 503, "player state storage unavailable")
		return
	}
	// Report serialization is separate from command/credential admission.
	// The latter locks are deliberately released across durable I/O.
	store.writer.Lock()
	defer store.writer.Unlock()
	held := false
	lock := func() { s.registryGate.RLock(); s.mu.Lock(); held = true }
	unlock := func() {
		if held {
			s.mu.Unlock()
			s.registryGate.RUnlock()
			held = false
		}
	}
	lock()
	defer unlock()
	now := s.now().UTC()
	if !s.currentStateDevice(d) {
		failure(w, 409, "device identity changed")
		return
	}
	if store.failed {
		failure(w, 503, "player state storage unavailable")
		return
	}
	authority := s.stateAuthority(d)
	old := store.devices[d.id]
	if old.Authority != authority {
		old = stateDevice{Authority: authority}
	}
	next := cloneStateDevice(old, now)
	f, known := next.Fences[v.Runtime]
	if known && f.Epoch == s.playerStateEpoch {
		if f.Retired || v.Sequence < f.Sequence || (v.Sequence == f.Sequence && digest != f.Digest) {
			failure(w, 409, "player state sequence conflict")
			return
		}
		if v.Sequence == f.Sequence {
			reply(w, 200, map[string]any{"version": 1, "controller_epoch": s.playerStateEpoch, "runtime": v.Runtime, "sequence": v.Sequence, "received_at": f.Seen, "duplicate": true, "storage": store.storage()})
			return
		}
	}
	index := -1
	for i, row := range next.Runtimes {
		if row.Runtime == v.Runtime {
			index = i
			break
		}
	}
	if !known && len(next.Fences) >= playerStateFenceMax {
		failure(w, 429, "player runtime history is full")
		return
	}
	if index < 0 && len(next.Runtimes) >= playerStateRuntimeMax {
		victim := -1
		for i, row := range next.Runtimes {
			if stateStale(row, now) && (victim < 0 || row.ReceivedAt.Before(next.Runtimes[victim].ReceivedAt)) {
				victim = i
			}
		}
		if victim < 0 {
			failure(w, 429, "player runtime capacity reached")
			return
		}
		id := next.Runtimes[victim].Runtime
		fence := next.Fences[id]
		fence.Retired = true
		next.Fences[id] = fence
		next.Runtimes = append(next.Runtimes[:victim], next.Runtimes[victim+1:]...)
	}
	row := stateSnapshot{Runtime: v.Runtime, Sequence: v.Sequence, ReceivedAt: now, Player: v.Player, Playback: v.Playback}
	event := ""
	if index < 0 {
		event = "connected"
		next.Runtimes = append(next.Runtimes, row)
	} else {
		if stateStale(next.Runtimes[index], now) {
			event = "reconnected"
		} else if !stateSemanticEqual(next.Runtimes[index], row) {
			event = "changed"
		}
		next.Runtimes[index] = row
	}
	next.Fences[v.Runtime] = stateFence{Epoch: s.playerStateEpoch, Sequence: v.Sequence, Digest: digest, Seen: now}
	if event != "" {
		next.History = append(next.History, stateEvent{Event: event, ReceivedAt: now, Runtime: v.Runtime, Sequence: v.Sequence, Player: v.Player, Playback: v.Playback})
		if len(next.History) > playerStateHistoryMax {
			next.History = next.History[len(next.History)-playerStateHistoryMax:]
		}
	}
	rows := make(map[string]stateDevice, len(store.devices)+1)
	for id, current := range store.devices {
		rows[id] = cloneStateDevice(current, now)
	}
	rows[d.id] = next
	unlock()
	var saveErr error
	if event != "" {
		saveErr = store.save(rows)
	} else {
		_, saveErr = playerStateBytes(rows, playerStateStoreMax)
	}
	lock()
	if saveErr != nil {
		store.failed = true
		failure(w, 503, "player state could not be retained")
		return
	}
	if !s.currentStateDevice(d) || s.stateAuthority(d) != authority {
		failure(w, 409, "device identity changed")
		return
	}
	// Another device can rotate while this write is in flight. Never publish
	// its old row back into RAM after the registry already evicted it.
	for id, data := range rows {
		keep := false
		for _, current := range s.currentDevices() {
			if current.id == id && data.Authority == s.stateAuthority(current) {
				keep = true
				break
			}
		}
		if !keep {
			delete(rows, id)
		}
	}
	store.devices = rows
	if now.After(d.lastSeen) {
		d.lastSeen = now
	}
	reply(w, 200, map[string]any{"version": 1, "controller_epoch": s.playerStateEpoch, "runtime": v.Runtime, "sequence": v.Sequence, "received_at": now, "duplicate": false, "storage": store.storage()})
}

// Caller holds registry admission and s.mu. The pointer is replaced on every
// credential/generation change; no stale authenticated handle can publish.
func (s *Server) currentStateDevice(d *device) bool {
	if snapshot := s.registryView.Load(); snapshot != nil && snapshot.unavailable {
		return false
	}
	for _, current := range s.currentDevices() {
		if current == d {
			return true
		}
	}
	return false
}
func stateReadRow(v stateSnapshot, now time.Time) stateRow {
	age := now.Sub(v.ReceivedAt).Milliseconds()
	if age < 0 {
		age = 0
	}
	return stateRow{Runtime: v.Runtime, Sequence: v.Sequence, ReceivedAt: v.ReceivedAt, AgeMS: age, Stale: stateStale(v, now), Restored: v.Restored, Player: v.Player, Playback: v.Playback}
}
func (s *Server) readPlayerState(w http.ResponseWriter, r *http.Request) {
	q, ok := query(r, "device_id", "limit")
	if !ok || !q.Has("device_id") {
		failure(w, 400, "a configured device_id is required")
		return
	}
	limit := 0
	if q.Has("limit") {
		text := q.Get("limit")
		n, err := strconv.Atoi(text)
		if err != nil || strconv.Itoa(n) != text || n < 0 || n > 64 {
			failure(w, 400, "invalid player history limit")
			return
		}
		limit = n
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	var d *device
	for _, candidate := range s.currentDevices() {
		if candidate.id == q.Get("device_id") {
			d = candidate
			break
		}
	}
	if d == nil {
		failure(w, 400, "a configured device_id is required")
		return
	}
	if !allowedCommand(r, d, "status") {
		failure(w, 403, "status scope is not allowed")
		return
	}
	store := s.playerState
	if store == nil || store.failed {
		failure(w, 503, "player state storage unavailable")
		return
	}
	now := s.now().UTC()
	data := store.devices[d.id]
	if data.Authority != s.stateAuthority(d) {
		data = stateDevice{}
	}
	rows := []stateRow{}
	for _, v := range data.Runtimes {
		if !stateExpired(v.ReceivedAt, now) {
			rows = append(rows, stateReadRow(v, now))
		}
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].ReceivedAt.Equal(rows[j].ReceivedAt) {
			return rows[i].Runtime < rows[j].Runtime
		}
		return rows[i].ReceivedAt.After(rows[j].ReceivedAt)
	})
	var current, latest *stateRow
	fresh := 0
	for i := range rows {
		if !rows[i].Stale {
			fresh++
			current = &rows[i]
		}
	}
	if fresh != 1 {
		current = nil
	}
	if len(rows) > 0 {
		latest = &rows[0]
	}
	history := []stateEvent{}
	for i := len(data.History) - 1; i >= 0; i-- {
		if !stateExpired(data.History[i].ReceivedAt, now) {
			history = append(history, data.History[i])
		}
	}
	sort.SliceStable(history, func(i, j int) bool { return history[i].ReceivedAt.After(history[j].ReceivedAt) })
	available := len(history)
	if len(history) > limit {
		history = history[:limit]
	}
	connection := "unknown"
	var seen *time.Time
	if !d.lastSeen.IsZero() {
		t := d.lastSeen.UTC()
		seen = &t
		connection = "offline"
		if !now.Before(t) && now.Sub(t) < playerStateFresh {
			connection = "online"
		}
	}
	reply(w, 200, map[string]any{"version": 1, "controller_epoch": s.playerStateEpoch, "device_id": d.id, "server_time": now, "storage": store.storage(), "stale_after_ms": 45000, "retention_seconds": 86400, "connection": map[string]any{"state": connection, "last_seen_at": seen}, "ambiguous": fresh > 1, "current": current, "latest": latest, "runtimes": rows, "history": history, "history_truncated": available > len(history)})
}
