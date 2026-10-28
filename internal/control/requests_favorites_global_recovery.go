package control

// Additive global-ledger recovery. These checks bind public claims; they never
// establish physical persistence or authorize replay of the original operation.
import "encoding/json"

type favoritesGlobalRecoveryCursor struct {
	After, Ledger, Store, Context, Backend string
}

type favoritesGlobalRecoveryRequest struct {
	Runtime, Op, OperationID, OperationRuntime string
	Entry, Ledger, Store, Context, Backend     string
	AcknowledgementID, AfterLedger, Plan       string
	Cursor                                     *favoritesGlobalRecoveryCursor
}

type favoritesGlobalRecoveryRecord struct {
	OperationID, Runtime, State, Outcome, Entry string
	Audit                                       *favoritesGlobalRecoveryRequest
}

func globalRecoveryHash(raw json.RawMessage) bool {
	_, ok := favoritesTransferHash(raw)
	return ok
}
func globalRecoveryRuntime(raw json.RawMessage) bool {
	s, ok := inspectString(raw, 64)
	return ok && screenshotRuntimePattern.MatchString(s)
}
func globalRecoveryPresent(raw json.RawMessage) bool {
	return globalRecoveryHash(raw) && favoritesGlobalString(raw) != favoritesRecoveryAbsentRevision
}
func globalRecoveryTuple(domain string, q *favoritesGlobalRecoveryRequest) []any {
	return []any{domain, q.Runtime, "global", q.OperationID, q.OperationRuntime, q.Entry, q.Ledger, q.Store, q.Context, q.Backend, favoritesRecoveryReason}
}
func globalRecoveryAcknowledgement(q *favoritesGlobalRecoveryRequest) string {
	return favoritesRecoveryHashTuple(globalRecoveryTuple("ottplay-favorites-global-recovery-ack-v1", q))
}
func globalRecoveryPlan(q *favoritesGlobalRecoveryRequest) string {
	return favoritesRecoveryHashTuple(append(globalRecoveryTuple("ottplay-favorites-global-recovery-plan-v1", q), q.AcknowledgementID, q.AfterLedger))
}
func parseGlobalRecoveryCursor(raw json.RawMessage) *favoritesGlobalRecoveryCursor {
	m, ok := inspectObject(raw, "after_operation_id", "ledger_revision", "store_revision", "context_revision", "backend_proof")
	id, valid := favoritesTransferUUID(m["after_operation_id"])
	if !ok || !valid || !globalRecoveryPresent(m["ledger_revision"]) || !globalRecoveryHash(m["store_revision"]) || !globalRecoveryHash(m["context_revision"]) || !(inspectNull(m["backend_proof"]) || globalRecoveryHash(m["backend_proof"])) {
		return nil
	}
	return &favoritesGlobalRecoveryCursor{After: id, Ledger: favoritesGlobalString(m["ledger_revision"]), Store: favoritesGlobalString(m["store_revision"]), Context: favoritesGlobalString(m["context_revision"]), Backend: favoritesGlobalString(m["backend_proof"])}
}
func parseFavoritesGlobalRecoveryRequest(raw json.RawMessage) *favoritesGlobalRecoveryRequest {
	if len(raw) > maxFavoritesTransferEnvelopeBytes || !validFavoritesTransferJSON(raw) {
		return nil
	}
	m, err := decodeObject(raw)
	if err != nil || !globalRecoveryRuntime(m["runtime"]) || !inspectEnum(m["subject"], "global") {
		return nil
	}
	q := &favoritesGlobalRecoveryRequest{Runtime: favoritesGlobalString(m["runtime"]), Op: favoritesGlobalString(m["op"])}
	switch q.Op {
	case "inspect":
		if _, exists := m["operation_id"]; exists {
			if !favoritesTransferFields(m, "runtime", "op", "subject", "operation_id") {
				return nil
			}
			var valid bool
			q.OperationID, valid = favoritesTransferUUID(m["operation_id"])
			if !valid {
				return nil
			}
		} else {
			if !favoritesTransferFields(m, "runtime", "op", "subject", "cursor") {
				return nil
			}
			if !inspectNull(m["cursor"]) {
				q.Cursor = parseGlobalRecoveryCursor(m["cursor"])
				if q.Cursor == nil {
					return nil
				}
			}
		}
	case "acknowledge":
		if !favoritesTransferFields(m, "runtime", "op", "subject", "operation_id", "operation_runtime", "original_entry_sha256", "expected_ledger_revision", "expected_store_revision", "expected_context_revision", "backend_proof", "reason", "acknowledgement_id", "after_ledger_sha256", "plan_sha256", "accept_unknown") || !inspectEnum(m["reason"], favoritesRecoveryReason) || !inspectBool(m["accept_unknown"], false) || !favoritesGlobalBool(m["accept_unknown"]) || !globalRecoveryRuntime(m["operation_runtime"]) {
			return nil
		}
		var valid bool
		q.OperationID, valid = favoritesTransferUUID(m["operation_id"])
		if !valid {
			return nil
		}
		q.OperationRuntime = favoritesGlobalString(m["operation_runtime"])
		for key, dst := range map[string]*string{"original_entry_sha256": &q.Entry, "expected_ledger_revision": &q.Ledger, "expected_store_revision": &q.Store, "expected_context_revision": &q.Context, "backend_proof": &q.Backend, "acknowledgement_id": &q.AcknowledgementID, "after_ledger_sha256": &q.AfterLedger, "plan_sha256": &q.Plan} {
			if !globalRecoveryHash(m[key]) {
				return nil
			}
			*dst = favoritesGlobalString(m[key])
		}
		if q.Ledger == favoritesRecoveryAbsentRevision || q.AfterLedger == favoritesRecoveryAbsentRevision || q.AfterLedger == q.Ledger || q.AcknowledgementID != globalRecoveryAcknowledgement(q) || q.Plan != globalRecoveryPlan(q) {
			return nil
		}
	default:
		return nil
	}
	return q
}

func validFavoritesGlobalRecoveryEnvelope(raw json.RawMessage) bool {
	if len(raw) > maxFavoritesTransferEnvelopeBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "action", "params", "expires_at")
	id, valid := inspectString(m["id"], 32)
	return ok && valid && commandID.MatchString(id) && inspectEnum(m["action"], "favorites_global_recovery") && numberValue(m["expires_at"], 1, inspectMaxInteger, false) && parseFavoritesGlobalRecoveryRequest(m["params"]) != nil
}

func parseGlobalRecoveryRecord(raw json.RawMessage) *favoritesGlobalRecoveryRecord {
	m, ok := inspectObject(raw, "operation_id", "operation_runtime", "subject", "original_state", "original_outcome", "original_entry_sha256", "acknowledgement")
	id, valid := favoritesTransferUUID(m["operation_id"])
	if !ok || !valid || !globalRecoveryRuntime(m["operation_runtime"]) || !inspectEnum(m["subject"], "global") || !globalRecoveryHash(m["original_entry_sha256"]) || !inspectEnum(m["original_state"], "pending", "partial", "unknown") {
		return nil
	}
	r := &favoritesGlobalRecoveryRecord{OperationID: id, Runtime: favoritesGlobalString(m["operation_runtime"]), State: favoritesGlobalString(m["original_state"]), Outcome: favoritesGlobalString(m["original_outcome"]), Entry: favoritesGlobalString(m["original_entry_sha256"])}
	if (r.State == "partial" && r.Outcome != "partial") || (r.State != "partial" && r.Outcome != "unknown") {
		return nil
	}
	if !inspectNull(m["acknowledgement"]) {
		a, valid := inspectObject(m["acknowledgement"], "acknowledgement_id", "runtime", "ledger_revision_before", "store_revision", "context_revision", "backend_proof", "reason")
		if !valid || !globalRecoveryRuntime(a["runtime"]) || !globalRecoveryPresent(a["ledger_revision_before"]) || !inspectEnum(a["reason"], favoritesRecoveryReason) {
			return nil
		}
		q := &favoritesGlobalRecoveryRequest{Runtime: favoritesGlobalString(a["runtime"]), OperationID: r.OperationID, OperationRuntime: r.Runtime, Entry: r.Entry}
		for key, dst := range map[string]*string{"acknowledgement_id": &q.AcknowledgementID, "ledger_revision_before": &q.Ledger, "store_revision": &q.Store, "context_revision": &q.Context, "backend_proof": &q.Backend} {
			if !globalRecoveryHash(a[key]) {
				return nil
			}
			*dst = favoritesGlobalString(a[key])
		}
		if q.AcknowledgementID != globalRecoveryAcknowledgement(q) {
			return nil
		}
		r.Audit = q
	}
	return r
}

func globalRecoveryError(raw json.RawMessage) bool {
	return inspectEnum(raw, "invalid_request", "envelope_too_large", "runtime_changed", "expired", "busy", "unsupported_persistence", "revision_conflict", "context_changed", "policy_denied", "storage_unavailable", "limits_exceeded", "outcome_unknown")
}
func globalRecoveryRevisions(m map[string]json.RawMessage) bool {
	return globalRecoveryHash(m["ledger_revision"]) && globalRecoveryHash(m["store_revision"]) && globalRecoveryHash(m["context_revision"]) && (inspectNull(m["backend_proof"]) || globalRecoveryHash(m["backend_proof"]))
}
func globalRecoveryCursorMatches(c *favoritesGlobalRecoveryCursor, m map[string]json.RawMessage) bool {
	return c.Ledger == favoritesGlobalString(m["ledger_revision"]) && c.Store == favoritesGlobalString(m["store_revision"]) && c.Context == favoritesGlobalString(m["context_revision"]) && c.Backend == favoritesGlobalString(m["backend_proof"])
}
func validFavoritesGlobalRecoveryResult(raw json.RawMessage, status string, q *favoritesGlobalRecoveryRequest) bool {
	if q == nil || len(raw) > maxFavoritesTransferMetadataBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || !inspectEnum(m["runtime"], q.Runtime) || !inspectEnum(m["op"], q.Op) || !inspectEnum(m["subject"], "global") {
		return false
	}
	if status == "rejected" || status == "unsupported" {
		return favoritesTransferFields(m, "version", "runtime", "op", "subject", "error") && globalRecoveryError(m["error"])
	}
	if status != "ok" {
		return false
	}
	if q.Op == "acknowledge" {
		return validGlobalRecoveryAcknowledged(m, q)
	}
	if !profileInteger(m["ledger_format"], 1, 1) || !globalRecoveryRevisions(m) {
		return false
	}
	if q.OperationID == "" {
		return validGlobalRecoveryPage(m, q)
	}
	if !favoritesTransferFields(m, "version", "runtime", "op", "subject", "operation_id", "ledger_format", "ledger_revision", "store_revision", "context_revision", "backend_proof", "record", "acknowledge_allowed", "acknowledgement_id", "after_ledger_sha256", "plan_sha256") || !inspectEnum(m["operation_id"], q.OperationID) || !inspectBool(m["acknowledge_allowed"], false) {
		return false
	}
	var r *favoritesGlobalRecoveryRecord
	if !inspectNull(m["record"]) {
		r = parseGlobalRecoveryRecord(m["record"])
		if r == nil || r.OperationID != q.OperationID || !globalRecoveryPresent(m["ledger_revision"]) {
			return false
		}
	}
	if !favoritesGlobalBool(m["acknowledge_allowed"]) {
		return inspectNull(m["acknowledgement_id"]) && inspectNull(m["after_ledger_sha256"]) && inspectNull(m["plan_sha256"])
	}
	if r == nil || r.Audit != nil || !globalRecoveryHash(m["backend_proof"]) || !globalRecoveryHash(m["acknowledgement_id"]) || !globalRecoveryPresent(m["after_ledger_sha256"]) || !globalRecoveryHash(m["plan_sha256"]) {
		return false
	}
	plan := &favoritesGlobalRecoveryRequest{Runtime: q.Runtime, OperationID: r.OperationID, OperationRuntime: r.Runtime, Entry: r.Entry, Ledger: favoritesGlobalString(m["ledger_revision"]), Store: favoritesGlobalString(m["store_revision"]), Context: favoritesGlobalString(m["context_revision"]), Backend: favoritesGlobalString(m["backend_proof"]), AcknowledgementID: favoritesGlobalString(m["acknowledgement_id"]), AfterLedger: favoritesGlobalString(m["after_ledger_sha256"]), Plan: favoritesGlobalString(m["plan_sha256"])}
	return plan.AfterLedger != plan.Ledger && plan.AcknowledgementID == globalRecoveryAcknowledgement(plan) && plan.Plan == globalRecoveryPlan(plan)
}

func validGlobalRecoveryPage(m map[string]json.RawMessage, q *favoritesGlobalRecoveryRequest) bool {
	if !favoritesTransferFields(m, "version", "runtime", "op", "subject", "ledger_format", "ledger_revision", "store_revision", "context_revision", "backend_proof", "total", "records", "next_cursor") || !profileInteger(m["total"], 0, 32) {
		return false
	}
	rows, ok := inspectArray(m["records"], 16)
	if !ok {
		return false
	}
	total := favoritesGlobalInt(m["total"])
	expected := min(total, 16)
	previous := ""
	if q.Cursor != nil {
		if total <= 16 || !globalRecoveryCursorMatches(q.Cursor, m) {
			return false
		}
		expected = total - 16
		previous = q.Cursor.After
	}
	if len(rows) != expected || (favoritesGlobalString(m["ledger_revision"]) == favoritesRecoveryAbsentRevision && total != 0) {
		return false
	}
	for _, raw := range rows {
		r := parseGlobalRecoveryRecord(raw)
		if r == nil || r.OperationID <= previous {
			return false
		}
		previous = r.OperationID
	}
	if q.Cursor != nil || total <= 16 {
		return inspectNull(m["next_cursor"])
	}
	c := parseGlobalRecoveryCursor(m["next_cursor"])
	return c != nil && c.After == previous && globalRecoveryCursorMatches(c, m)
}
func validGlobalRecoveryAcknowledged(m map[string]json.RawMessage, q *favoritesGlobalRecoveryRequest) bool {
	if !favoritesTransferFields(m, "version", "runtime", "op", "subject", "operation_id", "operation_runtime", "acknowledgement_id", "plan_sha256", "acknowledged", "persistence", "ledger_revision", "store_revision", "context_revision", "record") || !inspectEnum(m["operation_id"], q.OperationID) || !inspectEnum(m["operation_runtime"], q.OperationRuntime) || !inspectEnum(m["acknowledgement_id"], q.AcknowledgementID) || !inspectEnum(m["plan_sha256"], q.Plan) || !inspectBool(m["acknowledged"], false) || !favoritesGlobalBool(m["acknowledged"]) || !inspectEnum(m["persistence"], "persistent") || !inspectEnum(m["ledger_revision"], q.AfterLedger) || !inspectEnum(m["store_revision"], q.Store) || !inspectEnum(m["context_revision"], q.Context) {
		return false
	}
	r := parseGlobalRecoveryRecord(m["record"])
	if r == nil || r.OperationID != q.OperationID || r.Runtime != q.OperationRuntime || r.Entry != q.Entry || r.Audit == nil {
		return false
	}
	a := r.Audit
	return q.AfterLedger != favoritesRecoveryAbsentRevision && q.AfterLedger != q.Ledger && a.Runtime == q.Runtime && a.Ledger == q.Ledger && a.Store == q.Store && a.Context == q.Context && a.Backend == q.Backend && a.AcknowledgementID == q.AcknowledgementID
}
