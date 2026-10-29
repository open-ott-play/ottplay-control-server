package control

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

type globalRecoveryFixtures struct {
	Positive map[string]json.RawMessage `json:"positive"`
	Negative []struct {
		ID    string          `json:"id"`
		Value json.RawMessage `json:"value"`
	} `json:"negative"`
}

func readGlobalRecoveryFixtures(t *testing.T) globalRecoveryFixtures {
	t.Helper()
	raw, err := os.ReadFile("../../docs/design/global-favorites-recovery-v1.fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	var f globalRecoveryFixtures
	if err = json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	return f
}
func globalRecoveryBound(t *testing.T, raw json.RawMessage) *favoritesGlobalRecoveryRequest {
	t.Helper()
	q := parseFavoritesGlobalRecoveryRequest(raw)
	if q == nil {
		t.Fatal("valid recovery request rejected")
	}
	return q
}
func TestGlobalRecoveryPublishedFixtures(t *testing.T) {
	f := readGlobalRecoveryFixtures(t)
	for _, pair := range [][2]string{{"page_request", "page_response"}, {"page_request", "empty_page_response"}, {"page_request", "first_page"}, {"last_page_request", "last_page"}, {"inspect_request", "inspect_response"}, {"inspect_request", "missing_operation_response"}, {"acknowledge_request", "acknowledge_response"}} {
		t.Run(pair[1], func(t *testing.T) {
			if !validFavoritesGlobalRecoveryResult(f.Positive[pair[1]], "ok", globalRecoveryBound(t, f.Positive[pair[0]])) {
				t.Fatal("published result rejected")
			}
		})
	}
	// Historical audits use their own runtime, not the observing page runtime.
	observed := transferObject(t, f.Positive["later_runtime_observation"])
	req := transferObject(t, f.Positive["inspect_request"])
	req["runtime"] = observed["runtime"]
	if !validFavoritesGlobalRecoveryResult(f.Positive["later_runtime_observation"], "ok", globalRecoveryBound(t, transferJSON(t, req))) {
		t.Fatal("historical audit could not be reconciled")
	}
	for _, negative := range f.Negative {
		t.Run(negative.ID, func(t *testing.T) {
			switch negative.ID {
			case "missing-audit-success":
				if validFavoritesGlobalRecoveryResult(negative.Value, "ok", globalRecoveryBound(t, f.Positive["acknowledge_request"])) {
					t.Fatal("missing audit accepted")
				}
			case "partial-list":
				if validFavoritesGlobalRecoveryResult(negative.Value, "ok", globalRecoveryBound(t, f.Positive["last_page_request"])) {
					t.Fatal("incomplete continuation accepted")
				}
			case "changed-page-revision":
				if validFavoritesGlobalRecoveryResult(negative.Value, "ok", globalRecoveryBound(t, f.Positive["last_page_request"])) {
					t.Fatal("mixed page revisions accepted")
				}
			case "false-original-success":
				if parseGlobalRecoveryRecord(negative.Value) != nil {
					t.Fatal("uncertainty rewritten")
				}
			default:
				if parseFavoritesGlobalRecoveryRequest(negative.Value) != nil {
					t.Fatal("invalid acknowledgement accepted")
				}
			}
		})
	}
}
func TestGlobalRecoveryExactRequestsAndTupleBinding(t *testing.T) {
	f := readGlobalRecoveryFixtures(t)
	for _, key := range []string{"page_request", "inspect_request", "acknowledge_request"} {
		raw := f.Positive[key]
		extra := transferObject(t, raw)
		extra["path"] = transferJSON(t, "private")
		if parseFavoritesGlobalRecoveryRequest(transferJSON(t, extra)) != nil {
			t.Fatal("unknown request field admitted")
		}
		if parseFavoritesGlobalRecoveryRequest(append(append([]byte{}, raw...), []byte(" "+strings.Repeat(" ", maxFavoritesTransferEnvelopeBytes))...)) != nil {
			t.Fatal("raw request cap ignored")
		}
		for _, runtime := range []any{nil, true, "bad runtime", strings.Repeat("x", 65)} {
			bad := transferObject(t, raw)
			bad["runtime"] = transferJSON(t, runtime)
			if parseFavoritesGlobalRecoveryRequest(transferJSON(t, bad)) != nil {
				t.Fatal("invalid runtime admitted")
			}
		}
	}
	for _, field := range []string{"runtime", "operation_id", "operation_runtime", "original_entry_sha256", "expected_ledger_revision", "expected_store_revision", "expected_context_revision", "backend_proof", "acknowledgement_id", "after_ledger_sha256", "plan_sha256", "reason", "accept_unknown"} {
		bad := transferObject(t, f.Positive["acknowledge_request"])
		value := any(strings.Repeat("a", 64))
		switch field {
		case "runtime", "operation_runtime":
			value = "another-runtime"
		case "operation_id":
			value = globalTestOperation
		case "reason":
			value = "retry"
		case "accept_unknown":
			value = false
		}
		bad[field] = transferJSON(t, value)
		if parseFavoritesGlobalRecoveryRequest(transferJSON(t, bad)) != nil {
			t.Fatal("unbound confirmation field admitted", field)
		}
	}
	// Recompute valid digests around an impossible null/unchanged after-image.
	original := globalRecoveryBound(t, f.Positive["acknowledge_request"])
	for _, after := range []string{favoritesRecoveryAbsentRevision, original.Ledger} {
		q := *original
		q.AfterLedger = after
		q.Plan = globalRecoveryPlan(&q)
		bad := transferObject(t, f.Positive["acknowledge_request"])
		bad["after_ledger_sha256"] = transferJSON(t, after)
		bad["plan_sha256"] = transferJSON(t, q.Plan)
		if parseFavoritesGlobalRecoveryRequest(transferJSON(t, bad)) != nil {
			t.Fatal("recomputed impossible after-image admitted")
		}
		result := transferObject(t, f.Positive["inspect_response"])
		result["after_ledger_sha256"] = bad["after_ledger_sha256"]
		result["plan_sha256"] = bad["plan_sha256"]
		if validFavoritesGlobalRecoveryResult(transferJSON(t, result), "ok", globalRecoveryBound(t, f.Positive["inspect_request"])) {
			t.Fatal("impossible inspect plan admitted")
		}
	}
}
func TestGlobalRecoveryPageOrderingAndNoPartialCollection(t *testing.T) {
	f := readGlobalRecoveryFixtures(t)
	q := globalRecoveryBound(t, f.Positive["page_request"])
	first := transferObject(t, f.Positive["first_page"])
	var rows []json.RawMessage
	_ = json.Unmarshal(first["records"], &rows)
	for _, changed := range [][]json.RawMessage{rows[:15], append([]json.RawMessage{rows[1], rows[0]}, rows[2:]...), append([]json.RawMessage{rows[0], rows[0]}, rows[2:]...)} {
		bad := transferObject(t, f.Positive["first_page"])
		bad["records"] = transferJSON(t, changed)
		if validFavoritesGlobalRecoveryResult(transferJSON(t, bad), "ok", q) {
			t.Fatal("incomplete, unordered or duplicate page accepted")
		}
	}
	for _, field := range []string{"ledger_revision", "store_revision", "context_revision", "backend_proof"} {
		bad := transferObject(t, f.Positive["last_page"])
		bad[field] = transferJSON(t, strings.Repeat("a", 64))
		if validFavoritesGlobalRecoveryResult(transferJSON(t, bad), "ok", globalRecoveryBound(t, f.Positive["last_page_request"])) {
			t.Fatal("changed continuation context accepted", field)
		}
	}
	bad := transferObject(t, f.Positive["page_response"])
	bad["ledger_revision"] = transferJSON(t, favoritesRecoveryAbsentRevision)
	if validFavoritesGlobalRecoveryResult(transferJSON(t, bad), "ok", q) {
		t.Fatal("null ledger contained protected records")
	}
	// Unqualified readonly evidence is permitted, but cannot offer acknowledgement.
	bad = transferObject(t, f.Positive["inspect_response"])
	bad["backend_proof"] = json.RawMessage("null")
	bad["acknowledge_allowed"] = json.RawMessage("false")
	for _, field := range []string{"acknowledgement_id", "after_ledger_sha256", "plan_sha256"} {
		bad[field] = json.RawMessage("null")
	}
	if !validFavoritesGlobalRecoveryResult(transferJSON(t, bad), "ok", globalRecoveryBound(t, f.Positive["inspect_request"])) {
		t.Fatal("readonly unqualified observation rejected")
	}
}
func TestGlobalRecoveryResultLimitsAndErrorFencing(t *testing.T) {
	f := readGlobalRecoveryFixtures(t)
	for _, key := range []string{"page_request", "inspect_request", "acknowledge_request"} {
		q := globalRecoveryBound(t, f.Positive[key])
		base := map[string]any{"version": 1, "runtime": q.Runtime, "op": q.Op, "subject": "global", "error": "outcome_unknown"}
		raw := transferJSON(t, base)
		for _, status := range []string{"rejected", "unsupported"} {
			if !validFavoritesGlobalRecoveryResult(raw, status, q) {
				t.Fatal("correlated negative result rejected")
			}
			for field, value := range map[string]any{"runtime": "stale", "op": "commit", "subject": "scoped", "error": "private failure", "verified": true} {
				bad := transferObject(t, raw)
				bad[field] = transferJSON(t, value)
				if validFavoritesGlobalRecoveryResult(transferJSON(t, bad), status, q) {
					t.Fatal("unbound error accepted", field)
				}
			}
			padded := append(append([]byte{}, raw...), []byte(strings.Repeat(" ", maxFavoritesTransferMetadataBytes))...)
			if validFavoritesGlobalRecoveryResult(padded, status, q) {
				t.Fatal("raw error metadata cap ignored")
			}
		}
	}
	q := globalRecoveryBound(t, f.Positive["acknowledge_request"])
	for field, value := range map[string]any{"ledger_revision": favoritesRecoveryAbsentRevision, "store_revision": strings.Repeat("a", 64), "acknowledged": false, "verified": true, "record": nil} {
		bad := transferObject(t, f.Positive["acknowledge_response"])
		bad[field] = transferJSON(t, value)
		if validFavoritesGlobalRecoveryResult(transferJSON(t, bad), "ok", q) {
			t.Fatal("false acknowledgement admitted", field)
		}
	}
}
