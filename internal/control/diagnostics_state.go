package control

import (
	"crypto/sha256"
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"time"
)

func diagMS(n int) time.Duration { return time.Duration(n) * time.Millisecond }
func diagRemaining(deadline, now time.Time) int64 {
	n := deadline.Sub(now).Milliseconds()
	if n < 0 {
		return 0
	}
	return n
}
func (d *diagnosticsState) terminal(ss *diagSession, state string, now time.Time) {
	ss.state = state
	if ss.stopReserved {
		ss.stopReserved = false
		d.reservedStops--
	}
	if rt := d.runtimes[ss.runtime]; rt != nil && rt.active == ss.id {
		rt.active = ""
	}
	// Terminal retention is fixed on the first transition; duplicates never extend it.
	if ss.retainUntil.IsZero() {
		ss.retainUntil = now.Add(diagMS(d.cfg.IdempotencyRetentionMS))
	}
}
func (d *diagnosticsState) expire(now time.Time) {
	for _, rt := range d.runtimes {
		if !now.Before(rt.expires) || !d.enabled[rt.device] {
			for _, ss := range d.sessions {
				if ss.runtime == rt.id && (ss.state == "active" || ss.state == "start_pending" || ss.state == "stop_pending") {
					d.terminal(ss, "revoked", now)
				}
			}
			delete(d.runtimes, rt.id)
		}
	}
	for id, ss := range d.sessions {
		if (ss.state == "start_pending" || ss.state == "active" || ss.state == "stop_pending") && !now.Before(ss.deadline) {
			d.terminal(ss, "expired", now)
		}
		for len(ss.events) > 0 && !now.Before(ss.events[0].at.Add(diagMS(d.cfg.EventRetentionMS))) {
			d.dropEvent(ss)
		}
		if !ss.retainUntil.IsZero() && !now.Before(ss.retainUntil) {
			d.eventBytes -= ss.eventBytes
			delete(d.sessions, id)
		}
	}
	for key, v := range d.idempotency {
		if !now.Before(v.until) {
			delete(d.idempotency, key)
		}
	}
}
func (d *diagnosticsState) handle(s *Server, r *http.Request, path string, body []byte, now time.Time) (int, map[string]any) {
	hash, ok := diagBearer(r)
	if !ok {
		return 401, diagError("invalid_credentials")
	}
	admin, dev := s.authenticate(r)
	rt := d.runtime(hash)
	op := d.operator(hash)
	runtimePath := path == diagnosticsPrefix+"/poll" || path == diagnosticsPrefix+"/results" || path == diagnosticsPrefix+"/events"
	if path == diagnosticsPrefix+"/runtimes" && r.Method == "POST" {
		if dev == nil || admin {
			return 403, diagError("credential_role_denied")
		}
		if !d.enabled[dev.id] {
			return 403, diagError("diagnostics_disabled")
		}
		if _, ok := query(r); !ok {
			return 400, diagError("invalid_query")
		}
		if !d.registrations[dev.id].allow(now, 1, 10) {
			return 429, diagError("rate_limited")
		}
		return d.register(dev.id, body, now)
	}
	if runtimePath {
		if rt == nil {
			if admin || dev != nil || op != nil {
				return 403, diagError("credential_role_denied")
			}
			return 401, diagError("invalid_credentials")
		}
		if _, ok := query(r); !ok {
			return 400, diagError("invalid_query")
		}
		if path == diagnosticsPrefix+"/events" {
			if !rt.eventRate.allow(now, 1, 120) || !rt.eventByteRate.allow(now, len(body), 256*1024) {
				return 429, diagError("telemetry_rate_limited")
			}
			return d.receiveEvents(rt, body, now)
		}
		if !rt.controlRate.allow(now, 1, 240) {
			return 429, diagError("rate_limited")
		}
		if path == diagnosticsPrefix+"/poll" {
			return d.controlPoll(rt, body, now)
		}
		return d.controlResult(rt, body, now)
	}
	if op == nil {
		if admin || dev != nil || rt != nil {
			return 403, diagError("credential_role_denied")
		}
		return 401, diagError("invalid_credentials")
	}
	if !op.rate.allow(now, 1, 240) {
		return 429, diagError("rate_limited")
	}
	if path == diagnosticsPrefix+"/runtimes" {
		q, ok := query(r, "device_id")
		if !ok || q.Get("device_id") == "" {
			return 400, diagError("invalid_query")
		}
		id := q.Get("device_id")
		if !op.actions["runtimes.read"] || !op.devices[id] {
			return 404, diagError("not_found")
		}
		if !d.enabled[id] {
			return 403, diagError("diagnostics_disabled")
		}
		rows := make([]map[string]any, 0)
		ids := []string{}
		for id, rt := range d.runtimes {
			if rt.device == q.Get("device_id") {
				ids = append(ids, id)
			}
		}
		sort.Strings(ids)
		for _, id := range ids {
			rt := d.runtimes[id]
			age := now.Sub(rt.lastSeen).Milliseconds()
			if age < 0 {
				age = 0
			}
			consent := map[string]any{"granted": rt.consent}
			if rt.consent {
				consent["epoch"] = rt.consentEpoch
			}
			rows = append(rows, map[string]any{"runtime_id": id, "instance_id": rt.instance, "boot_id": rt.boot, "last_seen_age_ms": age, "consent": consent, "capabilities": rt.capabilities, "active_session_id": rt.active})
		}
		return 200, map[string]any{"runtimes": rows}
	}
	if _, ok := query(r, "after_seq", "limit"); !ok {
		return 400, diagError("invalid_query")
	}
	if !(r.Method == "GET" && strings.HasSuffix(path, "/events")) && r.URL.RawQuery != "" {
		return 400, diagError("invalid_query")
	}
	if path == diagnosticsPrefix+"/sessions" {
		return d.startSession(op, body, now)
	}
	parts := strings.Split(strings.TrimPrefix(path, diagnosticsPrefix+"/"), "/")
	if parts[0] == "runtimes" {
		rt := d.runtimes[parts[1]]
		if rt == nil || !op.actions["runtimes.revoke"] || !op.devices[rt.device] {
			return 404, diagError("not_found")
		}
		epochs := r.Header.Values("X-OTT-Diagnostics-Epoch")
		if len(epochs) != 1 || epochs[0] != d.epoch {
			return 409, diagError("server_epoch_mismatch")
		}
		for _, ss := range d.sessions {
			if ss.runtime == rt.id && (ss.state == "active" || ss.state == "start_pending" || ss.state == "stop_pending") {
				d.terminal(ss, "revoked", now)
			}
		}
		delete(d.runtimes, rt.id)
		return 200, map[string]any{"status": "revoked", "device_stop_confirmed": false}
	}
	ss := d.sessions[parts[1]]
	action := "sessions.read"
	if r.Method == "POST" {
		action = "sessions.stop"
	}
	if ss == nil || !op.actions[action] || !op.devices[ss.device] {
		return 404, diagError("not_found")
	}
	if !d.enabled[ss.device] {
		return 403, diagError("diagnostics_disabled")
	}
	if r.Method == "POST" {
		return d.stopSession(op, ss, body, now)
	}
	if len(parts) == 3 {
		return d.eventPage(ss, r)
	}
	return 200, d.sessionView(ss, now)
}
func (d *diagnosticsState) register(device string, body []byte, now time.Time) (int, map[string]any) {
	var v struct {
		Instance     string      `json:"instance_id"`
		Boot         string      `json:"boot_id"`
		UUID         string      `json:"reported_uuid,omitempty"`
		Capabilities []string    `json:"capabilities"`
		Consent      diagConsent `json:"consent"`
	}
	if !diagDecode(body, &v, []string{"instance_id", "boot_id", "capabilities", "consent"}, "reported_uuid") || !diagLabel.MatchString(v.Instance) || !diagLabel.MatchString(v.Boot) || (v.UUID != "" && !diagLabel.MatchString(v.UUID)) || !v.Consent.valid() || v.Capabilities == nil || len(v.Capabilities) > 4 {
		return 400, diagError("invalid_registration")
	}
	seen := map[string]bool{}
	for _, x := range v.Capabilities {
		if seen[x] {
			return 400, diagError("invalid_capabilities")
		}
		seen[x] = true
		switch x {
		case "playback", "network", "input", "epg":
		default:
			return 400, diagError("invalid_capabilities")
		}
	}
	count := 0
	for _, rt := range d.runtimes {
		if rt.device == device {
			count++
		}
	}
	if count >= d.cfg.MaxRuntimesPerDevice || len(d.runtimes) >= d.cfg.MaxRuntimesTotal {
		return 429, diagError("runtime_limit")
	}
	id, e := diagRandom(d.random, 16)
	if e != nil {
		return 503, diagError("entropy_unavailable")
	}
	credential, hash, e := diagCredential(d.random)
	if e != nil {
		return 503, diagError("entropy_unavailable")
	}
	if d.runtimes[id] != nil || d.runtime(hash) != nil {
		return 503, diagError("identity_collision")
	}
	rt := &diagRuntime{id: id, device: device, instance: v.Instance, boot: v.Boot, capabilities: append([]string{}, v.Capabilities...), digest: hash, consent: *v.Consent.Granted, consentEpoch: v.Consent.Epoch, expires: now.Add(diagMS(min(d.cfg.RuntimeTTLMS, 10000))), lastSeen: now}
	// reported_uuid is validated but deliberately not retained or listed.
	d.runtimes[id] = rt
	return 201, map[string]any{"device_id": device, "runtime_id": id, "runtime_credential": credential, "runtime_ttl_ms": min(d.cfg.RuntimeTTLMS, 10000), "limits": map[string]int{"session_lease_ms_max": d.cfg.SessionLeaseMSMax, "event_bytes_max": 1024, "events_body_bytes": 16384, "events_per_batch": 32, "poll_after_ms": 3000}}
}
func diagFingerprint(action string, v any) [32]byte {
	b, _ := json.Marshal(v)
	return sha256.Sum256(append([]byte(action+"\n"), b...))
}
func (d *diagnosticsState) replay(op *diagOperator, key string, fp [32]byte) (int, map[string]any, bool) {
	old, ok := d.idempotency[op.id+"/"+key]
	if !ok {
		return 0, nil, false
	}
	if old.fingerprint != fp {
		return 409, diagError("idempotency_conflict"), true
	}
	// The immutable receipt records acceptance, not a fresh transition or current state.
	copy := map[string]any{}
	for k, v := range old.reply {
		copy[k] = v
	}
	return 202, copy, true
}
func (d *diagnosticsState) remember(op *diagOperator, key string, fp [32]byte, reply map[string]any, now time.Time) {
	d.idempotency[op.id+"/"+key] = diagIdempotency{fp, reply, now.Add(diagMS(d.cfg.IdempotencyRetentionMS))}
}
func (d *diagnosticsState) receipt(ss *diagSession) map[string]any {
	return map[string]any{"session_id": ss.id, "request_id": ss.control.RequestID, "state": ss.state, "control_revision": ss.control.Revision, "idempotency_retention_ms": d.cfg.IdempotencyRetentionMS}
}
func (d *diagnosticsState) startSession(op *diagOperator, body []byte, now time.Time) (int, map[string]any) {
	var v struct {
		Epoch   string `json:"server_epoch"`
		Key     string `json:"idempotency_key"`
		Device  string `json:"device_id"`
		Runtime string `json:"runtime_id"`
		Consent string `json:"consent_epoch"`
		Lease   int    `json:"lease_ms"`
		Profile string `json:"profile"`
	}
	if !diagDecode(body, &v, []string{"server_epoch", "idempotency_key", "device_id", "runtime_id", "consent_epoch", "lease_ms", "profile"}) || !diagLabel.MatchString(v.Key) || !commandID.MatchString(v.Runtime) || !diagLabel.MatchString(v.Consent) || v.Lease < 1000 || v.Lease > d.cfg.SessionLeaseMSMax || v.Profile != "standard" {
		return 400, diagError("invalid_start")
	}
	if !op.actions["sessions.start"] || !op.devices[v.Device] {
		return 404, diagError("not_found")
	}
	if !d.enabled[v.Device] {
		return 403, diagError("diagnostics_disabled")
	}
	if v.Epoch != d.epoch {
		return 409, diagError("server_epoch_mismatch")
	}
	fp := diagFingerprint("start", v)
	if code, value, ok := d.replay(op, v.Key, fp); ok {
		return code, value
	}
	rt := d.runtimes[v.Runtime]
	if rt == nil || rt.device != v.Device {
		return 404, diagError("not_found")
	}
	if !rt.consent || rt.consentEpoch != v.Consent {
		return 403, diagError("consent_required")
	}
	if rt.active != "" {
		return 409, diagError("runtime_busy")
	}
	// Each accepted start reserves a stop receipt slot independently of later starts.
	if len(d.sessions) >= d.cfg.MaxRetainedSessions || len(d.idempotency)+d.reservedStops+2 > d.cfg.MaxIdempotencyRecords {
		return 429, diagError("state_limit")
	}
	id, e := diagRandom(d.random, 16)
	if e != nil {
		return 503, diagError("entropy_unavailable")
	}
	request, e := diagRandom(d.random, 16)
	if e != nil {
		return 503, diagError("entropy_unavailable")
	}
	if d.sessions[id] != nil || rt.revision >= diagSafeInteger {
		return 503, diagError("identity_limit")
	}
	rt.revision++
	ss := &diagSession{id: id, device: rt.device, runtime: rt.id, consentEpoch: rt.consentEpoch, state: "start_pending", deadline: now.Add(diagMS(v.Lease)), results: map[string][32]byte{}, truncated: 1, stopReserved: true}
	ss.control = diagControl{RequestID: request, SessionID: id, Action: "start", Revision: rt.revision, ConsentEpoch: rt.consentEpoch, LeaseMS: int64(v.Lease), Profile: "standard"}
	rt.active = id
	d.sessions[id] = ss
	d.reservedStops++
	reply := d.receipt(ss)
	d.remember(op, v.Key, fp, reply, now)
	return 202, reply
}
func (d *diagnosticsState) issueStop(rt *diagRuntime, ss *diagSession) bool {
	if ss.control.Action == "stop" {
		ss.state = "stop_pending"
		return true
	}
	if rt.revision >= diagSafeInteger {
		return false
	}
	id, e := diagRandom(d.random, 16)
	if e != nil || id == ss.control.RequestID {
		return false
	}
	rt.revision++
	ss.control = diagControl{RequestID: id, SessionID: ss.id, Action: "stop", Revision: rt.revision, ConsentEpoch: ss.consentEpoch}
	ss.state = "stop_pending"
	return true
}
func (d *diagnosticsState) stopSession(op *diagOperator, ss *diagSession, body []byte, now time.Time) (int, map[string]any) {
	var v struct {
		Epoch  string `json:"server_epoch"`
		Key    string `json:"idempotency_key"`
		Reason string `json:"reason"`
	}
	if !diagDecode(body, &v, []string{"server_epoch", "idempotency_key", "reason"}) || !diagLabel.MatchString(v.Key) || v.Reason != "operator" {
		return 400, diagError("invalid_stop")
	}
	if v.Epoch != d.epoch {
		return 409, diagError("server_epoch_mismatch")
	}
	fp := diagFingerprint("stop/"+ss.id, v)
	if code, value, ok := d.replay(op, v.Key, fp); ok {
		return code, value
	}
	if ss.stopKey != "" {
		return 409, diagError("stop_already_requested")
	}
	if ss.state != "active" && ss.state != "start_pending" && ss.state != "stop_pending" {
		return 409, diagError("session_terminal")
	}
	rt := d.runtimes[ss.runtime]
	if rt == nil {
		return 409, diagError("runtime_expired")
	}
	if !ss.stopReserved {
		return 429, diagError("state_limit")
	}
	if !d.issueStop(rt, ss) {
		return 503, diagError("entropy_unavailable")
	}
	ss.stopKey = op.id + "/" + v.Key
	ss.stopReserved = false
	d.reservedStops--
	reply := d.receipt(ss)
	d.remember(op, v.Key, fp, reply, now)
	return 202, reply
}
func (d *diagnosticsState) controlPoll(rt *diagRuntime, body []byte, now time.Time) (int, map[string]any) {
	var v struct {
		Runtime  string      `json:"runtime_id"`
		Seq      uint64      `json:"poll_seq"`
		Revision uint64      `json:"last_control_revision"`
		Consent  diagConsent `json:"consent"`
	}
	if !diagDecode(body, &v, []string{"runtime_id", "poll_seq", "last_control_revision", "consent"}) || !v.Consent.valid() || v.Seq == 0 || v.Seq > diagSafeInteger || v.Revision > diagSafeInteger {
		return 400, diagError("invalid_poll")
	}
	if v.Runtime != rt.id {
		return 403, diagError("runtime_mismatch")
	}
	fp := diagFingerprint("poll", v)
	if v.Seq < rt.pollSeq || (v.Seq == rt.pollSeq && fp != rt.pollHash) {
		return 409, diagError("stale_poll")
	}
	if v.Revision > rt.revision {
		return 409, diagError("future_revision")
	}
	if v.Seq > rt.pollSeq {
		changed := rt.consent != *v.Consent.Granted || rt.consentEpoch != v.Consent.Epoch
		if changed && *v.Consent.Granted && rt.active != "" {
			if ss := d.sessions[rt.active]; ss != nil {
				if !d.issueStop(rt, ss) {
					d.terminal(ss, "revoked", now)
				}
			}
		}
		rt.consent = *v.Consent.Granted
		rt.consentEpoch = v.Consent.Epoch
		rt.pollSeq = v.Seq
		rt.pollHash = fp
	}
	if !rt.consent {
		// Final local-consent withdrawal retires this credential and slot. A later
		// grant must register a fresh runtime; delayed requests cannot resurrect it.
		for _, ss := range d.sessions {
			if ss.runtime == rt.id && (ss.state == "active" || ss.state == "start_pending" || ss.state == "stop_pending") {
				d.terminal(ss, "revoked", now)
			}
		}
		delete(d.runtimes, rt.id)
		return 200, map[string]any{"runtime_id": rt.id, "poll_after_ms": 3000, "control_revision": rt.revision, "control": nil}
	}
	rt.lastSeen = now
	rt.expires = now.Add(diagMS(d.cfg.RuntimeTTLMS))
	var control any
	interval := 3000
	if ss := d.sessions[rt.active]; ss != nil {
		interval = 1000
		if ss.state == "start_pending" || ss.state == "stop_pending" {
			c := ss.control
			if c.Action == "start" {
				c.LeaseMS = diagRemaining(ss.deadline, now)
			}
			control = c
		}
	}
	return 200, map[string]any{"runtime_id": rt.id, "poll_after_ms": interval, "control_revision": rt.revision, "control": control}
}
func (d *diagnosticsState) controlResult(rt *diagRuntime, body []byte, now time.Time) (int, map[string]any) {
	var v struct {
		Runtime  string `json:"runtime_id"`
		Session  string `json:"session_id"`
		Request  string `json:"request_id"`
		Revision uint64 `json:"control_revision"`
		Status   string `json:"status"`
		Error    string `json:"error_code,omitempty"`
	}
	if !diagDecode(body, &v, []string{"runtime_id", "session_id", "request_id", "control_revision", "status"}, "error_code") || !commandID.MatchString(v.Session) || !commandID.MatchString(v.Request) || v.Revision == 0 || v.Revision > diagSafeInteger {
		return 400, diagError("invalid_result")
	}
	if v.Runtime != rt.id {
		return 403, diagError("runtime_mismatch")
	}
	if v.Status != "applied" && v.Status != "rejected" && v.Status != "unsupported" {
		return 400, diagError("invalid_result")
	}
	switch v.Error {
	case "", "consent_revoked", "lease_expired", "disconnected", "suspended", "unsupported", "invalid_control", "local_stop":
	default:
		return 400, diagError("invalid_error_code")
	}
	ss := d.sessions[v.Session]
	if ss == nil || ss.runtime != rt.id {
		return 404, diagError("not_found")
	}
	fp := diagFingerprint("result", v)
	if old, ok := ss.results[v.Request]; ok {
		if old != fp {
			return 409, diagError("result_conflict")
		}
		return 200, map[string]any{"status": "recorded"}
	}
	if v.Request != ss.control.RequestID || v.Revision != ss.control.Revision {
		return 409, diagError("stale_control")
	}
	if ss.control.Action == "start" {
		if ss.state != "start_pending" || !rt.consent || rt.consentEpoch != ss.consentEpoch || !now.Before(ss.deadline) {
			return 409, diagError("session_terminal")
		}
		if v.Status == "applied" {
			ss.state = "active"
		} else {
			d.terminal(ss, "rejected", now)
		}
	} else {
		if ss.state != "stop_pending" && ss.state != "expired" {
			return 409, diagError("session_terminal")
		}
		if v.Status == "applied" {
			ss.confirmed = true
			d.terminal(ss, "stopped", now)
		}
	}
	// One start plus one stop result at most; conflicting repeat never overwrites it.
	ss.results[v.Request] = fp
	return 200, map[string]any{"status": "recorded"}
}
func (d *diagnosticsState) sessionView(ss *diagSession, now time.Time) map[string]any {
	return map[string]any{"session_id": ss.id, "device_id": ss.device, "runtime_id": ss.runtime, "state": ss.state, "device_stop_confirmed": ss.confirmed, "lease_remaining_ms": diagRemaining(ss.deadline, now), "control_revision": ss.control.Revision, "dropped_total": ss.dropped}
}
