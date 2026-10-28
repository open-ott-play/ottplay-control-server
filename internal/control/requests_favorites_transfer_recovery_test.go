package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
)

type favoritesRecoveryFixture struct {
	Request json.RawMessage `json:"request"`
	Result  json.RawMessage `json:"result"`
}

func recoveryFixture(t *testing.T, name string, empty bool) favoritesRecoveryFixture {
	t.Helper()
	path := "../../docs/design/favorites-recovery-v1.fixtures.json"
	if empty {
		path = "../../docs/design/favorites-recovery-v1-empty.fixtures.json"
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixtures map[string]json.RawMessage
	var fixture favoritesRecoveryFixture
	if json.Unmarshal(raw, &fixtures) != nil || json.Unmarshal(fixtures[name], &fixture) != nil || len(fixture.Request) == 0 || len(fixture.Result) == 0 {
		t.Fatal("invalid frozen recovery fixture", name)
	}
	return fixture
}

func TestFavoritesRecoveryFrozenVectorsAndAbsentLedger(t *testing.T) {
	for _, name := range []string{"list", "inspect", "acknowledge", "legacy_inspect", "inspect_after_lost_ack"} {
		fixture := recoveryFixture(t, name, false)
		bound := transferBound(t, fixture.Request)
		if bound.Recovery == nil || !validFavoritesTransferResult(fixture.Result, "ok", bound) {
			t.Fatal("accepted recovery packet failed", name)
		}
	}
	for _, name := range []string{"list", "inspect"} {
		fixture := recoveryFixture(t, name, true)
		if !validFavoritesTransferResult(fixture.Result, "ok", transferBound(t, fixture.Request)) {
			t.Fatal("absent store must be readable without initialization", name)
		}
	}
	digest := sha256.Sum256([]byte("null"))
	if hex.EncodeToString(digest[:]) != favoritesRecoveryAbsentRevision {
		t.Fatal("absent ledger commitment drift")
	}
	ack := transferBound(t, recoveryFixture(t, "acknowledge", false).Request).Recovery
	if ack.AcknowledgementID != "55912213208c18aa2f6d79d2d6e0f651d23149743c0980c980c29ee8718f8049" || ack.PlanSHA != "be5edc795727bf375fc837d0cb785ceeeaaaa2d5d87f6b2f81160ffd8078ae22" || ack.AfterLedgerSHA != "9531840379de3f4e631f80b81bc8905fa3192c106e158d7e552fd508ca58b0bc" {
		t.Fatal("deterministic JS/Python/Go hash vector drift")
	}
}

func TestFavoritesRecoveryExactRequestsBindAllConfirmationFields(t *testing.T) {
	for _, mode := range []string{"list", "inspect", "acknowledge"} {
		fixture := recoveryFixture(t, mode, false)
		for key := range transferObject(t, fixture.Request) {
			changed := transferObject(t, fixture.Request)
			delete(changed, key)
			if parseFavoritesTransferRequest(changed) != nil {
				t.Fatal("missing request field admitted", mode, key)
			}
		}
		changed := transferObject(t, fixture.Request)
		changed["private_store"] = json.RawMessage(`"must-not-relay"`)
		if parseFavoritesTransferRequest(changed) != nil {
			t.Fatal("unknown private field admitted", mode)
		}
	}
	fixture := recoveryFixture(t, "acknowledge", false)
	for key, values := range map[string][]string{
		"runtime": {`"another-runtime"`}, "operation_id": {`"55555555-5555-4555-8555-555555555555"`, `null`},
		"mode": {`"inspect"`, `"reset"`}, "reason": {`"confirmed-success"`, `null`},
		"accept_unknown":           {`false`, `1`, `null`, `"true"`},
		"original_entry_sha256":    {`"` + strings.Repeat("0", 64) + `"`},
		"expected_ledger_revision": {`"` + favoritesRecoveryAbsentRevision + `"`, `"` + strings.Repeat("0", 64) + `"`},
		"backend_proof":            {`null`, `"` + strings.Repeat("0", 64) + `"`},
		"acknowledgement_id":       {`"55555555-5555-4555-8555-555555555555"`, `"` + strings.Repeat("0", 64) + `"`},
		"after_ledger_sha256":      {`"` + strings.Repeat("0", 64) + `"`},
		"plan_sha256":              {`"` + strings.Repeat("0", 64) + `"`},
	} {
		for _, value := range values {
			changed := transferObject(t, fixture.Request)
			changed[key] = json.RawMessage(value)
			if parseFavoritesTransferRequest(changed) != nil {
				t.Fatal("changed acknowledgement commitment admitted", key, value)
			}
		}
	}
	// Recovery can name a removed original provider without widening old ops.
	changed := transferObject(t, fixture.Request)
	scope := transferObject(t, changed["scope"])
	scope["provider"] = json.RawMessage(`"retired/plex-source"`)
	changed["scope"] = transferJSON(t, scope)
	s := parseFavoritesRecoveryScope(changed["scope"])
	if s == nil || parseFavoritesTransferScope(changed["scope"]) != nil {
		t.Fatal("original-scope handling widened the frozen transfer parser")
	}
	if parseFavoritesTransferRequest(changed) != nil {
		t.Fatal("scope substitution was not bound by hashes")
	}
}

func TestFavoritesRecoveryInspectionCannotInventEligibilityOrEvidence(t *testing.T) {
	fixture := recoveryFixture(t, "inspect", false)
	bound := transferBound(t, fixture.Request)
	for key, values := range map[string][]string{
		"runtime": {`"stale-runtime"`}, "mode": {`"list"`}, "operation_id": {`"55555555-5555-4555-8555-555555555555"`},
		"ledger_format": {`1`, `1.0`, `3`, `null`}, "ledger_revision": {`"` + strings.Repeat("0", 64) + `"`},
		"backend_proof": {`null`, `"` + strings.Repeat("0", 64) + `"`}, "record": {`null`},
		"acknowledge_allowed": {`false`, `1`, `null`}, "acknowledgement_id": {`null`, `"` + strings.Repeat("0", 64) + `"`},
		"after_ledger_sha256": {`null`, `"` + strings.Repeat("0", 64) + `"`}, "plan_sha256": {`null`, `"` + strings.Repeat("0", 64) + `"`},
	} {
		for _, value := range values {
			changed := transferObject(t, fixture.Result)
			changed[key] = json.RawMessage(value)
			if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
				t.Fatal("unbound/unsupported inspect plan admitted", key, value)
			}
		}
	}
	for _, key := range []string{"acknowledge_allowed", "plan_sha256", "acknowledgement_id", "after_ledger_sha256"} {
		legacy := recoveryFixture(t, "legacy_inspect", false)
		changed := transferObject(t, legacy.Result)
		changed[key] = transferObject(t, fixture.Result)[key]
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("legacy row acquired an acknowledgement plan", key)
		}
	}
	missing := recoveryFixture(t, "inspect", true)
	changed := transferObject(t, missing.Result)
	changed["record"] = transferObject(t, fixture.Result)["record"]
	if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
		t.Fatal("null storage digest claimed a fenced record")
	}
}

func TestFavoritesRecoveryAcknowledgementKeepsOriginalOutcomeAndExactAfterImage(t *testing.T) {
	fixture := recoveryFixture(t, "acknowledge", false)
	bound := transferBound(t, fixture.Request)
	for key, value := range map[string]string{
		"runtime": `"foreign-runtime"`, "mode": `"inspect"`, "operation_id": `"55555555-5555-4555-8555-555555555555"`,
		"acknowledgement_id": `"` + strings.Repeat("0", 64) + `"`, "plan_sha256": `"` + strings.Repeat("0", 64) + `"`,
		"ledger_revision": `"` + bound.Recovery.LedgerRevision + `"`, "acknowledged": `false`, "persistence": `"session-only"`, "outcome": `"verified"`,
	} {
		changed := transferObject(t, fixture.Result)
		changed[key] = json.RawMessage(value)
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("unbound acknowledgement accepted", key)
		}
	}
	for key, value := range map[string]string{"original_state": `"verified"`, "original_outcome": `"not_applied"`, "original_entry_sha256": `"` + strings.Repeat("0", 64) + `"`, "acknowledgement": `null`} {
		changed := transferObject(t, fixture.Result)
		row := transferObject(t, changed["record"])
		row[key] = json.RawMessage(value)
		changed["record"] = transferJSON(t, row)
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("original outcome/evidence was rewritten", key)
		}
	}
	for key, value := range map[string]string{"runtime": `"foreign-runtime"`, "reason": `"success"`, "acknowledged_at_ms": `123`, "plan_sha256": `"` + bound.Recovery.PlanSHA + `"`, "ledger_revision_before": `"` + strings.Repeat("0", 64) + `"`} {
		changed := transferObject(t, fixture.Result)
		row := transferObject(t, changed["record"])
		audit := transferObject(t, row["acknowledgement"])
		audit[key] = json.RawMessage(value)
		row["acknowledgement"] = transferJSON(t, audit)
		changed["record"] = transferJSON(t, row)
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("mutable/foreign/circular audit accepted", key)
		}
	}
}

func TestFavoritesRecoveryLostAckReadSurvivesRuntimeAndUnrelatedLedgerChange(t *testing.T) {
	fixture := recoveryFixture(t, "inspect_after_lost_ack", false)
	params := transferObject(t, fixture.Request)
	params["runtime"] = json.RawMessage(`"new-read-only-runtime"`)
	result := transferObject(t, fixture.Result)
	result["runtime"] = params["runtime"]
	result["ledger_revision"] = transferJSON(t, strings.Repeat("e", 64))
	if !validFavoritesTransferResult(transferJSON(t, result), "ok", transferBound(t, transferJSON(t, params))) {
		t.Fatal("fresh status could not observe retained old-runtime audit")
	}
	row := transferObject(t, result["record"])
	audit := transferObject(t, row["acknowledgement"])
	audit["runtime"] = params["runtime"]
	row["acknowledgement"] = transferJSON(t, audit)
	result["record"] = transferJSON(t, row)
	if validFavoritesTransferResult(transferJSON(t, result), "ok", transferBound(t, transferJSON(t, params))) {
		t.Fatal("historical audit was relabeled as current-runtime acknowledgement")
	}
}

func TestFavoritesRecoveryCannotPlanAnAbsentAfterLedgerWithValidHashes(t *testing.T) {
	fixture := recoveryFixture(t, "acknowledge", false)
	bound := transferBound(t, fixture.Request)
	r := bound.Recovery
	// Recompute the entire public plan: rejecting a stale hash alone would not
	// detect the impossible claim that appending an audit removes the ledger.
	plan := favoritesRecoveryPlan(bound.Runtime, r.OperationID, r.Scope, r.EntrySHA, r.LedgerRevision, r.BackendProof, r.AcknowledgementID, favoritesRecoveryAbsentRevision)
	params := transferObject(t, fixture.Request)
	params["after_ledger_sha256"] = transferJSON(t, favoritesRecoveryAbsentRevision)
	params["plan_sha256"] = transferJSON(t, plan)
	if parseFavoritesTransferRequest(params) != nil {
		t.Fatal("valid hashes authorized a null after-image acknowledgement")
	}
	inspection := recoveryFixture(t, "inspect", false)
	result := transferObject(t, inspection.Result)
	result["after_ledger_sha256"] = transferJSON(t, favoritesRecoveryAbsentRevision)
	result["plan_sha256"] = transferJSON(t, plan)
	if validFavoritesTransferResult(transferJSON(t, result), "ok", transferBound(t, inspection.Request)) {
		t.Fatal("eligible inspection promised a null after-image")
	}
	// Also fence a malformed retained descriptor: an ACK with a real record may
	// never claim physical null, regardless of a matching public commitment.
	r.AfterLedgerSHA, r.PlanSHA = favoritesRecoveryAbsentRevision, plan
	result = transferObject(t, fixture.Result)
	result["ledger_revision"] = transferJSON(t, favoritesRecoveryAbsentRevision)
	result["plan_sha256"] = transferJSON(t, plan)
	if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("acknowledgement containing a record claimed an absent ledger")
	}
	for _, mode := range []string{"list", "inspect"} {
		empty := recoveryFixture(t, mode, true)
		if !validFavoritesTransferResult(empty.Result, "ok", transferBound(t, empty.Request)) {
			t.Fatal("absent-ledger read semantics regressed", mode)
		}
	}
}

func TestFavoritesRecoveryListIsBoundedUniqueAndComplete(t *testing.T) {
	fixture := recoveryFixture(t, "list", false)
	bound := transferBound(t, fixture.Request)
	result := transferObject(t, fixture.Result)
	rows, _ := inspectArray(result["records"], 32)
	original := rows[0]
	rows = nil
	for i := 0; i < 32; i++ {
		row := transferObject(t, original)
		row["operation_id"] = transferJSON(t, fmt.Sprintf("33333333-3333-4333-8333-%012x", i))
		rows = append(rows, transferJSON(t, row))
	}
	result["records"] = transferJSON(t, rows)
	if !validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("bounded 32-row list rejected")
	}
	for _, changedRows := range [][]json.RawMessage{append(append([]json.RawMessage{}, rows...), original), {rows[0], rows[0]}, {rows[1], rows[0]}} {
		result["records"] = transferJSON(t, changedRows)
		if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
			t.Fatal("oversized/duplicate/unsorted list admitted")
		}
	}
	result["records"] = json.RawMessage(`null`)
	if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("missing list masqueraded as complete")
	}
}

func TestFavoritesRecoveryNegativeMetadataAndCapabilityRemainStrict(t *testing.T) {
	fixture := recoveryFixture(t, "acknowledge", false)
	bound := transferBound(t, fixture.Request)
	for _, code := range []string{"busy", "unsupported_persistence", "revision_conflict", "policy_denied", "storage_unavailable"} {
		negative := transferJSON(t, map[string]any{"version": 1, "runtime": bound.Runtime, "op": "recovery", "error": code})
		if !validFavoritesTransferResult(negative, "rejected", bound) || validFavoritesTransferResult(negative, "ok", bound) {
			t.Fatal("negative status semantics changed", code)
		}
		if validFavoritesTransferResult(append(negative, []byte(strings.Repeat(" ", maxFavoritesTransferMetadataBytes))...), "rejected", bound) {
			t.Fatal("oversized raw negative metadata admitted")
		}
	}
	for _, code := range []string{"outcome_unknown", "transaction_not_found", "reconciliation_required", "private detail"} {
		negative := transferJSON(t, map[string]any{"version": 1, "runtime": bound.Runtime, "op": "recovery", "error": code})
		if validFavoritesTransferResult(negative, "rejected", bound) {
			t.Fatal("unfrozen recovery error admitted", code)
		}
	}
	if !validFavoritesRecoveryCapability(json.RawMessage(`{"version":1}`)) {
		t.Fatal("independent recovery marker rejected")
	}
	for _, raw := range []string{`null`, `{}`, `{"version":1.0}`, `{"version":true}`, `{"version":2}`, `{"version":1,"enabled":true}`, `{"version":1,"version":1}`} {
		if validFavoritesRecoveryCapability(json.RawMessage(raw)) {
			t.Fatal("unknown/coerced/duplicate capability accepted", raw)
		}
	}
}
