package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const offlineDocument = `{"version":1,"provider":"offline","name":"Movies","binding_id":"c98c115d-1234-4567-8abc-123456789abc","sources":[{"key":"s0","name":"Мультфильмы","kind":"folder"}],"selected":{"source_key":"s0","name":"Винни-Пух.mp4"}}`

func TestOfflineProfileRequests(t *testing.T) {
	for _, body := range []string{
		`{"action":"profile_save","params":{"number":1}}`,
		`{"action":"profile_save","params":{"number":15,"name":"Local movies"}}`,
		`{"action":"profile_save","params":{"number":15,"name":""}}`,
		`{"action":"profile_export","params":{"number":1}}`,
		`{"action":"profile_import","params":{"number":2,"profile":` + offlineDocument + `}}`,
	} {
		s := newTestServer(t)
		id := rpcID(t, s, body)
		got := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
		expect(t, got, 200)
		if !strings.Contains(got.Body.String(), id) {
			t.Fatal("request missing from queue")
		}
	}
}

func TestOfflineProfilesRejectUnknownAuthorityAndInvalidShapeBeforeQueueing(t *testing.T) {
	bad := []string{
		`{"action":"profile_save","params":{"number":1,"name":null}}`,
		`{"action":"profile_save","params":{"number":1,"name":"a\n"}}`,
		`{"action":"profile_save","params":{"number":1,"extra":true}}`,
		`{"action":"profile_save","params":{"number":1,"number":2}}`,
		`{"action":"profile_export","params":{"number":1,"path":"private"}}`,
		`{"action":"profile_import","params":{"number":1}}`,
		`{"action":"profile_import","params":{"number":1,"profile":null}}`,
		`{"action":"profile_import","params":{"number":1,"profile":` + offlineDocument + `,"force":true}}`,
	}
	for _, action := range []string{"profile_save", "profile_export", "profile_import"} {
		for _, number := range []string{`0`, `16`, `1.0`, `1e0`, `true`, `null`, `"1"`} {
			suffix := ""
			if action == "profile_import" {
				suffix = `,"profile":` + offlineDocument
			}
			bad = append(bad, `{"action":"`+action+`","params":{"number":`+number+suffix+`}}`)
		}
	}
	changes := [][2]string{
		{`"version":1`, `"version":true`}, {`"provider":"offline"`, `"provider":"m3u"`},
		{`"key":"s0"`, `"key":"s1"`}, {`"source_key":"s0"`, `"source_key":"s1"`},
		{`"kind":"folder"`, `"kind":"native"`}, {`"name":"Мультфильмы"`, `"name":""`},
		{`"name":"Мультфильмы"`, `"name":"/private/movies"`},
		{`"name":"Винни-Пух.mp4"`, `"name":"../secret.mp4"`},
		{`"name":"Винни-Пух.mp4"`, `"name":"file:secret.mp4"`},
		{`"name":"Винни-Пух.mp4"`, `"name":"a//b"`},
		{`"name":"Винни-Пух.mp4"`, `"name":"a/"`},
		{`"name":"Винни-Пух.mp4"`, `"name":"C:\\secret.mp4"`},
		{`"name":"Movies"`, `"name":"\ud800"`},
		{`c98c115d-1234-4567-8abc-123456789abc`, `native-ref-1`},
		{`"selected":`, `"selected":null,"selected":`},
	}
	for _, change := range changes {
		bad = append(bad, `{"action":"profile_import","params":{"number":1,"profile":`+strings.Replace(offlineDocument, change[0], change[1], 1)+`}}`)
	}
	for _, field := range []string{"handle", "bookmark", "url", "path", "nativeRef", "data", "files", "token"} {
		for _, anchor := range []string{`"version":1`, `"key":"s0"`, `"source_key":"s0"`} {
			dirty := strings.Replace(offlineDocument, anchor, anchor+`,"`+field+`":"secret"`, 1)
			bad = append(bad, `{"action":"profile_import","params":{"number":1,"profile":`+dirty+`}}`)
		}
	}
	for _, body := range bad {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("invalid request queued")
		}
	}
}

func TestOfflineProfileDocumentBoundAndEmptyLibrary(t *testing.T) {
	var data map[string]any
	if err := json.Unmarshal([]byte(offlineDocument), &data); err != nil {
		t.Fatal(err)
	}
	data["sources"] = []any{}
	data["selected"] = nil
	raw, _ := json.Marshal(data)
	if !validOfflineProfile(raw) {
		t.Fatal("empty portable profile rejected")
	}
	rows := []any{}
	for i := 0; i < 32; i++ {
		rows = append(rows, map[string]any{"key": "s" + jsonNumber(i), "name": strings.Repeat("a", 512), "kind": "file"})
	}
	data["sources"] = rows
	raw, _ = json.Marshal(data)
	if validOfflineProfile(raw) {
		t.Fatal("oversized document accepted")
	}
}

func jsonNumber(value int) string { b, _ := json.Marshal(value); return string(b) }

func TestOfflineProfileOperatorScopesAreDistinct(t *testing.T) {
	token := strings.Repeat("o", 32)
	digest := sha256.Sum256([]byte(token))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "profile-reader", CredentialSHA256: hex.EncodeToString(digest[:]), DeviceIDs: []string{"first"}, Actions: []string{"profile_export"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, `{"action":"profile_export","params":{"number":1}}`, nil), 202)
	expect(t, request(s, "POST", "/api/requests?device_id=second", token, `{"action":"profile_export","params":{"number":1}}`, nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, `{"action":"profile_save","params":{"number":1}}`, nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, `{"action":"profile_import","params":{"number":1,"profile":`+offlineDocument+`}}`, nil), 403)
	for _, action := range []string{"profile_save", "profile_export", "profile_import"} {
		if !config.ValidCommandAction(action) {
			t.Fatalf("missing scope %s", action)
		}
	}
}

func TestOfflineProfileResponseRejectsLeakedAuthorityAndWrongSlot(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"profile_export","params":{"number":1}}`)
	response := `{"provider":"offline","number":1,"profile":` + offlineDocument + `}`
	for _, bad := range []string{
		strings.Replace(response, `"number":1`, `"number":2`, 1),
		strings.Replace(response, `"version":1`, `"version":1,"bookmark":"private"`, 1),
		strings.Replace(response, `"key":"s0"`, `"key":"s0","path":"/private/movie"`, 1),
		strings.Replace(response, `"name":"Movies"`, `"name":"`+strings.Repeat("x", 17000)+`"`, 1),
	} {
		expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":`+bad+`}`, nil), 400)
		if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 {
			t.Fatal("invalid response consumed pending export")
		}
	}
	valid := `{"id":"` + id + `","status":"ok","data":` + response + `}`
	expect(t, request(s, "POST", "/api/responses", firstToken, valid, nil), 200)
	// Duplicate responses remain subject to the original request's schema.
	expect(t, request(s, "POST", "/api/responses", firstToken, strings.Replace(valid, `"number":1`, `"number":2`, 1), nil), 400)
	expect(t, request(s, "POST", "/api/responses", firstToken, valid, nil), 200)
}

func TestOfflineProfileSaveAndImportResponseAreBounded(t *testing.T) {
	row := `{"number":1,"name":"Movies","active":true,"configured":true,"binding_required":false,"source_count":1,"selected_name":"Винни-Пух.mp4"}`
	for _, action := range []string{"profile_save", "profile_import"} {
		s := newTestServer(t)
		params := `{"number":1}`
		if action == "profile_import" {
			params = `{"number":1,"profile":` + offlineDocument + `}`
		}
		id := rpcID(t, s, `{"action":"`+action+`","params":`+params+`}`)
		reply := `{"provider":"offline","profile":` + row + `,"saved":true,"binding_required":false}`
		for _, invalid := range []string{
			strings.Replace(reply, `"saved":true`, `"saved":1`, 1),
			strings.Replace(reply, `"active":true`, `"active":1`, 1),
			strings.Replace(reply, `"number":1`, `"number":2`, 1),
			strings.Replace(reply, `"selected_name":`, `"nativeRef":"secret","selected_name":`, 1),
			strings.Replace(reply, `"source_count":1`, `"source_count":0`, 1),
		} {
			expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":`+invalid+`}`, nil), 400)
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":`+reply+`}`, nil), 200)
	}
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"profile_save","params":{"number":1}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"rejected","data":{"error":"Import into an inactive local profile, then load it"}}`, nil), 200)
}
