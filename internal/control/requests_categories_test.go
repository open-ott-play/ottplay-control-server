package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func categoryContract(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	data, err := os.ReadFile("../../contracts/categories-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]json.RawMessage
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestCategorySharedContractAndAuthenticatedRoundTrip(t *testing.T) {
	fixture := categoryContract(t)
	for key, expected := range map[string]int{"version": 1, "max_page_size": maxCategoryPageSize, "max_items": maxCategoryItems, "max_result_bytes": maxCategoryResultBytes} {
		if !profileInteger(fixture[key], expected, expected) {
			t.Fatalf("contract constant %s differs", key)
		}
	}
	for _, name := range []string{"list", "select", "channels", "channels_next"} {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			body := string(fixture[name+"_request"])
			expect(t, request(s, "POST", "/api/requests?device_id=first", "", body, nil), 401)
			expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
			id := rpcID(t, s, body)
			bound := s.devices[0].queue[0].category
			if bound == nil || bound.Runtime != "page-123" {
				t.Fatal("category request lost runtime binding")
			}
			poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
			expect(t, poll, 200)
			if !strings.Contains(poll.Body.String(), id) {
				t.Fatal("request missing from acknowledged poll")
			}
			response := screenshotEnvelope(id, "ok", fixture[name+"_result"])
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response || len(s.devices[0].queue) != 0 || s.bytes != 0 || s.resultBytes != len(response) {
				t.Fatal("category result changed, remained queued or was stored twice")
			}
			stale := strings.Replace(response, `"runtime":"page-123"`, `"runtime":"stale-page"`, 1)
			expect(t, request(s, "POST", "/api/responses", firstToken, stale, nil), 400)
		})
	}
}

func TestCategoryRequestsRejectMalformedSelectorsAndPagination(t *testing.T) {
	fixture := categoryContract(t)
	for _, name := range []string{"list", "select", "channels"} {
		body, _ := decodeObject(fixture[name+"_request"])
		params, _ := decodeObject(body["params"])
		invalid := []map[string]json.RawMessage{}
		for key := range params {
			// search is optional; every other fixture field is required.
			if key == "search" {
				continue
			}
			copy := cloneCategoryFields(params)
			delete(copy, key)
			invalid = append(invalid, copy)
		}
		for key, values := range map[string][]string{
			"runtime": {`null`, `true`, `""`, `"page_123"`},
			"extra":   {`true`},
			"catalog": {`null`, `1`, `""`, `"private token"`, `"https://example.invalid"`, `"` + strings.Repeat("x", 129) + `"`},
		} {
			for _, raw := range values {
				copy := cloneCategoryFields(params)
				copy[key] = json.RawMessage(raw)
				invalid = append(invalid, copy)
			}
		}
		if name != "select" {
			for key, values := range map[string][]string{
				"offset": {`null`, `true`, `-1`, `1000001`, `0.0`, `1e0`, `0.0000000000000001`},
				"limit":  {`null`, `true`, `0`, `201`, `1.0`, `1e0`},
			} {
				for _, raw := range values {
					copy := cloneCategoryFields(params)
					copy[key] = json.RawMessage(raw)
					invalid = append(invalid, copy)
				}
			}
		}
		if name != "list" {
			for _, raw := range []string{`null`, `1`, `""`, `"  "`, `"bad\ncategory"`, `"bad\u0085category"`, `"\ud800"`, `"` + strings.Repeat("я", 513) + `"`} {
				copy := cloneCategoryFields(params)
				copy["category"] = json.RawMessage(raw)
				invalid = append(invalid, copy)
			}
		}
		for _, fields := range invalid {
			paramsJSON, _ := json.Marshal(fields)
			body["params"] = paramsJSON
			bodyJSON, _ := json.Marshal(body)
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, string(bodyJSON), nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("invalid category request was queued")
			}
		}
	}
	for _, params := range []string{
		`{"runtime":"page-123","op":"list","offset":1,"limit":200}`,
		`{"runtime":"page-123","op":"unknown","offset":0,"limit":200}`,
		`{"runtime":"page-123","op":"list","offset":0,"offset":1,"limit":200}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"categories","params":`+params+`}`, nil), 400)
	}
}

func cloneCategoryFields(fields map[string]json.RawMessage) map[string]json.RawMessage {
	copy := make(map[string]json.RawMessage, len(fields))
	for key, value := range fields {
		copy[key] = value
	}
	return copy
}

func TestCategoryResultsFenceRuntimeIdentityAndPageInvariants(t *testing.T) {
	fixture := categoryContract(t)
	for _, name := range []string{"list", "select", "channels", "channels_next"} {
		base, _ := decodeObject(fixture[name+"_result"])
		mutations := map[string][]string{
			"version": {`null`, `true`, `2`, `1.0`},
			"runtime": {`"stale-page"`},
			"extra":   {`"private token"`},
			"catalog": {`""`, `"private token"`},
		}
		if name == "select" {
			mutations["selected"] = []string{`null`, `"other-category"`}
			mutations["catalog"] = append(mutations["catalog"], `"wrong-token"`)
			mutations["dispatched"] = []string{`null`, `false`, `1`}
		} else {
			mutations["offset"] = []string{`1`, `true`, `0.0`}
			mutations["total"] = []string{`-1`, `null`, `true`, `1000001`, `0`}
			mutations["loading"] = []string{`null`, `1`}
			mutations["has_more"] = []string{`null`, `1`, `false`}
			if name != "channels" {
				mutations["has_more"][2] = `true`
			}
			rowKey := "categories"
			if name != "list" {
				rowKey = "channels"
				mutations["category"] = []string{`"other-category"`}
			}
			if name == "channels_next" {
				mutations["catalog"] = append(mutations["catalog"], `"wrong-token"`)
			}
			mutations[rowKey] = []string{`null`, `{}`, `[]`}
			var rows []json.RawMessage
			_ = json.Unmarshal(base[rowKey], &rows)
			row, _ := decodeObject(rows[0])
			for key, values := range map[string][]string{
				"id": {`null`, `""`, `"  "`, `"\ud800"`, `"bad\u0085id"`}, "name": {`null`, `""`, `"bad\nname"`, `"bad\u009fname"`, `"` + strings.Repeat("я", 257) + `"`},
				"url": {`"https://private.invalid"`},
			} {
				for _, value := range values {
					copy := cloneCategoryFields(row)
					copy[key] = json.RawMessage(value)
					changed, _ := json.Marshal(copy)
					changedRows := append([]json.RawMessage{changed}, rows[1:]...)
					encoded, _ := json.Marshal(changedRows)
					mutations[rowKey] = append(mutations[rowKey], string(encoded))
				}
			}
		}
		for key, values := range mutations {
			for _, value := range values {
				t.Run(name+"/"+key+"/"+value, func(t *testing.T) {
					s := newTestServer(t)
					id := rpcID(t, s, string(fixture[name+"_request"]))
					data := cloneCategoryFields(base)
					data[key] = json.RawMessage(value)
					body := screenshotEnvelope(id, "ok", data)
					expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 400)
					if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
						t.Fatal("invalid result consumed request or retained data")
					}
				})
			}
		}
	}
}

func TestCategoryNegativeRepliesAreBoundIncludingReplays(t *testing.T) {
	fixture := categoryContract(t)
	var codes []string
	_ = json.Unmarshal(fixture["errors"], &codes)
	for _, name := range []string{"list", "select", "channels"} {
		for _, status := range []string{"rejected", "unsupported"} {
			for _, code := range codes {
				s := newTestServer(t)
				id := rpcID(t, s, string(fixture[name+"_request"]))
				data := map[string]any{"version": 1, "runtime": "page-123", "op": name, "state": "error", "error": code}
				for _, change := range []map[string]any{{"runtime": "stale-page"}, {"op": "other"}, {"error": "private token"}, {"extra": "secret"}} {
					bad := make(map[string]any)
					for key, value := range data {
						bad[key] = value
					}
					for key, value := range change {
						bad[key] = value
					}
					expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, bad), nil), 400)
				}
				body := screenshotEnvelope(id, status, data)
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
				data["runtime"] = "stale-page"
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, data), nil), 400)
				if s.resultBytes != len(body) || len(s.devices[0].queue) != 0 {
					t.Fatal("negative replay changed retained result")
				}
			}
		}
	}
}

func TestCategoryPageBoundariesAndCatalogContinuity(t *testing.T) {
	fixture := categoryContract(t)
	requestBody, _ := decodeObject(fixture["channels_request"])
	params, _ := decodeObject(requestBody["params"])
	params["limit"] = json.RawMessage(`200`)
	expected := parseCategoryRequest("channels", params)
	data, _ := decodeObject(fixture["channels_result"])
	rows := make([]map[string]any, 200)
	for i := range rows {
		rows[i] = map[string]any{"id": "video:" + strconv.Itoa(i), "name": "Video", "number": i + 1}
	}
	encode := func() json.RawMessage {
		t.Helper()
		data["channels"], _ = json.Marshal(rows)
		body, _ := json.Marshal(data)
		return body
	}
	data["total"], data["has_more"] = json.RawMessage(`200`), json.RawMessage(`false`)
	if !validCategoryResult(encode(), "ok", expected) {
		t.Fatal("maximum valid page rejected")
	}
	rows[1]["id"] = rows[0]["id"]
	if validCategoryResult(encode(), "ok", expected) {
		t.Fatal("duplicate stable item IDs accepted")
	}
	rows[1]["id"], rows[1]["number"] = "video:1", 1
	if validCategoryResult(encode(), "ok", expected) {
		t.Fatal("duplicate global channel numbers accepted")
	}
	rows[1]["number"] = 2
	rows = append(rows, map[string]any{"id": "overflow", "name": "Overflow", "number": 201})
	data["total"] = json.RawMessage(`201`)
	if validCategoryResult(encode(), "ok", expected) {
		t.Fatal("page limit overflow accepted")
	}
	rows = nil
	data["total"], data["has_more"] = json.RawMessage(`0`), json.RawMessage(`false`)
	rows = []map[string]any{}
	if !validCategoryResult(encode(), "ok", expected) {
		t.Fatal("empty category rejected")
	}
	data["total"], data["has_more"] = json.RawMessage(`1`), json.RawMessage(`true`)
	if validCategoryResult(encode(), "ok", expected) {
		t.Fatal("empty page with no forward progress accepted")
	}
	requestBody, _ = decodeObject(fixture["list_request"])
	params, _ = decodeObject(requestBody["params"])
	params["offset"], params["catalog"] = json.RawMessage(`2`), json.RawMessage(`"pinned-catalog"`)
	expected = parseCategoryRequest("categories", params)
	data, _ = decodeObject(fixture["list_result"])
	data["offset"], data["categories"] = json.RawMessage(`2`), json.RawMessage(`[]`)
	data["catalog"] = json.RawMessage(`"pinned-catalog"`)
	encoded, _ := json.Marshal(data)
	if expected == nil || !validCategoryResult(encoded, "ok", expected) {
		t.Fatal("pinned category continuation rejected")
	}
	data["catalog"] = json.RawMessage(`"replacement-catalog"`)
	encoded, _ = json.Marshal(data)
	if validCategoryResult(encoded, "ok", expected) {
		t.Fatal("category continuation changed snapshot token")
	}
	data["catalog"] = json.RawMessage(`"pinned-catalog"`)
	encoded, _ = json.Marshal(data)
	if validCategoryResult(append(encoded, []byte(strings.Repeat(" ", maxCategoryResultBytes))...), "ok", expected) {
		t.Fatal("oversized category response accepted")
	}
}

func TestCategoryOperatorScopesAndLegacyChannels(t *testing.T) {
	token := strings.Repeat("q", 32)
	sum := sha256.Sum256([]byte(token))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "categories", CredentialSHA256: hex.EncodeToString(sum[:]), DeviceIDs: []string{"first"}, Actions: []string{"categories.list", "channels"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	fixture := categoryContract(t)
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, string(fixture["list_request"]), nil), 202)
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, string(fixture["channels_request"]), nil), 202)
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, string(fixture["select_request"]), nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=second", token, string(fixture["list_request"]), nil), 403)
	for _, action := range []string{"categories", "categories.*", "categories.delete"} {
		if config.ValidCommandAction(action) {
			t.Fatalf("accepted broad category scope %s", action)
		}
	}
	if !config.ValidCommandAction("categories.select") {
		t.Fatal("category selection scope missing")
	}
	for _, params := range []string{`{}`, `{"search":""}`, `{"search":"Коты"}`} {
		legacy := newTestServer(t)
		id := rpcID(t, legacy, `{"action":"channels","params":`+params+`}`)
		if legacy.devices[0].queue[0].category != nil {
			t.Fatal("legacy channels unexpectedly opted in")
		}
		expect(t, request(legacy, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", map[string]any{"channels": []any{map[string]any{"id": 4, "name": "Legacy", "number": 1}}}), nil), 200)
	}
}
