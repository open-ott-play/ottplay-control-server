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

func localMediaContract(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	b, err := os.ReadFile("../../contracts/local-media-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var v map[string]json.RawMessage
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatal(err)
	}
	return v
}

func TestLocalMediaSharedContractAndDurableResponse(t *testing.T) {
	c := localMediaContract(t)
	if !validLocalMediaCapability(c["capability"]) {
		t.Fatal("capability rejected")
	}
	var errors []string
	if json.Unmarshal(c["errors"], &errors) != nil || len(errors) != len(localMediaErrors) {
		t.Fatal("errors differ")
	}
	for _, e := range errors {
		if !localMediaErrors[e] {
			t.Fatal(e)
		}
	}
	for _, key := range []string{"sources", "play", "shuffle", "connect", "paths", "lookup"} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c[key+"_request"]))
		if s.devices[0].queue[0].localMedia == nil {
			t.Fatal("request not bound")
		}
		body := `{"id":"` + id + `","status":"ok","data":` + string(c[key+"_result"]) + `}`
		for range 2 {
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		}
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		expect(t, got, 200)
		if got.Body.String() != body || len(s.devices[0].queue) != 0 {
			t.Fatal("response changed or replayed")
		}
	}
}

func TestLocalMediaInvalidRequestNeverQueued(t *testing.T) {
	for _, params := range []string{
		`{}`, `null`, `[]`, `{"runtime":"page-123","op":"unknown"}`,
		`{"runtime":"page-123","op":"shuffle"}`, `{"runtime":"page-123","op":"shuffle","source":""}`,
		`{"runtime":"page-123","op":"shuffle","source":"./Movies"}`,
		`{"runtime":"page-123","op":"shuffle","source":"https://secret.example"}`,
		`{"runtime":"page-123","op":"shuffle","source":"Movies\nSecret"}`,
		`{"runtime":"page-123","op":"shuffle","source":null}`,
		`{"runtime":"page-123","op":"shuffle","source":"Movies","include_paths":true}`,
		`{"runtime":"page-123","op":"sources","source":"Movies"}`,
		`{"runtime":"page-123","op":"sources","include_paths":1}`,
		`{"runtime":"page-123","op":"sources","include_paths":null}`,
		`{"runtime":"page-123","op":"sources","path":null}`,
		`{"runtime":"page-123","op":"sources","path":true}`,
		`{"runtime":"page-123","op":"sources","path":""}`,
		`{"runtime":"page-123","op":"sources","path":"Movies"}`,
		`{"runtime":"page-123","op":"sources","path":"./Movies"}`,
		`{"runtime":"page-123","op":"sources","path":"https://secret.example"}`,
		`{"runtime":"page-123","op":"sources","path":"/Movies","source":"Movies"}`,
		`{"runtime":"page-123","op":"shuffle","source":"Movies","path":"/Movies"}`,
		`{"runtime":"page-123","op":"sources","extra":true}`,
		`{"runtime":"bad_page","op":"sources"}`,
		`{"runtime":"page-123","op":"sources","op":"shuffle"}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"local_media","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid request queued")
		}
	}
	for _, source := range []string{`/Volumes/data/Movies/`, `C:\Movies`, `Movies`, `source-123`, `Три кота`} {
		params, _ := json.Marshal(map[string]any{"runtime": "page-123", "op": "shuffle", "source": source})
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"local_media","params":`+string(params)+`}`, nil), 202)
	}
}

func TestLocalMediaPathLookupKeepsPathDisclosureExplicit(t *testing.T) {
	for _, path := range []string{"/var/media/Movies", `C:\Movies`, "//server/share/Movies"} {
		params, _ := json.Marshal(map[string]any{"runtime": "page-123", "op": "sources", "path": path})
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"local_media","params":`+string(params)+`}`)
		if s.devices[0].queue[0].localMedia.Path != path {
			t.Fatal("lookup path was not bound")
		}
		c := localMediaContract(t)
		expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":`+string(c["lookup_result"])+`}`, nil), 400)
		empty := map[string]any{"version": 1, "runtime": "page-123", "op": "sources", "state": "ready", "sources": []any{}}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", empty), nil), 200)
	}
}

func TestLocalMediaBadResultDoesNotConsumeAndPathsAreExplicit(t *testing.T) {
	c := localMediaContract(t)
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["runtime"] = "other-page" },
		func(v map[string]any) { v["op"] = "connect" },
		func(v map[string]any) { v["count"] = 0 },
		func(v map[string]any) { v["count"] = 50001 },
		func(v map[string]any) { v["accepted"] = false },
		func(v map[string]any) { v["playback_confirmed"] = true },
		func(v map[string]any) { v["shuffled"] = false },
		func(v map[string]any) { v["source"].(map[string]any)["path"] = "/private/secret" },
		func(v map[string]any) { v["source"].(map[string]any)["available"] = false },
		func(v map[string]any) { v["source"].(map[string]any)["name"] = "Other folder" },
		func(v map[string]any) { v["secret"] = "never-forward" },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c["shuffle_request"]))
		var v map[string]any
		_ = json.Unmarshal(c["shuffle_result"], &v)
		mutate(v)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", v), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid result consumed request")
		}
	}
	s := newTestServer(t)
	id := rpcID(t, s, string(c["sources_request"]))
	body := `{"id":"` + id + `","status":"ok","data":` + string(c["paths_result"]) + `}`
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 400)
	if len(s.devices[0].queue) != 1 {
		t.Fatal("unsolicited paths consumed request")
	}
}

func TestLocalMediaNegativeReplyIsBoundAndFixed(t *testing.T) {
	c := localMediaContract(t)
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["runtime"] = "other-page" },
		func(v map[string]any) { v["op"] = "sources" },
		func(v map[string]any) { v["error"] = "private path or token" },
		func(v map[string]any) { v["extra"] = "secret" },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c["shuffle_request"]))
		var v map[string]any
		_ = json.Unmarshal(c["error_result"], &v)
		mutate(v)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "rejected", v), nil), 400)
	}
	s := newTestServer(t)
	id := rpcID(t, s, string(c["shuffle_request"]))
	var v map[string]any
	_ = json.Unmarshal(c["error_result"], &v)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "rejected", v), nil), 200)
}

func TestLocalMediaOperatorScopes(t *testing.T) {
	credential := strings.Repeat("m", 32)
	hash := sha256.Sum256([]byte(credential))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "reader", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"local_media.sources"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	contract := localMediaContract(t)
	expect(t, request(s, "POST", "/api/requests?device_id=first", credential, string(contract["sources_request"]), nil), 202)
	expect(t, request(s, "POST", "/api/requests?device_id=first", credential, string(contract["shuffle_request"]), nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=second", credential, string(contract["sources_request"]), nil), 403)
	for _, op := range []string{"play", "shuffle", "connect"} {
		s.operators[0].actions["local_media."+op] = true
		expect(t, request(s, "POST", "/api/requests?device_id=first", credential, string(contract[op+"_request"]), nil), 202)
	}
}

func TestLocalMediaCapabilityAndSourceBounds(t *testing.T) {
	c := localMediaContract(t)
	for _, count := range []int{13611, maxLocalMediaItems} {
		var result map[string]any
		_ = json.Unmarshal(c["shuffle_result"], &result)
		result["count"] = count
		raw, _ := json.Marshal(result)
		if !validLocalMediaResult(raw, "ok", &localMediaRequest{Runtime: "page-123", Op: "shuffle", Source: "Movies"}) {
			t.Fatal("supported complete source count rejected", count)
		}
	}
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["version"] = true },
		func(v map[string]any) { v["max_sources"] = 33 },
		func(v map[string]any) { v["max_items"] = 50001 },
		func(v map[string]any) { v["absolute_paths"] = 1 },
		func(v map[string]any) { v["operations"] = []string{"shuffle", "shuffle"} },
		func(v map[string]any) { v["operations"] = []string{"delete"} },
		func(v map[string]any) { v["secret"] = "private" },
	} {
		var marker map[string]any
		_ = json.Unmarshal(c["capability"], &marker)
		mutate(marker)
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", map[string]any{"local_media": marker}), nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("invalid capability consumed request")
		}
	}
	request := &localMediaRequest{Runtime: "page-123", Op: "sources"}
	if validLocalMediaResult(append([]byte(strings.Repeat(" ", maxLocalMediaResultBytes)), c["sources_result"]...), "ok", request) {
		t.Fatal("oversized response accepted")
	}
	var result map[string]any
	_ = json.Unmarshal(c["sources_result"], &result)
	rows := result["sources"].([]any)
	result["sources"] = []any{rows[0], rows[0]}
	raw, _ := json.Marshal(result)
	if validLocalMediaResult(raw, "ok", request) {
		t.Fatal("duplicate source ID accepted")
	}
	result["sources"] = []any{}
	raw, _ = json.Marshal(result)
	if !validLocalMediaResult(raw, "ok", request) {
		t.Fatal("empty source listing rejected")
	}
}
