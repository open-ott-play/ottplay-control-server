package control

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestAspectNegativeResultsAcceptOnlyBoundErrorCodes(t *testing.T) {
	for _, operation := range []string{"get", "set"} {
		for _, status := range []string{"rejected", "unsupported"} {
			for _, code := range []string{"invalid_request", "runtime_mismatch", "restricted", "unsupported", "unavailable"} {
				t.Run(operation+"/"+status+"/"+code, func(t *testing.T) {
					s := newTestServer(t)
					id := rpcID(t, s, aspectRequestFixture(operation))
					data := aspectNegativeResultFixture(operation)
					data["error"] = code
					body := screenshotEnvelope(id, status, data)
					expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
					expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
					result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
					expect(t, result, 200)
					if result.Body.String() != body || len(s.devices[0].queue) != 0 || s.bytes != 0 || s.resultBytes != len(body) {
						t.Fatal("bound negative aspect response was changed or stored twice")
					}
				})
			}
		}
	}
}

func TestAspectNegativeResultsRejectUnboundOrMalformedResponsesIncludingReplays(t *testing.T) {
	for _, operation := range []string{"get", "set"} {
		valid := aspectResultJSON(t, aspectNegativeResultFixture(operation))
		invalid := []string{`null`, `[]`, `{}`, `true`, `"unsupported"`, `{"error":"unsupported"}`}
		mutations := map[string][]any{
			"version":   {nil, true, false, 0, 2, 1.5, "1", []any{}, map[string]any{}},
			"runtime":   {nil, true, 1, "", "page-stale", "PAGE-123", []any{}, map[string]any{}},
			"operation": {nil, true, 1, "", "status", "GET", []any{}, map[string]any{}},
			"error":     {nil, true, 1, "", "UNSUPPORTED", "protected_ui", "raw private exception", []any{}, map[string]any{}},
			"accepted":  {false},
			"extra":     {nil},
		}
		if operation == "get" {
			mutations["operation"] = append(mutations["operation"], "set")
			mutations["mode"] = []any{nil, "fit", "fill"}
		} else {
			mutations["operation"] = append(mutations["operation"], "get")
			mutations["mode"] = []any{nil, true, 1, "", "fit", "FILL", "stretch", []any{}, map[string]any{}}
		}
		for key, values := range mutations {
			for _, value := range values {
				data := aspectNegativeResultFixture(operation)
				data[key] = value
				invalid = append(invalid, aspectResultJSON(t, data))
			}
		}
		for key, value := range aspectNegativeResultFixture(operation) {
			data := aspectNegativeResultFixture(operation)
			delete(data, key)
			invalid = append(invalid, aspectResultJSON(t, data))
			data[strings.ToUpper(key)] = value
			invalid = append(invalid, aspectResultJSON(t, data))
			field, _ := json.Marshal(value)
			invalid = append(invalid, strings.TrimSuffix(valid, "}")+`,"`+key+`":`+string(field)+`}`)
		}
		for _, version := range []string{"1.0", "1e0", "1.0000000000000001"} {
			invalid = append(invalid, strings.Replace(valid, `"version":1`, `"version":`+version, 1))
		}
		for _, status := range []string{"rejected", "unsupported"} {
			for _, data := range invalid {
				t.Run(operation+"/"+status+"/"+data, func(t *testing.T) {
					s := newTestServer(t)
					id := rpcID(t, s, aspectRequestFixture(operation))
					badBody := `{"id":"` + id + `","status":"` + status + `","data":` + data + `}`
					expect(t, request(s, "POST", "/api/responses", firstToken, badBody, nil), 400)
					if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || s.resultBytes != 0 {
						t.Fatal("unbound or malformed negative aspect response consumed request")
					}
					goodBody := `{"id":"` + id + `","status":"` + status + `","data":` + valid + `}`
					expect(t, request(s, "POST", "/api/responses", firstToken, goodBody, nil), 200)
					expect(t, request(s, "POST", "/api/responses", firstToken, badBody, nil), 400)
					expect(t, request(s, "POST", "/api/responses", firstToken, goodBody, nil), 200)
					result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
					expect(t, result, 200)
					if result.Body.String() != goodBody || len(s.devices[0].queue) != 0 || s.bytes != 0 || s.resultBytes != len(goodBody) {
						t.Fatal("invalid negative replay changed stored response or queue")
					}
				})
			}
		}
	}
}

func TestAspectSuccessfulResultRejectsStaleNegativeReplays(t *testing.T) {
	for _, operation := range []string{"get", "set"} {
		for _, status := range []string{"rejected", "unsupported"} {
			t.Run(operation+"/"+status, func(t *testing.T) {
				s := newTestServer(t)
				id := rpcID(t, s, aspectRequestFixture(operation))
				body := screenshotEnvelope(id, "ok", aspectResultFixture(operation))
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
				stale := aspectNegativeResultFixture(operation)
				stale["runtime"] = "page-stale"
				for _, data := range []any{map[string]any{"error": "unsupported"}, stale} {
					expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, data), nil), 400)
				}
				result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
				expect(t, result, 200)
				if result.Body.String() != body || len(s.devices[0].queue) != 0 || s.resultBytes != len(body) {
					t.Fatal("stale negative response changed stored success")
				}
			})
		}
	}
}
