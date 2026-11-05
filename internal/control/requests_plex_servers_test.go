package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type plexServersWireCase struct {
	ID            string          `json:"id"`
	Valid         bool            `json:"valid"`
	MarkerJSON    *string         `json:"marker_json"`
	RequestJSON   string          `json:"request_json"`
	RequestParams json.RawMessage `json:"request_params"`
	ResponseJSON  string          `json:"response_json"`
}

type plexServersWireFixture struct {
	ContractSHA256    string                `json:"contract_sha256"`
	ExpectedRequestID string                `json:"expected_request_id"`
	Capabilities      []plexServersWireCase `json:"capability_cases"`
	Requests          []plexServersWireCase `json:"request_cases"`
	Replies           []plexServersWireCase `json:"reply_cases"`
}

func plexServersFixture(t *testing.T) plexServersWireFixture {
	t.Helper()
	b, err := os.ReadFile("../../testdata/plex_servers_v1.json")
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(b)
	if hex.EncodeToString(sum[:]) != "f4e5d75babe9313e99139115d483e46cfb19763f3aea751361d8c75069ec20a2" {
		t.Fatal("shared wire fixture changed without a reviewed contract delta")
	}
	var f plexServersWireFixture
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	if f.ContractSHA256 != "96630fdc691b94bcf4b6e26324636dbbcccb5798a988eb34af5e01d3082d3464" ||
		len(f.Capabilities) != 23 || len(f.Requests) != 50 || len(f.Replies) != 105 {
		t.Fatal("incomplete or unexpected managed Plex contract")
	}
	return f
}

func (f plexServersWireFixture) reply(t *testing.T, id string) plexServersWireCase {
	t.Helper()
	for _, row := range f.Replies {
		if row.ID == id {
			return row
		}
	}
	t.Fatalf("missing shared reply %q", id)
	return plexServersWireCase{}
}

func TestPlexServersSharedCapabilities(t *testing.T) {
	for _, row := range plexServersFixture(t).Capabilities {
		t.Run(row.ID, func(t *testing.T) {
			var marker []byte
			if row.MarkerJSON != nil {
				marker = []byte(*row.MarkerJSON)
			}
			if validPlexServersCapability(marker) != row.Valid {
				t.Fatal("capability acceptance differs from frozen contract")
			}
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
			data := `{}`
			if row.MarkerJSON != nil {
				data = `{"plex_servers":` + *row.MarkerJSON + `}`
			}
			body := `{"id":"` + id + `","status":"ok","data":` + data + `}`
			code := 400
			if row.Valid || row.MarkerJSON == nil {
				code = 200
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), code)
			if code == 200 {
				got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
				expect(t, got, 200)
				if got.Body.String() != body {
					t.Fatal("relay manufactured or changed capability metadata")
				}
			} else if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("invalid capability consumed request")
			}
		})
	}
}

func TestPlexServersSharedRequests(t *testing.T) {
	for _, row := range plexServersFixture(t).Requests {
		t.Run(row.ID, func(t *testing.T) {
			s := newTestServer(t)
			got := request(s, "POST", "/api/requests?device_id=first", adminToken, row.RequestJSON, nil)
			code := 400
			if row.Valid {
				code = 202
			} else if len(row.RequestJSON) > MaxBodyBytes {
				code = 413
			}
			expect(t, got, code)
			if !row.Valid {
				if len(s.devices[0].queue) != 0 || s.bytes != 0 || len(s.devices[0].results) != 0 {
					t.Fatal("invalid request was dispatched or received invented typed data")
				}
				if strings.Contains(got.Body.String(), "plex_servers") || strings.Contains(got.Body.String(), "runtime") {
					t.Fatal("outer rejection reflected typed request data")
				}
				return
			}
			m, _ := decodeObject([]byte(row.RequestJSON))
			q := parsePlexServersRequest(m["params"])
			e := s.devices[0].queue[0]
			if q == nil || e.plexServers == nil || *e.plexServers != *q || e.permission != "plex_servers."+q.Op {
				t.Fatal("queue lost request identity or exact permission")
			}
		})
	}
}

func TestPlexServersSharedReplies(t *testing.T) {
	f := plexServersFixture(t)
	for _, row := range f.Replies {
		t.Run(row.ID, func(t *testing.T) {
			q := parsePlexServersRequest(row.RequestParams)
			if validPlexServersResultEnvelope([]byte(row.ResponseJSON), f.ExpectedRequestID, q) != row.Valid {
				t.Fatal("reply acceptance differs from frozen contract")
			}
			if q == nil {
				if row.Valid {
					t.Fatal("valid reply has invalid request")
				}
				return // Invalid original runtime/op must never reach the queue.
			}
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, "plex_servers", row.RequestParams, ""))
			// IDs are equally sized; preserve original whitespace, escaping and
			// duplicate fields so the HTTP path checks the original byte budget.
			body := strings.ReplaceAll(row.ResponseJSON, `"`+f.ExpectedRequestID+`"`, `"`+id+`"`)
			code := 400
			if row.Valid {
				code = 200
			} else if row.ID == "different-request-id" {
				code = 404
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), code)
			if !row.Valid {
				if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
					t.Fatal("invalid result consumed or stored request")
				}
				return
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			retained := s.devices[0].results[id]
			if retained.plexServers == nil || *retained.plexServers != *q || retained.permission != "plex_servers."+q.Op {
				t.Fatal("retained result lost original correlation")
			}
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != body || s.resultBytes != len(body) || s.bytes != 0 || len(s.devices[0].queue) != 0 {
				t.Fatal("raw result changed, duplicated or remained pending")
			}
		})
	}
}

func TestPlexServersRetainedCorrelationAndExpiry(t *testing.T) {
	f := plexServersFixture(t)
	row := f.reply(t, "select-applied")
	s := newTestServer(t)
	id := rpcID(t, s, globalRPC(t, "plex_servers", row.RequestParams, ""))
	body := strings.ReplaceAll(row.ResponseJSON, `"`+f.ExpectedRequestID+`"`, `"`+id+`"`)
	expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
	expect(t, request(s, "POST", "/api/responses", adminToken, body, nil), 403)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	for _, name := range []string{"select-other-context", "select-other-server", "select-new-runtime", "select-wrong-op", "error-with-new-runtime", "list-error-stale", "whole-reply-bytes-65537"} {
		bad := strings.ReplaceAll(f.reply(t, name).ResponseJSON, `"`+f.ExpectedRequestID+`"`, `"`+id+`"`)
		expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
	}
	if string(s.devices[0].results[id].data) != body || s.resultBytes != len(body) {
		t.Fatal("invalid later reply replaced first retained result")
	}
	// A later valid but different result must not rewrite the original receipt.
	later := strings.ReplaceAll(f.reply(t, "select-error-outcome_unknown").ResponseJSON, `"`+f.ExpectedRequestID+`"`, `"`+id+`"`)
	expect(t, request(s, "POST", "/api/responses", firstToken, later, nil), 200)
	if string(s.devices[0].results[id].data) != body {
		t.Fatal("later completion overwrote original result")
	}
	s.now = func() time.Time { return time.Now().Add(2 * time.Minute) }
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil), 404)
	if s.resultBytes != 0 {
		t.Fatal("expired result bytes were retained")
	}
	s = newTestServer(t)
	id = rpcID(t, s, globalRPC(t, "plex_servers", row.RequestParams, ""))
	s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
	expect(t, request(s, "POST", "/api/responses", firstToken, strings.ReplaceAll(row.ResponseJSON, `"`+f.ExpectedRequestID+`"`, `"`+id+`"`), nil), 404)
	if len(s.devices[0].results) != 0 {
		t.Fatal("expired pending request became a result")
	}
}

func TestPlexServersExactOperatorOwnership(t *testing.T) {
	f := plexServersFixture(t)
	for _, op := range []string{"list", "select"} {
		t.Run(op, func(t *testing.T) {
			c := testConfig()
			owner, other, legacy := strings.Repeat("q", 32), strings.Repeat("v", 32), strings.Repeat("z", 32)
			for _, row := range []struct {
				id, token string
				actions   []string
			}{{"owner", owner, []string{"plex_servers." + op}}, {"other", other, []string{"plex_servers." + op}},
				{"legacy", legacy, []string{"profile", "plex_library.list", "plex_library.play", "plex_queue.play"}}} {
				h := sha256.Sum256([]byte(row.token))
				c.Operators = append(c.Operators, config.CommandOperator{ID: row.id, CredentialSHA256: hex.EncodeToString(h[:]), DeviceIDs: []string{"first"}, Actions: row.actions})
			}
			s, err := New(c)
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			good, denied := f.reply(t, "list-one"), f.reply(t, "select-applied")
			if op == "select" {
				good, denied = denied, good
			}
			body := globalRPC(t, "plex_servers", good.RequestParams, "")
			for _, token := range []string{firstToken, legacy} {
				expect(t, request(s, "POST", "/api/requests?device_id=first", token, body, nil), 403)
			}
			expect(t, request(s, "POST", "/api/requests?device_id=second", owner, body, nil), 403)
			expect(t, request(s, "POST", "/api/requests?device_id=first", owner, globalRPC(t, "plex_servers", denied.RequestParams, ""), nil), 403)
			accepted := request(s, "POST", "/api/requests?device_id=first", owner, body, nil)
			expect(t, accepted, 202)
			var queued map[string]string
			if err := json.Unmarshal(accepted.Body.Bytes(), &queued); err != nil {
				t.Fatal(err)
			}
			id := queued["id"]
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
			expect(t, request(s, "POST", "/api/responses", firstToken, strings.ReplaceAll(good.ResponseJSON, `"`+f.ExpectedRequestID+`"`, `"`+id+`"`), nil), 200)
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, owner, "", nil), 200)
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
			// Retained access is still checked against the exact operation.
			s.operators[0].actions = map[string]bool{"plex_queue.play": true}
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, owner, "", nil), 403)
		})
	}
}

func TestPlexServersNoProbeOrLegacyDispatch(t *testing.T) {
	f := plexServersFixture(t)
	row := f.reply(t, "select-applied")
	s := newTestServer(t)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":{"plex_servers":true}}`, nil), 400)
	id := rpcID(t, s, globalRPC(t, "plex_servers", row.RequestParams, ""))
	if len(commands(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), false)) != 0 {
		t.Fatal("managed Plex action leaked into legacy commands")
	}
	expect(t, request(s, "POST", "/api/webhook/commands/ack", firstToken, `{"ids":["`+id+`"]}`, nil), 200)
	if len(s.devices[0].queue) != 1 {
		t.Fatal("legacy acknowledgement consumed managed Plex request")
	}
	for _, action := range []string{"plex_servers", "plex_library", "plex_queue"} {
		data := inspectData("operation")
		data["data"].(map[string]any)["action"] = action
		q := &inspectRequest{Version: 1, Runtime: "page-123", Section: "operation", OperationID: strings.Repeat("a", 32)}
		if !validInspectResult(transferJSON(t, data), "ok", q) {
			t.Fatalf("inspection rejected known action %s", action)
		}
	}
}

func TestPlexServersRawEscapingAndPrivateFields(t *testing.T) {
	f := plexServersFixture(t)
	row := f.reply(t, "list-one")
	for _, size := range []int{maxPlexServersResultBytes, maxPlexServersResultBytes + 1} {
		s := newTestServer(t)
		id := rpcID(t, s, globalRPC(t, "plex_servers", row.RequestParams, ""))
		body := strings.ReplaceAll(row.ResponseJSON, `"`+f.ExpectedRequestID+`"`, `"`+id+`"`)
		body = strings.Replace(body, `"Family Plex"`, `"`+strings.Repeat(`\u0041`, 512)+`"`, 1)
		body += strings.Repeat(" ", size-len(body))
		code := 200
		if size > maxPlexServersResultBytes {
			code = 400
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), code)
		if code == 400 && (len(s.devices[0].queue) != 1 || s.resultBytes != 0) {
			t.Fatal("raw oversized result was compacted into an accepted result")
		}
	}
	s := newTestServer(t)
	id := rpcID(t, s, globalRPC(t, "plex_servers", row.RequestParams, ""))
	body := strings.ReplaceAll(row.ResponseJSON, `"`+f.ExpectedRequestID+`"`, `"`+id+`"`)
	body = strings.Replace(body, `"name":"Family Plex"`, `"name":"Family Plex","token":"fixture-private-value","origin":"https://fixture.invalid/private"`, 1)
	got := request(s, "POST", "/api/responses", firstToken, body, nil)
	expect(t, got, 400)
	if strings.Contains(got.Body.String(), "fixture") || len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
		t.Fatal("private response fields leaked or were accepted")
	}
}
