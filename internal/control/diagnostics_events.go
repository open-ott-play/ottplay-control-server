package control

import (
	"encoding/json"
	"math"
	"net/http"
	"strconv"
	"time"
)

type diagnosticEventWire struct {
	Elapsed float64        `json:"elapsed_ms"`
	Kind    string         `json:"kind"`
	Code    string         `json:"code"`
	Metrics map[string]any `json:"metrics"`
}

// Telemetry never accepts arbitrary strings, exception text, URLs or log payloads.
func diagEventJSON(raw json.RawMessage) (json.RawMessage, bool) {
	if len(raw) > 1024 {
		return nil, false
	}
	var event diagnosticEventWire
	if !diagDecode(raw, &event, []string{"elapsed_ms", "kind", "code"}, "metrics") || math.IsNaN(event.Elapsed) || math.IsInf(event.Elapsed, 0) || event.Elapsed < 0 || event.Elapsed > 600000 || len(event.Metrics) > 24 {
		return nil, false
	}
	switch event.Kind {
	case "lifecycle", "playback", "network", "input", "epg":
	default:
		return nil, false
	}
	switch event.Code {
	case "sample", "start", "stop", "waiting", "playing", "stalled", "ended", "error", "ready":
	default:
		return nil, false
	}
	for key, value := range event.Metrics {
		switch key {
		case "paused", "ended", "available", "enabled":
			if _, ok := value.(bool); !ok {
				return nil, false
			}
		case "errors", "dropped", "recoveries", "stalls", "waiting", "bufferAhead", "currentTime", "droppedFrames", "errorCode", "networkState", "readyState", "height", "width", "duration", "httpStatus", "latencyMs", "loadedBytes", "inputEvents", "inputListeners", "epgEntries", "epgPending", "epgErrors":
			n, ok := value.(float64)
			if !ok || math.IsNaN(n) || math.IsInf(n, 0) || n < 0 || n > diagSafeInteger {
				return nil, false
			}
		default:
			return nil, false
		}
	}
	if event.Metrics == nil {
		event.Metrics = map[string]any{}
	}
	canonical, err := json.Marshal(event)
	return canonical, err == nil && len(canonical) <= 1024
}

func (d *diagnosticsState) receiveEvents(rt *diagRuntime, body []byte, now time.Time) (int, map[string]any) {
	var v struct {
		Runtime string            `json:"runtime_id"`
		Session string            `json:"session_id"`
		First   uint64            `json:"first_seq"`
		Events  []json.RawMessage `json:"events"`
	}
	if !diagDecode(body, &v, []string{"runtime_id", "session_id", "first_seq", "events"}) || !commandID.MatchString(v.Session) || v.First == 0 || v.First > diagSafeInteger || len(v.Events) == 0 || len(v.Events) > 32 || uint64(len(v.Events)-1) > diagSafeInteger-v.First {
		return 400, diagError("invalid_events")
	}
	if v.Runtime != rt.id {
		return 403, diagError("runtime_mismatch")
	}
	ss := d.sessions[v.Session]
	if ss == nil || ss.runtime != rt.id {
		return 404, diagError("not_found")
	}
	if ss.state != "active" || rt.active != ss.id || !rt.consent || rt.consentEpoch != ss.consentEpoch || !now.Before(ss.deadline) {
		return 409, diagError("session_inactive")
	}
	total := 0
	for i, raw := range v.Events {
		event, ok := diagEventJSON(raw)
		if !ok {
			return 400, diagError("invalid_event")
		}
		v.Events[i] = event
		total += len(event)
	}
	fp := diagFingerprint("events", v)
	if ss.lastBatch && v.First == ss.lastFirst && fp == ss.lastHash {
		return 200, map[string]any{"accepted_through_seq": ss.accepted, "dropped_total": ss.dropped}
	}
	if v.First <= ss.accepted {
		return 409, diagError("sequence_conflict")
	}
	if total > d.cfg.EventBytesPerRuntime || total > d.cfg.EventBytesTotal {
		return 413, diagError("event_storage_limit")
	}
	// Validate the entire batch before evicting or changing any sequence state.
	for d.runtimeEventBytes(rt.id)+total > d.cfg.EventBytesPerRuntime {
		d.dropOldest(rt.id)
	}
	for d.eventBytes+total > d.cfg.EventBytesTotal {
		d.dropOldest("")
	}
	ss.dropped += v.First - (ss.accepted + 1)
	if len(ss.events) == 0 {
		ss.truncated = v.First
	}
	for i, event := range v.Events {
		ss.events = append(ss.events, diagEvent{v.First + uint64(i), event, now})
		ss.eventBytes += len(event)
		d.eventBytes += len(event)
	}
	ss.accepted = v.First + uint64(len(v.Events)) - 1
	ss.lastFirst = v.First
	ss.lastHash = fp
	ss.lastBatch = true
	return 200, map[string]any{"accepted_through_seq": ss.accepted, "dropped_total": ss.dropped}
}

func (d *diagnosticsState) runtimeEventBytes(runtime string) int {
	n := 0
	for _, ss := range d.sessions {
		if ss.runtime == runtime {
			n += ss.eventBytes
		}
	}
	return n
}
func (d *diagnosticsState) dropEvent(ss *diagSession) {
	event := ss.events[0]
	ss.eventBytes -= len(event.data)
	d.eventBytes -= len(event.data)
	ss.dropped++
	// This cursor remains a safe JSON integer even at the final allowed sequence.
	ss.truncated = event.seq
	if event.seq < diagSafeInteger {
		ss.truncated++
	}
	ss.events[0] = diagEvent{}
	ss.events = ss.events[1:]
	if len(ss.events) == 0 {
		ss.events = nil
	}
}
func (d *diagnosticsState) dropOldest(runtime string) {
	var oldest *diagSession
	for _, ss := range d.sessions {
		if (runtime == "" || ss.runtime == runtime) && len(ss.events) > 0 && (oldest == nil || ss.events[0].at.Before(oldest.events[0].at) || (ss.events[0].at.Equal(oldest.events[0].at) && ss.id < oldest.id)) {
			oldest = ss
		}
	}
	if oldest != nil {
		d.dropEvent(oldest)
	}
}
func (d *diagnosticsState) eventPage(ss *diagSession, r *http.Request) (int, map[string]any) {
	q, _ := query(r, "after_seq", "limit")
	after := uint64(0)
	limit := uint64(32)
	if value, ok := q["after_seq"]; ok {
		n, e := strconv.ParseUint(value[0], 10, 64)
		if e != nil || n > diagSafeInteger {
			return 400, diagError("invalid_query")
		}
		after = n
	}
	if value, ok := q["limit"]; ok {
		n, e := strconv.ParseUint(value[0], 10, 64)
		if e != nil || n == 0 || n > 32 {
			return 400, diagError("invalid_query")
		}
		limit = n
	}
	rows := make([]map[string]any, 0)
	next := after
	size := 0
	for _, event := range ss.events {
		if event.seq <= after {
			continue
		}
		// Leave ample room for object wrappers, cursors and the response envelope.
		if uint64(len(rows)) >= limit || size+len(event.data) > 14000 {
			break
		}
		rows = append(rows, map[string]any{"seq": event.seq, "event": event.data})
		size += len(event.data)
		next = event.seq
	}
	return 200, map[string]any{"events": rows, "next_seq": next, "truncated_before_seq": ss.truncated, "dropped_total": ss.dropped}
}
