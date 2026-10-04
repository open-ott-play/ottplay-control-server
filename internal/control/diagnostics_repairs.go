package control

import "time"

const maxDiagnosticRepairs = 256
const diagnosticRepairRetention = 15 * time.Minute

type diagRepair struct {
	id, device, runtime, consentEpoch, action, state string
	deadline, retainUntil                            time.Time
}

func (d *diagnosticsState) terminalRepair(repair *diagRepair, state string, now time.Time) {
	repair.state = state
	if rt := d.runtimes[repair.runtime]; rt != nil && rt.pendingRepair == repair.id {
		rt.pendingRepair = ""
	}
	if repair.retainUntil.IsZero() {
		repair.retainUntil = now.Add(diagnosticRepairRetention)
	}
}

func (d *diagnosticsState) revokeRepairs(runtime string, now time.Time) {
	for _, repair := range d.repairs {
		if repair.runtime == runtime && repair.state == "pending" {
			d.terminalRepair(repair, "revoked", now)
		}
	}
}

func (d *diagnosticsState) expireRepairs(now time.Time) {
	for id, repair := range d.repairs {
		if repair.state == "pending" {
			rt := d.runtimes[repair.runtime]
			if rt == nil || !d.enabled[repair.device] || !rt.consent || rt.consentEpoch != repair.consentEpoch {
				d.terminalRepair(repair, "revoked", now)
			} else if !now.Before(repair.deadline) {
				d.terminalRepair(repair, "expired", now)
			}
		}
		if !repair.retainUntil.IsZero() && !now.Before(repair.retainUntil) {
			delete(d.repairs, id)
		}
	}
}

func repairCapable(rt *diagRuntime) bool {
	for _, capability := range rt.capabilities {
		if capability == "repairs" {
			return true
		}
	}
	return false
}

func (d *diagnosticsState) startRepair(op *diagOperator, body []byte, now time.Time) (int, map[string]any) {
	var v struct {
		Epoch    string `json:"server_epoch"`
		Key      string `json:"idempotency_key"`
		Device   string `json:"device_id"`
		Runtime  string `json:"runtime_id"`
		Consent  string `json:"consent_epoch"`
		Action   string `json:"action"`
		Deadline int    `json:"deadline_ms"`
	}
	if !diagDecode(body, &v, []string{"server_epoch", "idempotency_key", "device_id", "runtime_id", "consent_epoch", "action", "deadline_ms"}) || !diagLabel.MatchString(v.Key) || !commandID.MatchString(v.Runtime) || !diagLabel.MatchString(v.Consent) || v.Deadline < 1000 || v.Deadline > 30000 || (v.Action != "restart_stream" && v.Action != "reload_player") {
		return 400, diagError("invalid_repair")
	}
	if !op.actions["repairs.start"] || !op.devices[v.Device] {
		return 404, diagError("not_found")
	}
	if !d.enabled[v.Device] {
		return 403, diagError("diagnostics_disabled")
	}
	if v.Epoch != d.epoch {
		return 409, diagError("server_epoch_mismatch")
	}
	fp := diagFingerprint("repair", v)
	if code, reply, ok := d.replay(op, v.Key, fp); ok {
		return code, reply
	}
	rt := d.runtimes[v.Runtime]
	if rt == nil || rt.device != v.Device {
		return 404, diagError("not_found")
	}
	if !repairCapable(rt) {
		return 403, diagError("capability_required")
	}
	if !rt.consent || rt.consentEpoch != v.Consent {
		return 403, diagError("consent_required")
	}
	if rt.pendingRepair != "" {
		return 409, diagError("repair_busy")
	}
	if len(d.repairs) >= maxDiagnosticRepairs || len(d.idempotency)+d.reservedStops+1 > d.cfg.MaxIdempotencyRecords {
		return 429, diagError("state_limit")
	}
	id, e := diagRandom(d.random, 16)
	if e != nil {
		return 503, diagError("entropy_unavailable")
	}
	if d.repairs[id] != nil {
		return 503, diagError("identity_collision")
	}
	repair := &diagRepair{id: id, device: rt.device, runtime: rt.id, consentEpoch: rt.consentEpoch, action: v.Action, state: "pending", deadline: now.Add(diagMS(v.Deadline))}
	d.repairs[id] = repair
	rt.pendingRepair = id
	reply := map[string]any{"repair_id": id, "state": "pending", "idempotency_retention_ms": d.cfg.IdempotencyRetentionMS}
	d.remember(op, v.Key, fp, reply, now)
	return 202, reply
}

func (d *diagnosticsState) repairPoll(rt *diagRuntime, body []byte, now time.Time) (int, map[string]any) {
	var v struct {
		Runtime string `json:"runtime_id"`
	}
	if !diagDecode(body, &v, []string{"runtime_id"}) {
		return 400, diagError("invalid_repair_poll")
	}
	if v.Runtime != rt.id {
		return 403, diagError("runtime_mismatch")
	}
	if !repairCapable(rt) {
		return 403, diagError("capability_required")
	}
	var control any
	if repair := d.repairs[rt.pendingRepair]; repair != nil && repair.state == "pending" && rt.consent && rt.consentEpoch == repair.consentEpoch && now.Before(repair.deadline) {
		control = map[string]any{"repair_id": repair.id, "action": repair.action, "consent_epoch": repair.consentEpoch, "lease_ms": diagRemaining(repair.deadline, now)}
	}
	return 200, map[string]any{"repair": control}
}

func (d *diagnosticsState) repairResult(rt *diagRuntime, body []byte, now time.Time) (int, map[string]any) {
	var v struct {
		Runtime string `json:"runtime_id"`
		Repair  string `json:"repair_id"`
		Status  string `json:"status"`
	}
	if !diagDecode(body, &v, []string{"runtime_id", "repair_id", "status"}) || !commandID.MatchString(v.Repair) {
		return 400, diagError("invalid_repair_result")
	}
	if v.Runtime != rt.id {
		return 403, diagError("runtime_mismatch")
	}
	if !repairCapable(rt) {
		return 403, diagError("capability_required")
	}
	repair := d.repairs[v.Repair]
	if repair == nil {
		return 404, diagError("not_found")
	}
	if repair.runtime != rt.id {
		return 403, diagError("runtime_mismatch")
	}
	if v.Status != "applied" && v.Status != "accepted" && v.Status != "rejected" && v.Status != "unsupported" {
		return 400, diagError("invalid_repair_result")
	}
	// A recorded conflicting result never overwrites history, even if its status
	// would not have been legal for the original action.
	if repair.state == "applied" || repair.state == "accepted" || repair.state == "rejected" || repair.state == "unsupported" {
		if v.Status != repair.state {
			return 409, diagError("result_conflict")
		}
	}
	if repair.state == "expired" || repair.state == "revoked" || !rt.consent || rt.consentEpoch != repair.consentEpoch || !now.Before(repair.deadline) {
		return 409, diagError("repair_terminal")
	}
	if (v.Status == "applied" && repair.action != "restart_stream") || (v.Status == "accepted" && repair.action != "reload_player") {
		return 400, diagError("invalid_repair_result")
	}
	if repair.state == "pending" {
		d.terminalRepair(repair, v.Status, now)
	}
	return 200, map[string]any{"status": "recorded"}
}

func (d *diagnosticsState) repairView(op *diagOperator, id string, now time.Time) (int, map[string]any) {
	repair := d.repairs[id]
	if repair == nil || !op.actions["repairs.read"] || !op.devices[repair.device] {
		return 404, diagError("not_found")
	}
	if !d.enabled[repair.device] {
		return 403, diagError("diagnostics_disabled")
	}
	return 200, map[string]any{"repair_id": repair.id, "device_id": repair.device, "runtime_id": repair.runtime, "action": repair.action, "state": repair.state, "lease_remaining_ms": diagRemaining(repair.deadline, now)}
}
