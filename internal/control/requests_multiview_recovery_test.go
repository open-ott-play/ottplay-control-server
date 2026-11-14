package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type multiviewRecoveryContract struct {
	Probe                        json.RawMessage            `json:"probe"`
	LegacyProbe                  json.RawMessage            `json:"legacy_probe"`
	Capabilities                 json.RawMessage            `json:"capabilities"`
	PositiveCapacityCapabilities json.RawMessage            `json:"positive_capacity_capabilities"`
	RecoveryMarkers              map[string]json.RawMessage `json:"recovery_markers"`
	Requests                     map[string]struct {
		Action string          `json:"action"`
		Params json.RawMessage `json:"params"`
	} `json:"requests"`
	Results       map[string]json.RawMessage `json:"results"`
	Rejections    map[string]json.RawMessage `json:"rejections"`
	InvalidProbes []struct {
		Name   string          `json:"name"`
		Params json.RawMessage `json:"params"`
	} `json:"invalid_probes"`
	InvalidCapabilities []struct {
		Name string          `json:"name"`
		Data json.RawMessage `json:"data"`
	} `json:"invalid_capabilities"`
	InvalidRequests []struct {
		Name    string `json:"name"`
		Request struct {
			Action string          `json:"action"`
			Params json.RawMessage `json:"params"`
		} `json:"request"`
	} `json:"invalid_requests"`
}

func multiviewRecoveryFixture(t testing.TB) multiviewRecoveryContract {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/multiview-recovery-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var f multiviewRecoveryContract
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	if len(f.Requests) != 3 || len(f.Results) != 3 || len(f.RecoveryMarkers) != 4 || len(f.InvalidCapabilities) < 10 {
		t.Fatal("incomplete recovery contract")
	}
	return f
}

func TestMultiviewRecoveryProbeNegotiation(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	for _, row := range f.InvalidProbes {
		t.Run(row.Name, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, "capabilities", row.Params, ""), nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("invalid negotiation reached queue")
			}
		})
	}
	for _, params := range []string{
		`{"multiview":true,"multiview_recovery_v1":null}`,
		`{"multiview":true,"multiview_recovery_v1":{}}`,
		`{"multiview":true,"multiview_recovery_v1":[]}`,
		`{"multiview":true,"multiview_recovery_v1":true,"multiview_recovery_v1":true}`,
		`{"multiview":true,"multiview":true,"multiview_recovery_v1":true}`,
	} {
		expect(t, request(newTestServer(t), "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":`+params+`}`, nil), 400)
	}
	s := newTestServer(t)
	id := rpcID(t, s, string(f.Probe))
	queued := s.devices[0].queue[0]
	if !queued.multiviewProbe || !queued.multiviewRecoveryProbe {
		t.Fatal("negotiation not bound to pending request")
	}
	wire := transferObject(t, queued.data)
	if string(wire["params"]) != "{}" {
		t.Fatal("relay opt-in sent to legacy player")
	}
	body := transferResponse(t, id, "ok", f.Capabilities)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	if !s.devices[0].results[id].multiviewRecoveryProbe || !s.devices[0].results[id].multiviewProbe {
		t.Fatal("retained receipt lost negotiation")
	}
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, got, 200)
	if got.Body.String() != body {
		t.Fatal("opted-in capability payload rewritten")
	}
}

func TestMultiviewRecoveryProjectionIsRequestBound(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	for _, probe := range []string{`{"action":"capabilities","params":{}}`, string(f.LegacyProbe), string(f.Probe)} {
		t.Run(probe, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, probe)
			opted := strings.Contains(probe, "multiview_recovery_v1")
			body := transferResponse(t, id, "ok", f.Capabilities)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			retained := append([]byte(nil), s.devices[0].results[id].data...)
			got := transferObject(t, retained)
			data := transferObject(t, got["data"])
			_, hasRecovery := data["multiview_recovery"]
			if hasRecovery != opted || s.devices[0].results[id].multiviewRecoveryProbe != opted {
				t.Fatal("recovery disclosure did not follow request opt-in")
			}
			original := transferObject(t, f.Capabilities)
			if !opted {
				delete(original, "multiview_recovery")
			}
			if !reflect.DeepEqual(multiviewObject(transferJSON(t, data)), multiviewObject(transferJSON(t, original))) {
				t.Fatal("projection changed legacy capability fields")
			}
			if s.resultBytes != len(retained) {
				t.Fatal("storage budget did not use retained bytes")
			}
			// A duplicate is validated using retained flags and cannot restore a
			// stripped marker or replace the original receipt.
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			bad := transferObject(t, f.Capabilities)
			bad["multiview_recovery"] = json.RawMessage(`{"version":1}`)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, bad)), nil), 400)
			if string(s.devices[0].results[id].data) != string(retained) || len(s.devices[0].queue) != 0 {
				t.Fatal("duplicate changed retained result")
			}
		})
	}
	// Concurrent probes for one device must not share mutable negotiation state.
	s := newTestServer(t)
	oldID := rpcID(t, s, string(f.LegacyProbe))
	newID := rpcID(t, s, string(f.Probe))
	for _, id := range []string{newID, oldID} {
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.Capabilities), nil), 200)
		data := transferObject(t, transferObject(t, s.devices[0].results[id].data)["data"])
		if _, exists := data["multiview_recovery"]; exists != (id == newID) {
			t.Fatal("concurrent probe inherited another request's opt-in")
		}
	}
}

func TestMultiviewRecoveryAbsentMarkerPreservesLegacy(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	data := transferObject(t, f.Capabilities)
	delete(data, "multiview_recovery")
	for _, payload := range []json.RawMessage{transferJSON(t, data), json.RawMessage(`{}`)} {
		for _, probe := range []string{string(f.Probe), string(f.LegacyProbe), `{"action":"capabilities","params":{}}`} {
			s := newTestServer(t)
			id := rpcID(t, s, probe)
			body := transferResponse(t, id, "ok", payload) + " \n"
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			if string(s.devices[0].results[id].data) != body {
				t.Fatal("absent marker manufactured or legacy bytes changed")
			}
		}
	}
}

func TestMultiviewRecoveryCapabilityClosedSchema(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	// Operation arrays are sets: retained cleanup does not depend on their order.
	unordered := transferObject(t, f.RecoveryMarkers["all"])
	unordered["operations"] = json.RawMessage(`["off","stop","status"]`)
	f.RecoveryMarkers["reordered"] = transferJSON(t, unordered)
	for _, caps := range []json.RawMessage{f.Capabilities, f.PositiveCapacityCapabilities} {
		data := transferObject(t, caps)
		for name, marker := range f.RecoveryMarkers {
			t.Run(name+"/"+string(data["multiview"]), func(t *testing.T) {
				data["multiview_recovery"] = marker
				s := newTestServer(t)
				id := rpcID(t, s, string(f.Probe))
				expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, data)), nil), 200)
			})
		}
	}
	for _, row := range f.InvalidCapabilities {
		for _, probe := range []string{string(f.Probe), string(f.LegacyProbe), `{"action":"capabilities","params":{}}`} {
			t.Run(row.Name+"/"+probe, func(t *testing.T) {
				s := newTestServer(t)
				id := rpcID(t, s, probe)
				expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", row.Data), nil), 400)
				if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
					t.Fatal("malformed capability consumed request")
				}
			})
		}
	}
	base := transferObject(t, f.Capabilities)
	for _, marker := range []string{
		`null`, `[]`, `{}`,
		`{"version":1e0,"runtime":"native-runtime-1","operations":["status"],"off_modes":[]}`,
		`{"version":1,"version":1,"runtime":"native-runtime-1","operations":["status"],"off_modes":[]}`,
		`{"version":1,"runtime":"native-runtime-1","operations":["status"],"off_modes":[],"slots":[]}`,
		`{"version":1,"runtime":"native-runtime-1","operations":["status","off"],"off_modes":["close_only","close_only"]}`,
		`{"version":1,"runtime":"native-runtime-1","operations":null,"off_modes":[]}`,
		`{"version":1,"runtime":"https://private.invalid/token","operations":["status"],"off_modes":[]}`,
	} {
		s := newTestServer(t)
		id := rpcID(t, s, string(f.Probe))
		base["multiview_recovery"] = json.RawMessage(marker)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, base)), nil), 400)
	}
}

func TestMultiviewRecoveryRawLimitsBeforeProjectionAndAfterRetention(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	for _, probe := range []string{string(f.Probe), string(f.LegacyProbe), `{"action":"capabilities","params":{}}`} {
		s := newTestServer(t)
		id := rpcID(t, s, probe)
		body := transferResponse(t, id, "ok", f.Capabilities)
		padded := body + strings.Repeat(" ", maxMultiviewResultBytes-len(body))
		duplicate := strings.Replace(body, `"multiview_recovery":`, `"multiview_recovery":{},"multiview_recovery":`, 1)
		if duplicate == body {
			t.Fatal("duplicate-key fixture did not mutate")
		}
		for _, bad := range []string{padded + " ", duplicate, strings.Replace(body, `"native-runtime-1"`, `"\ud800"`, 1)} {
			expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, padded, nil), 200)
		for _, bad := range []string{padded + " ", duplicate} {
			expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
		}
	}
	// Negotiated probes retain the raw bound on every outer status even when
	// the player sends no marker; negative replies cannot evade negotiation.
	for _, status := range []string{"rejected", "unsupported"} {
		s := newTestServer(t)
		id := rpcID(t, s, string(f.Probe))
		body := transferResponse(t, id, status, json.RawMessage(`{}`))
		padded := body + strings.Repeat(" ", maxMultiviewResultBytes-len(body))
		expect(t, request(s, "POST", "/api/responses", firstToken, padded+" ", nil), 400)
		expect(t, request(s, "POST", "/api/responses", firstToken, padded, nil), 200)
		expect(t, request(s, "POST", "/api/responses", firstToken, padded+" ", nil), 400)
	}
}

func TestMultiviewRecoveryProjectionCannotExceedEnvelopeBudget(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	for _, probe := range []string{string(f.LegacyProbe), `{"action":"capabilities","params":{}}`} {
		t.Run(probe, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, probe)
			bodyWithMetadata := func(count int) string {
				caps := transferObject(t, f.Capabilities)
				caps["metadata"] = transferJSON(t, strings.Repeat("<", count))
				// JSON permits literal '<'. The response projection's default
				// encoder escapes it to six bytes, including unrelated metadata.
				return strings.ReplaceAll(transferResponse(t, id, "ok", transferJSON(t, caps)), `\u003c`, "<")
			}
			large := bodyWithMetadata(50000)
			projected := transferObject(t, []byte(large))
			caps := transferObject(t, projected["data"])
			delete(caps, "multiview_recovery")
			projected["data"] = transferJSON(t, caps)
			if len(large) >= maxMultiviewResultBytes || len(transferJSON(t, projected)) <= maxMultiviewResultBytes {
				t.Fatal("fixture must grow beyond the complete envelope budget only after projection")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, large, nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("oversized projected response consumed or retained pending request")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, bodyWithMetadata(10000), nil), 200)
			retained := append([]byte(nil), s.devices[0].results[id].data...)
			if len(retained) > maxMultiviewResultBytes || s.resultBytes != len(retained) {
				t.Fatal("retained projected envelope exceeded its budget")
			}
			data := transferObject(t, transferObject(t, retained)["data"])
			if settingString(data["metadata"]) != strings.Repeat("<", 10000) {
				t.Fatal("projection changed legitimate metadata")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, large, nil), 400)
			if string(s.devices[0].results[id].data) != string(retained) || s.resultBytes != len(retained) {
				t.Fatal("oversized duplicate changed the original receipt")
			}
		})
	}
	// Opted-in requests do not serialize a projection: their original bounded
	// bytes and unrelated metadata remain unchanged.
	s := newTestServer(t)
	id := rpcID(t, s, string(f.Probe))
	caps := transferObject(t, f.Capabilities)
	caps["metadata"] = transferJSON(t, strings.Repeat("<", 50000))
	body := strings.ReplaceAll(transferResponse(t, id, "ok", transferJSON(t, caps)), `\u003c`, "<")
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	if string(s.devices[0].results[id].data) != body {
		t.Fatal("opted-in response changed despite needing no projection")
	}
}

func TestMultiviewRecoveryRequestsKeepClosedCAS(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	for _, row := range f.InvalidRequests {
		t.Run(row.Name, func(t *testing.T) {
			if parseMultiviewRequest(row.Request.Params) != nil {
				t.Fatal("malformed recovery parsed")
			}
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, row.Request.Action, row.Request.Params, ""), nil), 400)
			if len(s.devices[0].queue) != 0 {
				t.Fatal("malformed recovery queued")
			}
		})
	}
	for _, raw := range []string{
		strings.Replace(string(f.Requests["stop_slot8"].Params), `"recovery_version": 1`, `"recovery_version": 1e0`, 1),
		strings.Replace(string(f.Requests["stop_slot8"].Params), `"recovery_version": 1`, `"recovery_version": 1,"recovery_version":1`, 1),
	} {
		if raw == string(f.Requests["stop_slot8"].Params) || parseMultiviewRequest(json.RawMessage(raw)) != nil {
			t.Fatal("strict recovery integer/duplicate test invalid or accepted")
		}
	}
	legacy := multiviewFixture(t)
	for name, req := range legacy.Requests {
		if name == "stop" || name == "off" || name == "off_restore" {
			if parseMultiviewRequest(req.Params) == nil {
				t.Fatal("legacy mutation changed")
			}
			continue
		}
		m := transferObject(t, req.Params)
		m["recovery_version"] = json.RawMessage(`1`)
		if parseMultiviewRequest(transferJSON(t, m)) != nil {
			t.Fatal("recovery widened non-cleanup operation")
		}
	}
}

func TestMultiviewRecoveryZeroAdmissionRetainedSlot8Receipts(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	for name, input := range f.Requests {
		for _, status := range []string{"ok", "rejected", "unsupported"} {
			t.Run(name+"/"+status, func(t *testing.T) {
				s := newTestServer(t)
				capID := rpcID(t, s, string(f.Probe))
				expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, capID, "ok", f.Capabilities), nil), 200)
				q := parseMultiviewRequest(input.Params)
				if q == nil {
					t.Fatal("shared recovery request rejected")
				}
				id := rpcID(t, s, globalRPC(t, input.Action, input.Params, ""))
				data := f.Results[name]
				if status != "ok" {
					data = f.Rejections[name]
					if status == "unsupported" {
						m := transferObject(t, data)
						m["reason"] = json.RawMessage(`"unsupported"`)
						data = transferJSON(t, m)
					}
				}
				for _, field := range []string{"runtime", "binding"} {
					bad := transferObject(t, data)
					bad[field] = json.RawMessage(`"stale"`)
					expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, status, transferJSON(t, bad)), nil), 400)
				}
				if name != "status" {
					for _, mutate := range []func(map[string]json.RawMessage){
						func(m map[string]json.RawMessage) { delete(m, "recovery_version") },
						func(m map[string]json.RawMessage) { m["recovery_version"] = json.RawMessage(`1.0`) },
						func(m map[string]json.RawMessage) {
							m["expected"] = json.RawMessage(`{"scene":"new","layout_revision":"layout-1","focus_revision":"focus-1","audio_revision":"audio-1"}`)
						},
					} {
						bad := transferObject(t, data)
						binding := transferObject(t, bad["binding"])
						mutate(binding)
						bad["binding"] = transferJSON(t, binding)
						expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, status, transferJSON(t, bad)), nil), 400)
					}
				}
				body := transferResponse(t, id, status, data)
				expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
				wrongRuntime := transferObject(t, data)
				wrongRuntime["runtime"] = json.RawMessage(`"new-runtime"`)
				expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, status, transferJSON(t, wrongRuntime)), nil), 400)
				if name != "status" {
					missingRecovery := transferObject(t, data)
					binding := transferObject(t, missingRecovery["binding"])
					delete(binding, "recovery_version")
					missingRecovery["binding"] = transferJSON(t, binding)
					expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, status, transferJSON(t, missingRecovery)), nil), 400)
				}
				if !reflect.DeepEqual(s.devices[0].results[id].multiview, q) || string(s.devices[0].results[id].data) != body {
					t.Fatal("recovery receipt lost original CAS")
				}
				if len(s.devices[0].queue) != 0 {
					t.Fatal("terminal result caused another cleanup request")
				}
			})
		}
	}
}

func TestMultiviewRecoveryUsesExistingPermissions(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	for _, name := range []string{"stop_slot8", "off_close_only"} {
		input := f.Requests[name]
		op := parseMultiviewRequest(input.Params).Op
		for _, allowed := range []bool{false, true} {
			c := testConfig()
			token := strings.Repeat("q", 32)
			hash := sha256.Sum256([]byte(token))
			action := "multiview.status"
			if allowed {
				action = "multiview." + op
			}
			c.Operators = []config.CommandOperator{{ID: "scoped", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{action}}}
			s, err := New(c)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = s.Close() })
			body := globalRPC(t, input.Action, input.Params, "")
			want := 403
			if allowed {
				want = 202
			}
			expect(t, request(s, "POST", "/api/requests?device_id=first", token, body, nil), want)
			expect(t, request(s, "POST", "/api/requests?device_id=second", token, body, nil), 403)
			if allowed && s.devices[0].queue[0].permission != action {
				t.Fatal("cleanup bypassed existing permission")
			}
		}
	}
}

func TestMultiviewRecoveryLostReceiptIsNotReplayed(t *testing.T) {
	f := multiviewRecoveryFixture(t)
	for _, name := range []string{"stop_slot8", "off_close_only"} {
		t.Run(name, func(t *testing.T) {
			c := journalConfig(t)
			s, err := New(c)
			if err != nil {
				t.Fatal(err)
			}
			key := "recovery-unknown-0001"
			input := f.Requests[name]
			body := globalRPC(t, input.Action, input.Params, key)
			id := rpcID(t, s, body)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
			if len(s.devices[0].queue) != 1 {
				t.Fatal("pending retry made a second mutation")
			}
			changed := transferObject(t, input.Params)
			changed["runtime"] = json.RawMessage(`"new-runtime"`)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, input.Action, transferJSON(t, changed), key), nil), 409)
			if err := s.Close(); err != nil {
				t.Fatal(err)
			}
			disk, err := os.ReadFile(c.Journal.Path)
			if err != nil {
				t.Fatal(err)
			}
			for _, private := range []string{"native-runtime-1", "scene-1", "slot-8-generation-1", "expected", "binding"} {
				if strings.Contains(string(disk), private) {
					t.Fatal("private recovery binding persisted")
				}
			}
			s, err = New(c)
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			outcome := operationValue(t, s, key)
			if outcome["id"] != id || outcome["state"] != "unknown" {
				t.Fatal("lost receipt became a confirmed outcome")
			}
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
			if len(s.devices[0].queue) != 0 || len(s.devices[0].results) != 0 {
				t.Fatal("unknown cleanup replayed after restart")
			}
		})
	}
}
