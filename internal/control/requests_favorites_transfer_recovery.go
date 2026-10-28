package control

// Recovery acknowledges uncertainty, never proves the original mutation or
// authorizes its replay. Only the player can inspect its private physical ledger.

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"regexp"
)

const favoritesRecoveryReason = "accept-unknown-no-replay"
const favoritesRecoveryAbsentRevision = "74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b"

var favoritesRecoveryProviderPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_./-]{0,63}$`)

type favoritesRecoveryRequest struct {
	Mode, OperationID                          string
	Scope                                      *favoritesTransferScope
	EntrySHA, LedgerRevision, BackendProof     string
	AcknowledgementID, AfterLedgerSHA, PlanSHA string
}

type favoritesRecoveryAudit struct {
	AcknowledgementID, Runtime, LedgerRevision, BackendProof string
}

type favoritesRecoveryRecord struct {
	OperationID, State, Outcome, EntrySHA string
	Scope                                 *favoritesTransferScope
	Audit                                 *favoritesRecoveryAudit
}

func validFavoritesRecoveryCapability(raw json.RawMessage) bool {
	if len(raw) > maxFavoritesTransferMetadataBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "version")
	return ok && profileInteger(m["version"], 1, 1)
}

func favoritesRecoveryError(code string) bool {
	switch code {
	case "invalid_request", "envelope_too_large", "runtime_changed", "expired", "busy", "unsupported_persistence", "revision_conflict", "policy_denied", "storage_unavailable", "limits_exceeded":
		return true
	}
	return false
}

// Original ledger identities need not name a current/registered Plex source.
// A separate parser preserves the existing seven operations' scope restrictions.
func parseFavoritesRecoveryScope(raw json.RawMessage) *favoritesTransferScope {
	m, ok := inspectObject(raw, "profileBindingId", "sourceBindingId", "provider")
	profile, p := favoritesTransferUUID(m["profileBindingId"])
	source, s := favoritesTransferUUID(m["sourceBindingId"])
	provider, v := favoritesTransferText(m["provider"], 64)
	if !ok || !p || !s || !v || !favoritesRecoveryProviderPattern.MatchString(provider) {
		return nil
	}
	return &favoritesTransferScope{ProfileBindingID: profile, SourceBindingID: source, Provider: provider}
}

func favoritesRecoveryHashTuple(values []any) string {
	// Every member is bounded ASCII or an array thereof: compact Go JSON agrees
	// exactly with the fixed JS/Python UTF-8 tuples, including slash characters.
	raw, err := json.Marshal(values)
	if err != nil {
		return ""
	}
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func favoritesRecoveryTuple(domain, runtime, operation string, scope *favoritesTransferScope, entry, ledger, backend string) []any {
	return []any{domain, runtime, operation, []string{scope.ProfileBindingID, scope.SourceBindingID, scope.Provider}, entry, ledger, backend, favoritesRecoveryReason}
}

func favoritesRecoveryAcknowledgement(runtime, operation string, scope *favoritesTransferScope, entry, ledger, backend string) string {
	return favoritesRecoveryHashTuple(favoritesRecoveryTuple("ottplay-favorites-recovery-ack-v1", runtime, operation, scope, entry, ledger, backend))
}

func favoritesRecoveryPlan(runtime, operation string, scope *favoritesTransferScope, entry, ledger, backend, acknowledgement, after string) string {
	tuple := favoritesRecoveryTuple("ottplay-favorites-recovery-plan-v1", runtime, operation, scope, entry, ledger, backend)
	return favoritesRecoveryHashTuple(append(tuple, acknowledgement, after))
}

func parseFavoritesRecoveryRequest(params map[string]json.RawMessage, runtime string) *favoritesRecoveryRequest {
	r := &favoritesRecoveryRequest{}
	if json.Unmarshal(params["mode"], &r.Mode) != nil {
		return nil
	}
	switch r.Mode {
	case "list":
		if favoritesTransferFields(params, "op", "runtime", "mode") {
			return r
		}
	case "inspect":
		operation, valid := favoritesTransferUUID(params["operation_id"])
		if valid && favoritesTransferFields(params, "op", "runtime", "mode", "operation_id") {
			r.OperationID = operation
			return r
		}
	case "acknowledge":
		if !favoritesTransferFields(params, "op", "runtime", "mode", "operation_id", "scope", "original_entry_sha256", "expected_ledger_revision", "backend_proof", "reason", "acknowledgement_id", "after_ledger_sha256", "plan_sha256", "accept_unknown") || !bytes.Equal(bytes.TrimSpace(params["accept_unknown"]), []byte("true")) || !inspectEnum(params["reason"], favoritesRecoveryReason) {
			return nil
		}
		var valid bool
		r.OperationID, valid = favoritesTransferUUID(params["operation_id"])
		r.Scope = parseFavoritesRecoveryScope(params["scope"])
		if !valid || r.Scope == nil {
			return nil
		}
		for field, target := range map[string]*string{
			"original_entry_sha256": &r.EntrySHA, "expected_ledger_revision": &r.LedgerRevision,
			"backend_proof": &r.BackendProof, "acknowledgement_id": &r.AcknowledgementID,
			"after_ledger_sha256": &r.AfterLedgerSHA, "plan_sha256": &r.PlanSHA,
		} {
			*target, valid = favoritesTransferHash(params[field])
			if !valid {
				return nil
			}
		}
		if r.LedgerRevision != favoritesRecoveryAbsentRevision && r.AfterLedgerSHA != favoritesRecoveryAbsentRevision && r.AfterLedgerSHA != r.LedgerRevision &&
			r.AcknowledgementID == favoritesRecoveryAcknowledgement(runtime, r.OperationID, r.Scope, r.EntrySHA, r.LedgerRevision, r.BackendProof) &&
			r.PlanSHA == favoritesRecoveryPlan(runtime, r.OperationID, r.Scope, r.EntrySHA, r.LedgerRevision, r.BackendProof, r.AcknowledgementID, r.AfterLedgerSHA) {
			return r
		}
	}
	return nil
}

func favoritesRecoveryNull(raw json.RawMessage) bool {
	return bytes.Equal(bytes.TrimSpace(raw), []byte("null"))
}

func parseFavoritesRecoveryRecord(raw json.RawMessage) *favoritesRecoveryRecord {
	m, valid := inspectObject(raw, "operation_id", "scope", "original_state", "original_outcome", "original_entry_sha256", "acknowledgement")
	if !valid {
		return nil
	}
	r := &favoritesRecoveryRecord{Scope: parseFavoritesRecoveryScope(m["scope"])}
	r.OperationID, valid = favoritesTransferUUID(m["operation_id"])
	if !valid || r.Scope == nil {
		return nil
	}
	r.EntrySHA, valid = favoritesTransferHash(m["original_entry_sha256"])
	if !valid || !inspectEnum(m["original_state"], "pending", "partial", "unknown") {
		return nil
	}
	_ = json.Unmarshal(m["original_state"], &r.State)
	r.Outcome = "unknown"
	if r.State == "partial" {
		r.Outcome = "partial"
	}
	if !inspectEnum(m["original_outcome"], r.Outcome) {
		return nil
	}
	if favoritesRecoveryNull(m["acknowledgement"]) {
		return r
	}
	audit, valid := inspectObject(m["acknowledgement"], "acknowledgement_id", "runtime", "ledger_revision_before", "backend_proof", "reason")
	if !valid || !inspectEnum(audit["reason"], favoritesRecoveryReason) {
		return nil
	}
	a := &favoritesRecoveryAudit{}
	if json.Unmarshal(audit["runtime"], &a.Runtime) != nil || !screenshotRuntimePattern.MatchString(a.Runtime) {
		return nil
	}
	for field, target := range map[string]*string{
		"acknowledgement_id": &a.AcknowledgementID, "ledger_revision_before": &a.LedgerRevision, "backend_proof": &a.BackendProof,
	} {
		*target, valid = favoritesTransferHash(audit[field])
		if !valid {
			return nil
		}
	}
	if a.LedgerRevision == favoritesRecoveryAbsentRevision || a.AcknowledgementID != favoritesRecoveryAcknowledgement(a.Runtime, r.OperationID, r.Scope, r.EntrySHA, a.LedgerRevision, a.BackendProof) {
		return nil
	}
	r.Audit = a
	return r
}

func favoritesRecoveryReadBinding(m map[string]json.RawMessage) (int, string, string, bool) {
	if !profileInteger(m["ledger_format"], 1, 2) {
		return 0, "", "", false
	}
	var format int
	_ = json.Unmarshal(m["ledger_format"], &format)
	ledger, valid := favoritesTransferHash(m["ledger_revision"])
	if !valid || ledger == favoritesRecoveryAbsentRevision && format != 2 {
		return 0, "", "", false
	}
	backend := ""
	if !favoritesRecoveryNull(m["backend_proof"]) {
		backend, valid = favoritesTransferHash(m["backend_proof"])
	}
	return format, ledger, backend, valid
}

func validFavoritesRecoveryResult(m map[string]json.RawMessage, runtime string, r *favoritesRecoveryRequest) bool {
	if r == nil || !inspectEnum(m["mode"], r.Mode) {
		return false
	}
	if r.Mode == "acknowledge" {
		if r.AfterLedgerSHA == favoritesRecoveryAbsentRevision || !favoritesTransferFields(m, "version", "runtime", "op", "mode", "operation_id", "acknowledgement_id", "plan_sha256", "acknowledged", "persistence", "ledger_revision", "record") ||
			!inspectEnum(m["operation_id"], r.OperationID) || !inspectEnum(m["acknowledgement_id"], r.AcknowledgementID) || !inspectEnum(m["plan_sha256"], r.PlanSHA) ||
			!bytes.Equal(bytes.TrimSpace(m["acknowledged"]), []byte("true")) || !inspectEnum(m["persistence"], "persistent") || !inspectEnum(m["ledger_revision"], r.AfterLedgerSHA) {
			return false
		}
		row := parseFavoritesRecoveryRecord(m["record"])
		return row != nil && row.Audit != nil && row.OperationID == r.OperationID && sameFavoritesTransferScope(row.Scope, r.Scope) && row.EntrySHA == r.EntrySHA &&
			row.Audit.Runtime == runtime && row.Audit.AcknowledgementID == r.AcknowledgementID && row.Audit.LedgerRevision == r.LedgerRevision && row.Audit.BackendProof == r.BackendProof
	}
	format, ledger, backend, valid := favoritesRecoveryReadBinding(m)
	if !valid {
		return false
	}
	if r.Mode == "list" {
		if !favoritesTransferFields(m, "version", "runtime", "op", "mode", "ledger_format", "ledger_revision", "backend_proof", "records") {
			return false
		}
		rows, valid := inspectArray(m["records"], 32)
		if !valid || ledger == favoritesRecoveryAbsentRevision && len(rows) != 0 {
			return false
		}
		previous := ""
		for _, raw := range rows {
			row := parseFavoritesRecoveryRecord(raw)
			if row == nil || row.OperationID <= previous || format == 1 && row.Audit != nil {
				return false
			}
			previous = row.OperationID
		}
		return true
	}
	if r.Mode != "inspect" || !favoritesTransferFields(m, "version", "runtime", "op", "mode", "operation_id", "ledger_format", "ledger_revision", "backend_proof", "record", "acknowledge_allowed", "plan_sha256", "acknowledgement_id", "after_ledger_sha256") || !inspectEnum(m["operation_id"], r.OperationID) {
		return false
	}
	var row *favoritesRecoveryRecord
	if !favoritesRecoveryNull(m["record"]) {
		row = parseFavoritesRecoveryRecord(m["record"])
		if row == nil || row.OperationID != r.OperationID || ledger == favoritesRecoveryAbsentRevision || format == 1 && row.Audit != nil {
			return false
		}
	}
	if bytes.Equal(bytes.TrimSpace(m["acknowledge_allowed"]), []byte("false")) {
		return favoritesRecoveryNull(m["plan_sha256"]) && favoritesRecoveryNull(m["acknowledgement_id"]) && favoritesRecoveryNull(m["after_ledger_sha256"])
	}
	if !bytes.Equal(bytes.TrimSpace(m["acknowledge_allowed"]), []byte("true")) || format != 2 || row == nil || row.Audit != nil || backend == "" {
		return false
	}
	after, a := favoritesTransferHash(m["after_ledger_sha256"])
	acknowledgement, b := favoritesTransferHash(m["acknowledgement_id"])
	plan, p := favoritesTransferHash(m["plan_sha256"])
	return a && b && p && after != favoritesRecoveryAbsentRevision && after != ledger &&
		acknowledgement == favoritesRecoveryAcknowledgement(runtime, row.OperationID, row.Scope, row.EntrySHA, ledger, backend) &&
		plan == favoritesRecoveryPlan(runtime, row.OperationID, row.Scope, row.EntrySHA, ledger, backend, acknowledgement, after)
}
