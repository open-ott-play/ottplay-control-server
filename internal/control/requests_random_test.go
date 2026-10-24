package control

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestRandomSelectionContractAndResults(t *testing.T) {
	data, err := os.ReadFile("../../contracts/random-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]json.RawMessage
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"request", "search_request", "range_request", "protected_request"} {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, string(fixture[name]))
			if s.devices[0].queue[0].randomPlay == nil {
				t.Fatal("random selector lost at enqueue")
			}
			bad := strings.Replace(string(fixture["result"]), `"number": 23`, `"number": 1001`, 1)
			if name == "range_request" {
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", json.RawMessage(bad)), nil), 400)
			}
			response := screenshotEnvelope(id, "ok", fixture["result"])
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			if len(s.devices[0].queue) != 0 || s.devices[0].results[id].randomPlay == nil {
				t.Fatal("random result lost validation state")
			}
		})
	}
	s := newTestServer(t)
	_ = rpcID(t, s, string(fixture["legacy_request"]))
	if s.devices[0].queue[0].randomPlay != nil {
		t.Fatal("legacy selector changed")
	}
}

func TestRandomSelectionRejectsMalformedUnionBeforeQueue(t *testing.T) {
	for _, params := range []string{
		`{"catalog":"source","id":"a","random":{}}`, `{"random":{}}`,
		`{"catalog":"source","random":null}`, `{"catalog":"source","random":[]}`,
		`{"catalog":"https://example.test","random":{}}`,
		`{"catalog":"source","random":{"search":""}}`,
		`{"catalog":"source","random":{"search":"x\n"}}`,
		`{"catalog":"source","random":{"search":"x\u0085"}}`,
		`{"catalog":"source","random":{"search":"x","search":"y"}}`,
		`{"catalog":"source","random":{"mode":"safe"}}`,
		`{"catalog":"source","random":{"mode":null}}`,
		`{"catalog":"source","random":{"unknown":true}}`,
		`{"catalog":"source","random":{"range":[0,1]}}`,
		`{"catalog":"source","random":{"range":[2,1]}}`,
		`{"catalog":"source","random":{"range":[1,9007199254740992]}}`,
		`{"catalog":"source","random":{"range":[1.0,2]}}`,
		`{"catalog":"source","random":{"range":[1e0,2]}}`,
		`{"catalog":"source","random":{"range":[null,2]}}`,
		`{"catalog":"source","random":{"range":[true,2]}}`,
		`{"catalog":"source","random":{"range":[1]}}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"play_catalog","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid random selection queued")
		}
	}
}

func TestRandomSelectionLargeNumbersDoNotDependOnNativeIntWidth(t *testing.T) {
	for _, number := range []string{"2147483648", "9007199254740991"} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"play_catalog","params":{"catalog":"source","random":{"range":[`+number+`,`+number+`]}}}`)
		result := json.RawMessage(`{"dispatched":true,"channel":{"id":"a","number":` + number + `,"name":"One"}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", result), nil), 200)
	}
}

func TestRandomSelectionRejectsUnsafeAcknowledgements(t *testing.T) {
	for _, result := range []string{
		`{"dispatched":false,"channel":{"id":"a","number":1,"name":"One"}}`,
		`{"dispatched":true,"channel":{"id":"a","number":1,"name":"One","url":"private"}}`,
		`{"dispatched":true,"channel":{"id":"a","number":1.5,"name":"One"}}`,
		`{"dispatched":true,"channel":{"id":"a","number":1,"name":"bad\nname"}}`,
		`{"dispatched":true,"channel":{"id":"a","number":1,"name":"One"},"token":"private"}`,
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"play_catalog","params":{"catalog":"source","random":{}}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", json.RawMessage(result)), nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("invalid random result consumed request")
		}
	}
}
