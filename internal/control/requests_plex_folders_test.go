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

func plexFolderFixture(t *testing.T) (map[string]json.RawMessage, map[string]json.RawMessage) {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/plex-folders-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Requests, Responses map[string]json.RawMessage
	}
	if err = json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture.Requests, fixture.Responses
}

func TestPlexFolderFixtureBoundResults(t *testing.T) {
	requests, responses := plexFolderFixture(t)
	for _, op := range []string{"list", "add", "remove", "clear"} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"plex_folders","params":`+string(requests[op])+`}`)
		if s.devices[0].queue[0].plexFolders == nil {
			t.Fatal("missing folder request binding")
		}
		var data map[string]any
		_ = json.Unmarshal(responses[op], &data)
		for range 2 {
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 200)
		}
		data["runtime"] = "other-page"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
	}
}

func TestPlexFolderRejectMalformedRequests(t *testing.T) {
	requests, _ := plexFolderFixture(t)
	for _, alter := range []func(map[string]any){
		func(m map[string]any) { m["path"] = "https://example.invalid/library/sections/16/all" },
		func(m map[string]any) { m["path"] = "/library/sections/16/all?token=secret" },
		func(m map[string]any) { m["path"] = "/library/sections/16/../all" },
		func(m map[string]any) { m["revision"] = nil },
		func(m map[string]any) { m["title"] = strings.Repeat("😀", 513) },
		func(m map[string]any) { m["extra"] = true },
	} {
		var params map[string]any
		_ = json.Unmarshal(requests["add"], &params)
		alter(params)
		raw, _ := json.Marshal(params)
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"plex_folders","params":`+string(raw)+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid folder mutation was queued")
		}
	}
}

func TestPlexFolderResultValidationPreservesPendingRequest(t *testing.T) {
	requests, responses := plexFolderFixture(t)
	for _, alter := range []func(map[string]any){
		func(m map[string]any) { m["persisted"] = false },
		func(m map[string]any) { m["total"] = 0 },
		func(m map[string]any) { m["total"] = 1001 },
		func(m map[string]any) { m["context"].(map[string]any)["provider"] = "m3u" },
		func(m map[string]any) { m["context"].(map[string]any)["token"] = "new-context" },
		func(m map[string]any) { m["application"].(map[string]any)["reason"] = "next_stream" },
	} {
		var data map[string]any
		_ = json.Unmarshal(responses["add"], &data)
		alter(data)
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"plex_folders","params":`+string(requests["add"])+`}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid result consumed pending mutation")
		}
	}
}

func TestPlexFolderListBoundsAndCanonicalTitles(t *testing.T) {
	requests, responses := plexFolderFixture(t)
	params, _ := decodeObject(requests["list"])
	expected := parsePlexFoldersRequest(params)
	for _, alter := range []func(map[string]any){
		func(m map[string]any) { m["items"] = nil },
		func(m map[string]any) { m["total"] = true },
		func(m map[string]any) { m["next_cursor"] = "again" },
		func(m map[string]any) { m["items"].([]any)[0].(map[string]any)["title"] = strings.Repeat("😀", 513) },
		func(m map[string]any) { m["items"] = append(m["items"].([]any), m["items"].([]any)[0]); m["total"] = 2 },
	} {
		var data map[string]any
		_ = json.Unmarshal(responses["list"], &data)
		alter(data)
		raw, _ := json.Marshal(data)
		if validPlexFoldersResult(raw, "ok", expected) {
			t.Fatal("accepted malformed folder page")
		}
	}
	if !validPlexFolderTitle(json.RawMessage(`"` + strings.Repeat("😀", 512) + `"`)) {
		t.Fatal("valid UTF-16 title boundary rejected")
	}
}

func TestPlexFolderExactOperatorPermissions(t *testing.T) {
	requests, _ := plexFolderFixture(t)
	s := newTestServer(t)
	const token = "folder-reader-credential-at-least-32-bytes"
	hash := sha256.Sum256([]byte(token))
	s.configureOperators([]config.CommandOperator{{ID: "folder-reader", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"plex_folders.list"}}})
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, `{"action":"plex_folders","params":`+string(requests["list"])+`}`, nil), 202)
	for _, op := range []string{"add", "remove", "clear"} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", token, `{"action":"plex_folders","params":`+string(requests[op])+`}`, nil), 403)
	}
	for _, action := range []string{"plex_folders", "plex_folders.*", "plex_folders.replace"} {
		if config.ValidCommandAction(action) {
			t.Fatal("accepted broad/unknown folder permission")
		}
	}
}
