package control

import (
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
)

func favoritesContract(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/favorites-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]json.RawMessage
	if err = json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func favoriteFixture(t *testing.T, op string) map[string]any {
	t.Helper()
	var result map[string]any
	if err := json.Unmarshal(favoritesContract(t)[op+"_result"], &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestFavoritesSharedContract(t *testing.T) {
	fixture := favoritesContract(t)
	for key, want := range map[string]int{"max_items": maxFavorites, "max_result_bytes": maxFavoritesResultBytes} {
		if !profileInteger(fixture[key], want, want) {
			t.Fatalf("mismatched %s", key)
		}
	}
	var codes []string
	if json.Unmarshal(fixture["errors"], &codes) != nil || len(codes) != len(favoritesErrors) {
		t.Fatal("mismatched errors")
	}
	for _, code := range codes {
		if !favoritesErrors[code] {
			t.Fatalf("unknown error %s", code)
		}
	}
	for _, op := range []string{"list", "add", "play", "remove"} {
		r, _ := decodeObject(fixture[op+"_request"])
		params, _ := decodeObject(r["params"])
		bound := parseFavoritesRequest(params)
		if bound == nil || !validFavoritesResult(fixture[op+"_result"], "ok", bound) {
			t.Fatalf("shared %s rejected", op)
		}
	}
}

func TestFavoritesStrictRequests(t *testing.T) {
	for _, params := range []string{
		`null`, `[]`, `{}`, `{"runtime":"page-123","op":"list","query":"A"}`,
		`{"runtime":"page_123","op":"list"}`, `{"runtime":"page-123","op":"List"}`,
		`{"runtime":"page-123","op":"add","name":""}`, `{"runtime":"page-123","op":"add","name":"  "}`,
		`{"runtime":"page-123","op":"add","name":"A\nB"}`, `{"runtime":"page-123","op":"add","name":"\ud800"}`,
		`{"runtime":"page-123","op":"add","name":"` + strings.Repeat("я", 81) + `"}`,
		`{"runtime":"page-123","op":"add","profile":3}`, `{"runtime":"page-123","op":"add","url":"https://secret"}`,
		`{"runtime":"page-123","op":"play"}`, `{"runtime":"page-123","op":"play","query":null}`,
		`{"runtime":"page-123","op":"play","query":"A","id":"fav-1"}`,
		`{"runtime":"page-123","op":"play","id":"fav-"}`, `{"runtime":"page-123","op":"play","id":"fav-A"}`,
		`{"runtime":"page-123","op":"play","id":"fav-` + strings.Repeat("a", 61) + `"}`,
		`{"runtime":"page-123","op":"remove","query":"A","query":"B"}`,
		`{"runtime":"page-123","op":"remove","query":1}`, `{"runtime":"page-123","op":"remove","name":"A"}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"favorites","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("bad favorite was queued")
		}
	}
	for _, params := range []string{
		`{"runtime":"page-123","op":"list"}`, `{"runtime":"page-123","op":"add"}`,
		`{"runtime":"page-123","op":"add","name":"` + strings.Repeat("я", 80) + `"}`,
		`{"runtime":"page-123","op":"play","query":"` + strings.Repeat("я", 80) + `"}`,
		`{"runtime":"page-123","op":"remove","id":"fav-` + strings.Repeat("a", 60) + `"}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"favorites","params":`+params+`}`, nil), 202)
	}
}

func TestFavoritesAuthenticatedRoundTripAndRetainedBinding(t *testing.T) {
	fixture := favoritesContract(t)
	for _, op := range []string{"list", "add", "play", "remove"} {
		t.Run(op, func(t *testing.T) {
			s, _ := diagnosticServer(t)
			body := string(fixture[op+"_request"])
			for _, auth := range []struct {
				token string
				code  int
			}{{"", 401}, {firstToken, 403}, {diagnosticOperatorToken, 401}} {
				expect(t, request(s, "POST", "/api/requests?device_id=first", auth.token, body, nil), auth.code)
			}
			id := rpcID(t, s, body)
			if s.devices[0].queue[0].favorites.Runtime != "page-123" {
				t.Fatal("missing binding")
			}
			response := screenshotEnvelope(id, "ok", favoriteFixture(t, op))
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response || s.bytes != 0 || len(s.devices[0].queue) != 0 {
				t.Fatal("receipt changed or remained queued")
			}
			stale := favoriteFixture(t, op)
			stale["runtime"] = "old-page"
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", stale), nil), 400)
		})
	}
}

func TestFavoritesStrictResultsDoNotConsumeRequest(t *testing.T) {
	for _, change := range []func(map[string]any){
		func(v map[string]any) { v["version"] = true },
		func(v map[string]any) { v["runtime"] = "other-page" },
		func(v map[string]any) { v["op"] = "remove" },
		func(v map[string]any) { v["state"] = "ready" },
		func(v map[string]any) { v["dispatched"] = false },
		func(v map[string]any) { v["dispatched"] = 1 },
		func(v map[string]any) { v["url"] = "https://private" },
		func(v map[string]any) { v["entry"].(map[string]any)["id"] = "fav-another" },
		func(v map[string]any) { v["entry"].(map[string]any)["provider"] = "https://private" },
		func(v map[string]any) { v["entry"].(map[string]any)["profile_number"] = true },
		func(v map[string]any) { v["entry"].(map[string]any)["profile_number"] = 16 },
		func(v map[string]any) { v["entry"].(map[string]any)["profile_name"] = strings.Repeat("я", 129) },
		func(v map[string]any) { v["entry"].(map[string]any)["name"] = "A\x1b[31m" },
		func(v map[string]any) { v["entry"].(map[string]any)["name"] = strings.Repeat("я", 81) },
		func(v map[string]any) { v["entry"].(map[string]any)["title"] = strings.Repeat("я", 257) },
		func(v map[string]any) { v["entry"].(map[string]any)["kind"] = "url" },
		func(v map[string]any) { v["entry"].(map[string]any)["ref"] = "private" },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, string(favoritesContract(t)["play_request"]))
		v := favoriteFixture(t, "play")
		change(v)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", v), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid result consumed request or persisted")
		}
	}
}

func TestFavoritesBoundNegativeRepliesAndDuplicateFields(t *testing.T) {
	for _, status := range []string{"ok", "rejected", "unsupported"} {
		for code := range favoritesErrors {
			s := newTestServer(t)
			id := rpcID(t, s, string(favoritesContract(t)["play_request"]))
			v := map[string]any{"version": 1, "runtime": "page-123", "op": "play", "state": "error", "error": code}
			bad := screenshotEnvelope(id, status, map[string]any{"error": code})
			expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
			v["runtime"] = "wrong"
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil), 400)
			v["runtime"], v["error"] = "page-123", "private-token"
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil), 400)
			v["error"] = code
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil), 200)
		}
	}
	for _, transform := range []func(string) string{
		func(v string) string { return strings.Replace(v, `"op":"play"`, `"op":"play","op":"list"`, 1) },
		func(v string) string {
			return strings.Replace(v, `"id":"fav-abc123"`, `"id":"fav-abc123","id":"fav-2"`, 1)
		},
	} {
		s := newTestServer(t)
		id := rpcID(t, s, string(favoritesContract(t)["play_request"]))
		good := screenshotEnvelope(id, "ok", favoriteFixture(t, "play"))
		bad := transform(good)
		if good == bad {
			t.Fatal("missing duplicate field")
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
		expect(t, request(s, "POST", "/api/responses", firstToken, good, nil), 200)
		expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
	}
}

func TestFavoritesFullListFitsTransportAndBounds(t *testing.T) {
	v := favoriteFixture(t, "list")
	rows := make([]any, maxFavorites)
	for i := range rows {
		rows[i] = map[string]any{"id": "fav-" + strconv.Itoa(i), "name": strings.Repeat("<", 160),
			"title": strings.Repeat("<", 512), "provider": strings.Repeat("a", 64), "profile_number": nil,
			"profile_name": strings.Repeat("<", 256), "kind": "live"}
	}
	v["entries"] = rows
	bound := &favoritesRequest{Runtime: "page-123", Op: "list"}
	raw, _ := json.Marshal(v)
	if len(raw) <= 500*1024 || !validFavoritesResult(raw, "ok", bound) {
		t.Fatal("maximum escaped list does not fit")
	}
	s := newTestServer(t)
	id := rpcID(t, s, string(favoritesContract(t)["list_request"]))
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", v), nil), 200)
	for _, invalid := range []any{nil, append(rows, rows[0]), []any{rows[0], rows[0]}} {
		v["entries"] = invalid
		raw, _ = json.Marshal(v)
		if validFavoritesResult(raw, "ok", bound) {
			t.Fatal("invalid/incomplete/duplicate list accepted")
		}
	}
	v["entries"] = []any{}
	raw, _ = json.Marshal(v)
	if !validFavoritesResult(raw, "ok", bound) || validFavoritesResult(raw, "rejected", bound) ||
		validFavoritesResult(append(raw, []byte(strings.Repeat(" ", maxFavoritesResultBytes))...), "ok", bound) {
		t.Fatal("bad empty-list/status/byte-bound behavior")
	}
	s = newTestServer(t)
	id = rpcID(t, s, string(favoritesContract(t)["list_request"]))
	s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", v), nil), 404)
}

func TestFavoritesOperationEvidenceDoesNotClaimPlayback(t *testing.T) {
	v := map[string]any{"operation_id": strings.Repeat("a", 32), "state": "invoked", "action": "favorites",
		"evidence": map[string]any{"kind": "handler_completed", "generation": nil, "position": nil}}
	raw, _ := json.Marshal(v)
	if !validInspectOperation(raw, strings.Repeat("a", 32)) {
		t.Fatal("favorites operation not supported")
	}
	v["state"] = "observed"
	raw, _ = json.Marshal(v)
	if validInspectOperation(raw, strings.Repeat("a", 32)) {
		t.Fatal("handler completion became observed playback")
	}
}
