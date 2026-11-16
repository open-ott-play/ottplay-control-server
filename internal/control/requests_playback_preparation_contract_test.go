package control

import (
	"encoding/json"
	"os"
	"testing"
)

func TestPlaybackPreparationSharedSyntheticContract(t *testing.T) {
	raw, err := os.ReadFile("../../contracts/playback-preparation-diagnostics-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Version        int                        `json:"version"`
		Synthetic      bool                       `json:"synthetic"`
		Action         string                     `json:"action"`
		Capability     map[string]json.RawMessage `json:"capability"`
		RuntimePattern string                     `json:"runtime_pattern"`
		Request        json.RawMessage            `json:"request"`
		Phases         []string                   `json:"phases"`
		Failures       []string                   `json:"failures"`
		Causes         []string                   `json:"causes"`
		Errors         []string                   `json:"errors"`
		Limits         map[string]int64           `json:"limits"`
		Example        json.RawMessage            `json:"example"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Version != 1 || !fixture.Synthetic || fixture.Action != "playback_preparation_diagnostics" || fixture.RuntimePattern != "[A-Za-z0-9_.-]{1,96}" ||
		len(fixture.Phases) != 8 || len(fixture.Failures) != 5 || len(fixture.Causes) != 21 || len(fixture.Errors) != 5 ||
		fixture.Limits["receipt_bytes"] != maxPlaybackPreparationOuterBytes || fixture.Limits["attempt_generation"] != inspectMaxInteger || fixture.Limits["failures"] != 4294967295 || fixture.Limits["retry_after_ms"] != 30000 {
		t.Fatal("incomplete or incompatible shared preparation fixture")
	}
	if !validPlaybackPreparationCapability(fixture.Capability[fixture.Action]) {
		t.Fatal("shared capability marker refused")
	}
	expected := parsePlaybackPreparationRequest(transferObject(t, fixture.Request))
	if expected == nil || !validPlaybackPreparationResult(fixture.Example, "ok", expected) {
		t.Fatal("shared request/example refused")
	}
	fresh := func() map[string]any {
		t.Helper()
		var m map[string]any
		if err := json.Unmarshal(fixture.Example, &m); err != nil {
			t.Fatal(err)
		}
		return m
	}
	for field, values := range map[string][]string{"phase": fixture.Phases, "last_failure": fixture.Failures, "cause": fixture.Causes} {
		for _, value := range values {
			t.Run(field+"/"+value, func(t *testing.T) {
				m := fresh()
				m[field] = value
				if !validPlaybackPreparationResult(transferJSON(t, m), "ok", expected) {
					t.Fatal("shared enum refused")
				}
			})
		}
	}
	for _, reason := range fixture.Errors {
		t.Run("error/"+reason, func(t *testing.T) {
			runtime := expected.Runtime
			if reason == "runtime_mismatch" {
				runtime = "receiver_current.2"
			}
			m := map[string]any{"version": 1, "runtime": runtime, "error": reason}
			if !validPlaybackPreparationResult(transferJSON(t, m), "rejected", expected) {
				t.Fatal("shared negative enum refused")
			}
		})
	}
	m := fresh()
	m["attempt_generation"] = fixture.Limits["attempt_generation"]
	m["retry"].(map[string]any)["failures"] = fixture.Limits["failures"]
	m["retry"].(map[string]any)["retry_after_ms"] = fixture.Limits["retry_after_ms"]
	if !validPlaybackPreparationResult(transferJSON(t, m), "ok", expected) {
		t.Fatal("shared maximum limits refused")
	}
	// Run the shared request/example through the real authenticated HTTP relay.
	s := newTestServer(t)
	body := `{"action":"` + fixture.Action + `","params":` + string(fixture.Request) + `}`
	id := rpcID(t, s, body)
	response := screenshotEnvelope(id, "ok", fixture.Example)
	expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	if got.Code != 200 || got.Body.String() != response {
		t.Fatal("shared fixture changed in actual relay")
	}
}
