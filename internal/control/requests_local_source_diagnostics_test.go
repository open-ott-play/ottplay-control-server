package control

import (
	"encoding/json"
	"testing"
)

// The optional producer DTO must not change generic status transport for older
// controllers/consumers. The explicit CLI diagnostic mode owns DTO validation.
func TestLocalSourceDiagnosticsStatusTransportStaysOpaque(t *testing.T) {
	for name, status := range map[string]json.RawMessage{
		"legacy":                   []byte(`{"provider":"offline","ready":true}`),
		"optional-v1":              []byte(`{"provider":"offline","local_source_diagnostics":{"version":1,"runtime":"page-one","capturedAt":1700000000000,"consistent":true,"generation":7,"handleId":2,"currentSource":{"kind":"empty","itemId":null,"sourceId":null,"endpointMatch":"not_applicable"},"requestedSource":{"kind":"direct","itemId":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","sourceId":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","endpointMatch":"match"},"mediaErrorCode":null,"codecs":{"h264":"probably","hevc":"maybe"}}}`),
		"unknown-future-extension": []byte(`{"provider":"offline","local_source_diagnostics":{"version":2,"future_field":"synthetic"}}`),
	} {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"status","params":{}}`)
			body, err := json.Marshal(map[string]any{"id": id, "status": "ok", "data": status})
			if err != nil {
				t.Fatal(err)
			}
			expect(t, request(s, "POST", "/api/responses", secondToken, string(body), nil), 404)
			if len(s.devices[0].queue) != 1 {
				t.Fatal("another device consumed the status request")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, string(body), nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != string(body) {
				t.Fatal("generic status transport changed or discarded optional metadata")
			}
		})
	}
}
