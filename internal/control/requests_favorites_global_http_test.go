package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func globalRPC(t *testing.T, action string, params json.RawMessage, key string) string {
	t.Helper()
	body := map[string]any{"action": action, "params": params}
	if key != "" {
		body["idempotency_key"] = key
	}
	return string(transferJSON(t, body))
}

type globalHTTPFixture struct {
	action, op     string
	params, result json.RawMessage
}

func globalHTTPFixtures(t *testing.T) []globalHTTPFixture {
	t.Helper()
	var result []globalHTTPFixture
	requests, results := globalTestRequests(t), globalTestResults(t)
	for _, op := range []string{"describe", "export", "begin", "append", "preview", "commit", "status", "abort"} {
		result = append(result, globalHTTPFixture{"favorites_global", op, requests[op], results[op]})
	}
	f := readGlobalRecoveryFixtures(t)
	for _, pair := range [][3]string{{"inspect", "page_request", "page_response"}, {"inspect", "inspect_request", "inspect_response"}, {"acknowledge", "acknowledge_request", "acknowledge_response"}} {
		result = append(result, globalHTTPFixture{"favorites_global_recovery", pair[0], f.Positive[pair[1]], f.Positive[pair[2]]})
	}
	return result
}
func TestGlobalFavoritesHTTPDescriptorsAndRetainedResults(t *testing.T) {
	for _, f := range globalHTTPFixtures(t) {
		t.Run(f.action+"/"+f.op, func(t *testing.T) {
			s := newTestServer(t)
			body := globalRPC(t, f.action, f.params, "")
			expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
			id := rpcID(t, s, body)
			queued := s.devices[0].queue[0]
			if queued.permission != f.action+"."+f.op || queued.favoritesTransfer != nil {
				t.Fatal("wrong permission or legacy descriptor")
			}
			if f.action == "favorites_global" {
				if queued.favoritesGlobal == nil || queued.favoritesGlobal.Op != f.op || !validFavoritesGlobalEnvelope(queued.data) {
					t.Fatal("missing core descriptor/envelope")
				}
			} else {
				if queued.favoritesGlobalRecovery == nil || queued.favoritesGlobalRecovery.Op != f.op || !validFavoritesGlobalRecoveryEnvelope(queued.data) {
					t.Fatal("missing recovery descriptor/envelope")
				}
			}
			response := transferResponse(t, id, "ok", f.result)
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			stale := transferObject(t, f.result)
			stale["runtime"] = transferJSON(t, "stale")
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, stale)), nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("stale response consumed pending request")
			}
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			}
			saved := s.devices[0].results[id]
			if (f.action == "favorites_global" && saved.favoritesGlobal == nil) || (f.action == "favorites_global_recovery" && saved.favoritesGlobalRecovery == nil) {
				t.Fatal("completed descriptor lost")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, stale)), nil), 400)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response || s.bytes != 0 {
				t.Fatal("retained raw response changed")
			}
		})
	}
}
func TestGlobalFavoritesHTTPNegativeResponsesBoundBeforeConsumption(t *testing.T) {
	for _, f := range globalHTTPFixtures(t) {
		for _, status := range []string{"rejected", "unsupported"} {
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, f.action, f.params, ""))
			params := transferObject(t, f.params)
			errorResult := map[string]any{"version": 1, "runtime": favoritesGlobalString(params["runtime"]), "op": f.op, "subject": "global", "error": "runtime_changed"}
			raw := transferJSON(t, errorResult)
			for field, value := range map[string]any{"runtime": "stale", "op": "unrelated", "subject": "scoped", "error": "private message", "outcome": "verified"} {
				bad := transferObject(t, raw)
				bad[field] = transferJSON(t, value)
				expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, status, transferJSON(t, bad)), nil), 400)
			}
			if len(s.devices[0].queue) != 1 {
				t.Fatal("invalid negative result consumed request")
			}
			response := transferResponse(t, id, status, raw)
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			}
			if string(s.devices[0].results[id].data) != response {
				t.Fatal("negative result was not retained")
			}
		}
	}
}
func TestGlobalFavoritesHTTPPermissionsOwnerAndDeviceIsolation(t *testing.T) {
	c := testConfig()
	operator := func(id, token string, actions []string) config.CommandOperator {
		h := sha256.Sum256([]byte(token))
		return config.CommandOperator{ID: id, CredentialSHA256: hex.EncodeToString(h[:]), DeviceIDs: []string{"first"}, Actions: actions}
	}
	legacy, reader, other := strings.Repeat("l", 32), strings.Repeat("m", 32), strings.Repeat("n", 32)
	readActions := []string{"favorites_global.describe", "favorites_global.export", "favorites_global.status", "favorites_global_recovery.inspect"}
	c.Operators = []config.CommandOperator{operator("legacy", legacy, []string{"favorites.list", "favorites_transfer.snapshot", "favorites_transfer.recovery"}), operator("reader", reader, readActions), operator("other", other, readActions)}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, f := range globalHTTPFixtures(t) {
		body := globalRPC(t, f.action, f.params, "")
		expect(t, request(s, "POST", "/api/requests?device_id=first", legacy, body, nil), 403)
		expect(t, request(s, "POST", "/api/requests?device_id=second", reader, body, nil), 403)
		permitted := f.op == "describe" || f.op == "export" || f.op == "status" || f.op == "inspect"
		if !permitted {
			expect(t, request(s, "POST", "/api/requests?device_id=first", reader, body, nil), 403)
			continue
		}
		accepted := request(s, "POST", "/api/requests?device_id=first", reader, body, nil)
		expect(t, accepted, 202)
		var parsed map[string]string
		_ = json.Unmarshal(accepted.Body.Bytes(), &parsed)
		id := parsed["id"]
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.result), nil), 200)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, reader, "", nil), 200)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
		permission := f.action + "." + f.op
		delete(s.operators[1].actions, permission)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, reader, "", nil), 403)
		s.operators[1].actions[permission] = true
	}
}
func TestGlobalFavoritesHTTPCapabilitiesAreIndependentAndOptional(t *testing.T) {
	for _, raw := range []string{`{}`, `{"favorites_global":{"version":1}}`, `{"favorites_global_recovery":{"version":1}}`, `{"favorites_global":{"version":1},"favorites_global_recovery":{"version":1}}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", json.RawMessage(raw)), nil), 200)
	}
	for _, marker := range []string{"favorites_global", "favorites_global_recovery"} {
		for _, raw := range []string{`null`, `{"version":true}`, `{"version":1.0}`, `{"version":1,"version":1}`, `{"version":1,"write":true}`} {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
			body := `{"` + marker + `":` + raw + `}`
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", json.RawMessage(body)), nil), 400)
			if len(s.devices[0].queue) != 1 {
				t.Fatal("invalid capability consumed request")
			}
		}
	}
}
func TestGlobalFavoritesHTTPActualEnvelopeLimitBeforeJournal(t *testing.T) {
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	now := time.Unix(1800000000, 0)
	s.now = func() time.Time { return now }
	raw := globalTestRequests(t)["append"]
	chunk := bytes.Repeat([]byte{0xff}, maxFavoritesTransferChunkBytes)
	encoded := base64.StdEncoding.EncodeToString(chunk)
	sum := sha256.Sum256(chunk)
	key := "global-boundary-0001"
	body := ""
	for escaped := 0; escaped <= len(encoded); escaped++ {
		p := transferObject(t, raw)
		p["data_base64"] = json.RawMessage(`"` + strings.Replace(encoded, "/", `\u002f`, escaped) + `"`)
		p["chunk_sha256"] = transferJSON(t, hex.EncodeToString(sum[:]))
		candidate := globalRPC(t, "favorites_global", transferJSON(t, p), key)
		if len(candidate) > maxFavoritesTransferEnvelopeBytes {
			break
		}
		queued := transferObject(t, json.RawMessage(candidate))
		delete(queued, "idempotency_key")
		queued["id"] = transferJSON(t, strings.Repeat("0", 32))
		queued["expires_at"] = transferJSON(t, float64(now.Add(s.ttl).UnixNano())/1e9)
		if len(transferJSON(t, queued)) > maxFavoritesTransferEnvelopeBytes {
			body = candidate
			break
		}
	}
	if body == "" {
		t.Fatal("boundary fixture not reached")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 413)
	if len(s.devices[0].queue) != 0 || len(s.journal.records) != 0 || s.bytes != 0 {
		t.Fatal("oversize envelope changed journal/queue")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, "favorites_global", raw, key), nil), 202)
}
func TestGlobalFavoritesHTTPRawLimitsDuplicatesAndExpiry(t *testing.T) {
	for _, f := range globalHTTPFixtures(t) {
		s := newTestServer(t)
		id := rpcID(t, s, globalRPC(t, f.action, f.params, ""))
		// Preserve raw whitespace inside data: Marshal would hide the tested bound.
		padded := `{"id":"` + id + `","status":"ok","data":` + string(f.result[:len(f.result)-1]) + strings.Repeat(" ", maxFavoritesTransferMetadataBytes) + `}}`
		expect(t, request(s, "POST", "/api/responses", firstToken, padded, nil), 400)
		duplicate := `{"id":"` + id + `","id":"` + id + `","status":"ok","data":` + string(f.result) + `}`
		expect(t, request(s, "POST", "/api/responses", firstToken, duplicate, nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("invalid raw response consumed request")
		}
		s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.result), nil), 404)
	}
	for _, action := range []string{"favorites_global", "favorites_global_recovery"} {
		s := newTestServer(t)
		raw := `{"action":"` + action + `","params":{"runtime":"r","op":"inspect","op":"status","subject":"global","cursor":null}}`
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, raw, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("duplicate request keys accepted")
		}
	}
}
func TestGlobalFavoritesHTTPUnknownMutationsNeverReplayAfterRestart(t *testing.T) {
	f := readGlobalRecoveryFixtures(t)
	for _, row := range []globalHTTPFixture{{action: "favorites_global", params: globalTestRequests(t)["commit"]}, {action: "favorites_global_recovery", params: f.Positive["acknowledge_request"]}} {
		c := journalConfig(t)
		s, err := New(c)
		if err != nil {
			t.Fatal(err)
		}
		key := "global-no-replay-0001"
		body := globalRPC(t, row.action, row.params, key)
		id := rpcID(t, s, body)
		if rpcID(t, s, body) != id || len(s.devices[0].queue) != 1 {
			t.Fatal("same payload was enqueued twice")
		}
		changed := transferObject(t, row.params)
		if row.action == "favorites_global" {
			changed["plan_digest"] = transferJSON(t, strings.Repeat("a", 64))
		} else {
			q := globalRecoveryBound(t, row.params)
			q.AfterLedger = strings.Repeat("a", 64)
			q.Plan = globalRecoveryPlan(q)
			changed["after_ledger_sha256"] = transferJSON(t, q.AfterLedger)
			changed["plan_sha256"] = transferJSON(t, q.Plan)
		}
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, row.action, transferJSON(t, changed), key), nil), 409)
		if err = s.Close(); err != nil {
			t.Fatal(err)
		}
		s, err = New(c)
		if err != nil {
			t.Fatal(err)
		}
		if operationValue(t, s, key)["state"] != "unknown" {
			t.Fatal("restart invented a known outcome")
		}
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("uncertain mutation replayed after restart")
		}
		if err = s.Close(); err != nil {
			t.Fatal(err)
		}
	}
}
