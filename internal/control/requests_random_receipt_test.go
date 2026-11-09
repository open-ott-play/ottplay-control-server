package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const receiptRandomRequest = `{"action":"play_catalog","params":{"catalog":"channel-page-123","receipt_random":{"version":2,"scope":"receipt"}}}`

func TestReceiptRandomSharedContractAndRetainedValidation(t *testing.T) {
	body, err := os.ReadFile("../../contracts/random-receipt-v2.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]json.RawMessage
	if err := json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	s := newTestServer(t)
	id := rpcID(t, s, string(fixture["request"]))
	if bound := s.devices[0].queue[0].randomPlay; bound == nil || !bound.ReceiptScoped {
		t.Fatal("receipt selector lost at enqueue")
	}
	poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
	expect(t, poll, 200)
	if !strings.Contains(poll.Body.String(), id) || !strings.Contains(poll.Body.String(), `"receipt_random"`) {
		t.Fatal("receipt request missing from acknowledged poll")
	}
	var queued map[string]json.RawMessage
	_ = json.Unmarshal(s.devices[0].queue[0].data, &queued)
	params, _ := decodeObject(queued["params"])
	if bound := parseRandomPlayRequest(params); bound == nil || !bound.ReceiptScoped {
		t.Fatal("receipt selector was rewritten before delivery")
	}
	response := screenshotEnvelope(id, "ok", fixture["result"])
	expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
	expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
	expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
	stored := s.devices[0].results[id]
	if stored.randomPlay == nil || !stored.randomPlay.ReceiptScoped || len(s.devices[0].queue) != 0 {
		t.Fatal("receipt selector lost after completion")
	}
	if string(stored.data) != response {
		t.Fatal("successful public acknowledgement changed")
	}
	invalid := screenshotEnvelope(id, "ok", json.RawMessage(`{"dispatched":true,"channel":{"id":"x","number":1,"name":"Video","url":"private"}}`))
	expect(t, request(s, "POST", "/api/responses", firstToken, invalid, nil), 400)
	if string(s.devices[0].results[id].data) != response {
		t.Fatal("malformed duplicate replaced accepted result")
	}
}

func TestReceiptRandomRejectsMalformedUnionBeforeQueue(t *testing.T) {
	for _, params := range []string{
		`{"catalog":"receipt","receipt_random":{}}`,
		`{"catalog":"receipt","receipt_random":null}`,
		`{"catalog":"receipt","receipt_random":[]}`,
		`{"catalog":"receipt","receipt_random":true}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":"receipt"},"id":"one"}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":"receipt"},"random":{}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":"receipt"},"extra":1}`,
		`{"receipt_random":{"version":2,"scope":"receipt"}}`,
		`{"catalog":null,"receipt_random":{"version":2,"scope":"receipt"}}`,
		`{"catalog":"https://private.invalid","receipt_random":{"version":2,"scope":"receipt"}}`,
		`{"catalog":"a","catalog":"b","receipt_random":{"version":2,"scope":"receipt"}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"version":2,"scope":"receipt"}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":"receipt","scope":"receipt"}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":"receipt","search":"x"}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":"receipt","range":[1,1000]}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":"receipt","mode":"adult"}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":"all"}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":null}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":2}}`,
		`{"catalog":"receipt","receipt_random":{"version":2,"scope":true}}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"play_catalog","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid receipt selection queued")
		}
	}
	for _, version := range []string{`null`, `true`, `"2"`, `1`, `3`, `2.0`, `2e0`, `-2`} {
		s := newTestServer(t)
		body := strings.Replace(receiptRandomRequest, `"version":2`, `"version":`+version, 1)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid receipt version queued")
		}
	}
}

func TestReceiptRandomErrorsAreClosedAndPrivateDetailsNeverRetained(t *testing.T) {
	for _, status := range []string{"rejected", "unsupported"} {
		s := newTestServer(t)
		id := rpcID(t, s, receiptRandomRequest)
		for _, raw := range []string{
			`null`, `{}`, `{"error":null}`, `{"error":true}`, `{"error":""}`,
			`{"error":"bad\nline"}`, `{"error":"bad\u0085line"}`,
			`{"error":"failure","url":"https://private.invalid/token"}`,
			`{"error":"failure","error":"failure"}`,
			`{"error":"` + strings.Repeat("x", 1025) + `"}`,
			`{"error":"` + strings.Repeat("x", 65537) + `"}`,
		} {
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, json.RawMessage(raw)), nil), 400)
			if len(s.devices[0].queue) != 1 {
				t.Fatal("invalid failure consumed request")
			}
		}
		private := json.RawMessage(`{"error":"Cannot open https://private.invalid/media?token=never-retain-this"}`)
		response := screenshotEnvelope(id, status, private)
		expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
		expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		expect(t, got, 200)
		if strings.Contains(got.Body.String(), "private.invalid") || strings.Contains(got.Body.String(), "never-retain-this") ||
			!strings.Contains(got.Body.String(), "Receipt-scoped playback was "+status) || len(s.devices[0].queue) != 0 {
			t.Fatal("private native failure retained or request did not complete")
		}
		badDuplicate := screenshotEnvelope(id, status, json.RawMessage(`{"error":"failure","private":"secret"}`))
		expect(t, request(s, "POST", "/api/responses", firstToken, badDuplicate, nil), 400)
	}
}

func TestReceiptRandomUsesExistingPlayCatalogOperatorScope(t *testing.T) {
	token := strings.Repeat("o", 32)
	sum := sha256.Sum256([]byte(token))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "remote", CredentialSHA256: hex.EncodeToString(sum[:]), DeviceIDs: []string{"first"}, Actions: []string{"play_catalog"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, receiptRandomRequest, nil), 202)
	expect(t, request(s, "POST", "/api/requests?device_id=second", token, receiptRandomRequest, nil), 403)
	s.operators[0].actions = map[string]bool{"status": true}
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, receiptRandomRequest, nil), 403)
}

func TestReceiptRandomResultBoundsMatchCategoryRowsWithoutChangingLegacy(t *testing.T) {
	for _, row := range []struct {
		number   int64
		name     string
		accepted bool
	}{
		{1, strings.Repeat("a", 512), true},
		{maxCategoryItems, "Last item", true},
		{maxCategoryItems + 1, "Beyond native catalog bound", false},
		{1, strings.Repeat("a", 513), false},
		{1, strings.Repeat("Ж", 256), true},
		{1, strings.Repeat("Ж", 257), false},
	} {
		data, _ := json.Marshal(map[string]any{"dispatched": true, "channel": map[string]any{"id": "item", "number": row.number, "name": row.name}})
		if got := validRandomPlayResult(data, "ok", &randomPlayRequest{ReceiptScoped: true}); got != row.accepted {
			t.Fatalf("native result number=%d name_bytes=%d accepted=%v, want %v", row.number, len(row.name), got, row.accepted)
		}
		if !validRandomPlayResult(data, "ok", &randomPlayRequest{}) {
			t.Fatal("legacy random result bound changed")
		}
	}
}
