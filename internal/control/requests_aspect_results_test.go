package control

import (
	"encoding/json"
	"strings"
	"testing"
)

func aspectResultFixture(operation string) map[string]any {
	data := map[string]any{"version": 1, "runtime": "page-123", "operation": operation, "mode": "fill"}
	if operation == "get" {
		data["saved_mode"], data["persisted"] = "fill", true
	} else {
		data["accepted"], data["dispatched"], data["effect"] = true, false, "aspect-after-ack"
	}
	return data
}

func aspectRequestFixture(operation string) string {
	params := `{"operation":"` + operation + `","runtime":"page-123"`
	if operation == "set" {
		params += `,"mode":"fill"`
	}
	return `{"action":"aspect","params":` + params + `}}`
}

func aspectResultJSON(t *testing.T, data map[string]any) string {
	t.Helper()
	raw, err := json.Marshal(data)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func TestAspectResultsRequireExactRequestBoundSchemasIncludingReplays(t *testing.T) {
	for _, operation := range []string{"get", "set"} {
		t.Run(operation, func(t *testing.T) {
			valid := aspectResultJSON(t, aspectResultFixture(operation))
			invalid := []string{`null`, `[]`, `{}`, `true`, `"fill"`}
			mutations := map[string][]any{
				"version":   {nil, true, false, 0, 2, 1.5, "1", []any{}, map[string]any{}},
				"runtime":   {nil, true, 1, "", "page-other", "PAGE-123", []any{}, map[string]any{}},
				"operation": {nil, true, 1, "", "status", "SET", []any{}, map[string]any{}},
				"mode":      {nil, true, 1, "", "FILL", "fill ", "stretch", "cover", "16:9", []any{}, map[string]any{}},
				"extra":     {nil},
			}
			if operation == "get" {
				mutations["operation"] = append(mutations["operation"], "set")
				mutations["saved_mode"] = []any{nil, true, false, 1, "", "fit", "stretch", []any{}, map[string]any{}}
				mutations["persisted"] = []any{nil, false, 0, 1, "true", []any{}, map[string]any{}}
				mutations["accepted"] = []any{true}
			} else {
				mutations["operation"] = append(mutations["operation"], "get")
				mutations["mode"] = append(mutations["mode"], "fit")
				mutations["accepted"] = []any{nil, false, 0, 1, "true", []any{}, map[string]any{}}
				mutations["dispatched"] = []any{nil, true, 0, 1, "false", []any{}, map[string]any{}}
				mutations["effect"] = []any{nil, true, 1, "", "aspect", "reload-after-ack", []any{}, map[string]any{}}
				mutations["persisted"] = []any{true}
			}
			for key, values := range mutations {
				for _, value := range values {
					data := aspectResultFixture(operation)
					data[key] = value
					invalid = append(invalid, aspectResultJSON(t, data))
				}
			}
			for key, value := range aspectResultFixture(operation) {
				data := aspectResultFixture(operation)
				delete(data, key)
				invalid = append(invalid, aspectResultJSON(t, data))
				data[strings.ToUpper(key)] = value
				invalid = append(invalid, aspectResultJSON(t, data))
				// Even duplicate fields with identical values are ambiguous.
				field, _ := json.Marshal(value)
				invalid = append(invalid, strings.TrimSuffix(valid, "}")+`,"`+key+`":`+string(field)+`}`)
			}
			for _, version := range []string{"1.0", "1e0", "1.0000000000000001"} {
				invalid = append(invalid, strings.Replace(valid, `"version":1`, `"version":`+version, 1))
			}
			for _, data := range invalid {
				t.Run(data, func(t *testing.T) {
					s := newTestServer(t)
					id := rpcID(t, s, aspectRequestFixture(operation))
					badBody := `{"id":"` + id + `","status":"ok","data":` + data + `}`
					expect(t, request(s, "POST", "/api/responses", firstToken, badBody, nil), 400)
					if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || s.resultBytes != 0 {
						t.Fatal("invalid aspect receipt consumed the request or result storage")
					}
					goodBody := `{"id":"` + id + `","status":"ok","data":` + valid + `}`
					expect(t, request(s, "POST", "/api/responses", firstToken, goodBody, nil), 200)
					// Stored request context must fence duplicate-response validation too.
					expect(t, request(s, "POST", "/api/responses", firstToken, badBody, nil), 400)
					expect(t, request(s, "POST", "/api/responses", firstToken, goodBody, nil), 200)
					result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
					expect(t, result, 200)
					if result.Body.String() != goodBody || len(s.devices[0].queue) != 0 || s.bytes != 0 || s.resultBytes != len(goodBody) {
						t.Fatal("duplicate aspect receipt changed the original response or requeued the request")
					}
				})
			}
		})
	}
}

func TestAspectGetResultPersistenceIsDerivedFromSavedMode(t *testing.T) {
	for _, mode := range []string{"fit", "fill"} {
		for _, savedMode := range []any{nil, "fit", "fill"} {
			data := aspectResultFixture("get")
			data["mode"], data["saved_mode"] = mode, savedMode
			persisted := savedMode != nil && savedMode == mode
			data["persisted"] = persisted
			t.Run(aspectResultJSON(t, data), func(t *testing.T) {
				s := newTestServer(t)
				id := rpcID(t, s, aspectRequestFixture("get"))
				data["persisted"] = !persisted
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
				data["persisted"] = persisted
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 200)
			})
		}
	}
}

func TestAspectNegativeResultKeepsGenericSchemaAndRequestContext(t *testing.T) {
	for _, operation := range []string{"get", "set"} {
		for _, status := range []string{"rejected", "unsupported"} {
			t.Run(operation+"/"+status, func(t *testing.T) {
				s := newTestServer(t)
				id := rpcID(t, s, aspectRequestFixture(operation))
				body := screenshotEnvelope(id, status, map[string]any{"error": "unsupported"})
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
				data := aspectResultFixture(operation)
				data["runtime"] = "page-other"
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
				result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
				expect(t, result, 200)
				if result.Body.String() != body || len(s.devices[0].queue) != 0 || s.resultBytes != len(body) {
					t.Fatal("negative aspect receipt was replaced or requeued")
				}
			})
		}
	}
}

func TestAspectInspectOperationPreservesExistingEvidenceContract(t *testing.T) {
	for _, tc := range []struct{ state, evidence string }{{"accepted", "none"}, {"invoked", "handler_completed"}} {
		t.Run(tc.state, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, inspectPayload("operation"))
			data := inspectData("operation")
			operation := data["data"].(map[string]any)
			operation["action"], operation["state"] = "aspect", tc.state
			evidence := map[string]any{"kind": tc.evidence, "generation": nil, "position": nil}
			operation["evidence"] = evidence
			evidence["mode"] = "fill"
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
			delete(evidence, "mode")
			body := screenshotEnvelope(id, "ok", data)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, result, 200)
			if result.Body.String() != body {
				t.Fatal("aspect journal receipt changed in transit")
			}
		})
	}
}
