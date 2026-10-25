package control

import (
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
)

func plexLibraryFixture(op string) map[string]any {
	result := map[string]any{"version": 1, "runtime": "page-123", "op": op, "state": "ready"}
	library := map[string]any{"id": "16", "title": "Три кота", "type": "show"}
	if op == "list" {
		result["libraries"] = []any{library}
	} else {
		result["library"], result["shuffled"] = library, true
		queue := plexQueueFixture()
		if op == "preview" {
			queue = map[string]any{"version": 1, "runtime": "page-123", "state": "ready", "ids": []string{"78777", "78776", "78775"}, "titles": []string{"One", "Two", "Three"}, "order": "listed"}
		}
		result["queue"] = queue
	}
	return result
}

func TestPlexLibrarySharedContract(t *testing.T) {
	data, err := os.ReadFile("../../contracts/plex-library-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]json.RawMessage
	if err = json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	for key, expected := range map[string]int{"max_items": maxPlexQueueItems, "max_libraries": maxPlexLibraries, "max_result_bytes": maxPlexLibraryResultBytes} {
		if !profileInteger(fixture[key], expected, expected) {
			t.Fatalf("contract constant %s differs", key)
		}
	}
	var errors []string
	if json.Unmarshal(fixture["errors"], &errors) != nil || len(errors) != len(plexLibraryErrors) {
		t.Fatal("contract errors differ")
	}
	for _, problem := range errors {
		if !plexLibraryErrors[problem] {
			t.Fatal("unknown contract error")
		}
	}
	for _, op := range []string{"list", "preview", "play"} {
		request, _ := decodeObject(fixture[op+"_request"])
		params, _ := decodeObject(request["params"])
		bound := parsePlexLibraryRequest(params)
		if bound == nil || !validPlexLibraryResult(fixture[op+"_result"], "ok", bound) {
			t.Fatalf("contract %s rejected", op)
		}
	}
	request, _ := decodeObject(fixture["truncation_request"])
	params, _ := decodeObject(request["params"])
	bound := parsePlexLibraryRequest(params)
	if bound == nil || !bound.AllowTruncate || bound.Op != "play" {
		t.Fatal("truncation opt-in contract rejected")
	}
}

func TestPlexLibraryRequestValidation(t *testing.T) {
	base := `"runtime":"page-123","op":"play"`
	for _, params := range []string{
		`{}`, `null`, `[]`, `{` + base + `}`, `{` + base + `,"query":""}`, `{` + base + `,"query":"  "}`,
		`{` + base + `,"query":null}`, `{` + base + `,"query":true}`, `{` + base + `,"query":"a\nb"}`,
		`{` + base + `,"query":"\ud800"}`, `{` + base + `,"query":"` + strings.Repeat("я", 129) + `"}`,
		`{` + base + `,"library_id":"0"}`, `{` + base + `,"library_id":"01"}`, `{` + base + `,"library_id":16}`,
		`{` + base + `,"library_id":"` + strings.Repeat("1", 21) + `"}`,
		`{` + base + `,"query":"A","library_id":"16"}`, `{` + base + `,"query":"A","shuffle":1}`,
		`{` + base + `,"query":"A","shuffle":null}`, `{` + base + `,"query":"A","extra":true}`,
		`{` + base + `,"query":"A","allow_truncate":false}`, `{` + base + `,"query":"A","allow_truncate":null}`,
		`{` + base + `,"query":"A","allow_truncate":1}`, `{` + base + `,"query":"A","allow_truncate":"true"}`,
		`{` + base + `,"query":"A","query":"B"}`,
		`{"runtime":"page-123","op":"list","query":"A"}`, `{"runtime":"page-123","op":"list","shuffle":false}`,
		`{"runtime":"page-123","op":"list","allow_truncate":true}`,
		`{"runtime":"page_123","op":"list"}`, `{"runtime":"page-123","op":"search"}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"plex_library","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("invalid library request queued")
		}
	}
	for _, selector := range []string{`"query":"` + strings.Repeat("я", 128) + `"`, `"library_id":"` + strings.Repeat("1", 20) + `"`} {
		for _, suffix := range []string{"", `,"shuffle":false`, `,"shuffle":true`, `,"allow_truncate":true`, `,"shuffle":true,"allow_truncate":true`} {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"plex_library","params":{`+base+`,`+selector+suffix+`}}`, nil), 202)
		}
	}
}

func TestPlexLibraryTruncationIsBoundToOptInAndFullQueue(t *testing.T) {
	for _, op := range []string{"play", "preview"} {
		t.Run(op, func(t *testing.T) {
			value := plexLibraryFixture(op)
			ids, titles := make([]string, maxPlexQueueItems), make([]string, maxPlexQueueItems)
			for i := range ids {
				ids[i], titles[i] = strconv.Itoa(i+1), "Item "+strconv.Itoa(i+1)
			}
			queue := value["queue"].(map[string]any)
			queue["ids"] = ids
			if op == "preview" {
				queue["titles"] = titles
			}
			value["truncated"] = true
			bound := &plexLibraryRequest{Runtime: "page-123", Op: op, Shuffle: true, AllowTruncate: true}
			raw, _ := json.Marshal(value)
			if !validPlexLibraryResult(raw, "ok", bound) {
				t.Fatal("opted-in capped queue rejected")
			}
			bound.AllowTruncate = false
			if validPlexLibraryResult(raw, "ok", bound) {
				t.Fatal("unrequested capped queue accepted")
			}
			bound.AllowTruncate = true
			for _, marker := range []any{false, nil, 0, 1, "true", map[string]any{}} {
				value["truncated"] = marker
				raw, _ = json.Marshal(value)
				if validPlexLibraryResult(raw, "ok", bound) {
					t.Fatal("non-true truncation marker accepted")
				}
			}
			value["truncated"] = true
			queue["ids"] = ids[:499]
			if op == "preview" {
				queue["titles"] = titles[:499]
			}
			raw, _ = json.Marshal(value)
			if validPlexLibraryResult(raw, "ok", bound) {
				t.Fatal("truncated queue below limit accepted")
			}
			delete(value, "truncated")
			raw, _ = json.Marshal(value)
			if !validPlexLibraryResult(raw, "ok", bound) {
				t.Fatal("opt-in prevented a complete smaller queue")
			}
			bound.AllowTruncate = false
			if !validPlexLibraryResult(raw, "ok", bound) {
				t.Fatal("legacy smaller queue rejected")
			}
		})
	}
}

func TestPlexLibraryOptInRoundTripPreservesTruncationMarker(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"plex_library","params":{"runtime":"page-123","op":"play","query":"A","shuffle":true,"allow_truncate":true}}`)
	if !s.devices[0].queue[0].plexLibrary.AllowTruncate {
		t.Fatal("truncation consent was not bound to queued request")
	}
	value := plexLibraryFixture("play")
	ids := make([]string, maxPlexQueueItems)
	for i := range ids {
		ids[i] = strconv.Itoa(i + 1)
	}
	value["queue"].(map[string]any)["ids"] = ids
	value["truncated"] = true
	body := screenshotEnvelope(id, "ok", value)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, got, 200)
	if got.Body.String() != body {
		t.Fatal("truncation receipt changed")
	}
	s = newTestServer(t)
	id = rpcID(t, s, `{"action":"plex_library","params":{"runtime":"page-123","op":"play","query":"A","shuffle":true}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 400)
	if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
		t.Fatal("unrequested truncated result consumed or stored legacy request")
	}
}

func TestPlexLibraryAuthenticatedRoundTrip(t *testing.T) {
	for _, op := range []string{"list", "preview", "play"} {
		t.Run(op, func(t *testing.T) {
			s, _ := diagnosticServer(t)
			params := map[string]any{"runtime": "page-123", "op": op}
			if op != "list" {
				params["library_id"], params["shuffle"] = "16", true
			}
			body, _ := json.Marshal(map[string]any{"action": "plex_library", "params": params})
			for _, auth := range []struct {
				token string
				code  int
			}{{"", 401}, {firstToken, 403}, {diagnosticOperatorToken, 401}} {
				expect(t, request(s, "POST", "/api/requests?device_id=first", auth.token, string(body), nil), auth.code)
			}
			id := rpcID(t, s, string(body))
			if s.devices[0].queue[0].plexLibrary.Runtime != "page-123" {
				t.Fatal("request has no runtime binding")
			}
			response := screenshotEnvelope(id, "ok", plexLibraryFixture(op))
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response || s.bytes != 0 || len(s.devices[0].queue) != 0 {
				t.Fatal("library receipt changed or request stayed queued")
			}
			stale := plexLibraryFixture(op)
			stale["runtime"] = "another-page"
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", stale), nil), 400)
		})
	}
}

func TestPlexLibraryRejectsInconsistentResults(t *testing.T) {
	for _, change := range []func(map[string]any){
		func(v map[string]any) { v["version"] = true }, func(v map[string]any) { v["runtime"] = "another-page" },
		func(v map[string]any) { v["op"] = "preview" }, func(v map[string]any) { v["shuffled"] = 1 },
		func(v map[string]any) { v["shuffled"] = false }, func(v map[string]any) { v["private"] = "token" },
		func(v map[string]any) { v["library"].(map[string]any)["id"] = "17" },
		func(v map[string]any) { v["library"].(map[string]any)["title"] = strings.Repeat("я", 81) },
		func(v map[string]any) { v["library"].(map[string]any)["title"] = "bad\nname" },
		func(v map[string]any) { v["library"].(map[string]any)["type"] = "artist" },
		func(v map[string]any) { v["queue"].(map[string]any)["runtime"] = "another-page" },
		func(v map[string]any) { v["queue"].(map[string]any)["index"] = true },
		func(v map[string]any) { v["queue"].(map[string]any)["index"] = 1 },
		func(v map[string]any) { v["queue"].(map[string]any)["state"] = "error" },
		func(v map[string]any) { v["queue"] = plexLibraryFixture("preview")["queue"] },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"plex_library","params":{"runtime":"page-123","op":"play","library_id":"16","shuffle":true}}`)
		value := plexLibraryFixture("play")
		change(value)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid result consumed queue or stored metadata")
		}
	}
}

func TestPlexLibraryNegativeRepliesAreBoundAndAllowlisted(t *testing.T) {
	for _, status := range []string{"ok", "rejected", "unsupported"} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"plex_library","params":{"runtime":"page-123","op":"play","query":"A"}}`)
		v := map[string]any{"version": 1, "runtime": "wrong", "op": "play", "state": "error", "error": "ambiguous_library"}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil), 400)
		v["runtime"], v["error"] = "page-123", "private token"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil), 400)
		v["error"] = "ambiguous_library"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil), 200)
		v["op"] = "list"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil), 400)
	}
}

func TestPlexLibraryCompleteListAndEscapedTitleBounds(t *testing.T) {
	v := plexLibraryFixture("list")
	libraries := make([]any, maxPlexLibraries)
	for i := range libraries {
		libraries[i] = map[string]any{"id": strconv.Itoa(i + 1), "title": strings.Repeat("<", 160), "type": "movie"}
	}
	v["libraries"] = libraries
	raw, _ := json.Marshal(v)
	bound := &plexLibraryRequest{Runtime: "page-123", Op: "list"}
	if len(raw) <= 256*1024 || !validPlexLibraryResult(raw, "ok", bound) {
		t.Fatal("complete 1000-library escaped result rejected")
	}
	for _, rows := range []any{nil, append(libraries, libraries[0]), []any{libraries[0], libraries[0]}} {
		v["libraries"] = rows
		raw, _ = json.Marshal(v)
		if validPlexLibraryResult(raw, "ok", bound) {
			t.Fatal("incomplete or duplicate library list accepted")
		}
	}
	v["libraries"] = []any{}
	raw, _ = json.Marshal(v)
	if !validPlexLibraryResult(raw, "ok", bound) || validPlexLibraryResult(raw, "rejected", bound) {
		t.Fatal("empty/negative list status handled incorrectly")
	}
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"plex_library","params":{"runtime":"page-123","op":"list"}}`)
	s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", v), nil), 404)
}

func TestPlexLibraryRejectsDuplicateFieldsBeforeAndAfterReceipt(t *testing.T) {
	for _, transform := range []func(string) string{
		func(body string) string { return strings.Replace(body, `"op":"play"`, `"op":"play","op":"list"`, 1) },
		func(body string) string { return strings.Replace(body, `"id":"16"`, `"id":"16","id":"17"`, 1) },
		func(body string) string { return strings.Replace(body, `"index":0`, `"index":0,"index":1`, 1) },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"plex_library","params":{"runtime":"page-123","op":"play","query":"A","shuffle":true}}`)
		body := screenshotEnvelope(id, "ok", plexLibraryFixture("play"))
		bad := transform(body)
		if bad == body {
			t.Fatal("test did not introduce a duplicate field")
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
	}
}

func TestPlexLibraryPreviewMaximumAndDataLimit(t *testing.T) {
	v := plexLibraryFixture("preview")
	queue := v["queue"].(map[string]any)
	ids, titles := make([]string, 500), make([]string, 500)
	for i := range ids {
		ids[i], titles[i] = strconv.Itoa(i+1), strings.Repeat("<", 512)
	}
	queue["ids"], queue["titles"] = ids, titles
	// Use the same compact, non-HTML-escaping shape a browser posts. Each title
	// remains 512 bytes; the generic 2 MiB envelope bound is unchanged.
	var out strings.Builder
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(v); err != nil {
		t.Fatal(err)
	}
	bound := &plexLibraryRequest{Runtime: "page-123", Op: "preview", Shuffle: true}
	if !validPlexLibraryResult([]byte(out.String()), "ok", bound) {
		t.Fatal("maximum browser-shaped preview rejected")
	}
	if validPlexLibraryResult([]byte(out.String()+strings.Repeat(" ", maxPlexLibraryResultBytes)), "ok", bound) {
		t.Fatal("oversized library data accepted")
	}
}

func TestPlexLibraryOperationEvidenceDoesNotInventObservation(t *testing.T) {
	v := map[string]any{"operation_id": strings.Repeat("a", 32), "state": "invoked", "action": "plex_library",
		"evidence": map[string]any{"kind": "handler_completed", "generation": nil, "position": nil}}
	raw, _ := json.Marshal(v)
	if !validInspectOperation(raw, strings.Repeat("a", 32)) {
		t.Fatal("library operation receipt rejected")
	}
	v["state"] = "observed"
	raw, _ = json.Marshal(v)
	if validInspectOperation(raw, strings.Repeat("a", 32)) {
		t.Fatal("handler completion treated as observed playback")
	}
}
