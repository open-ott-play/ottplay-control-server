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

func TestTorrentCatalogHTTPBoundAndRetainedResponses(t *testing.T) {
	valid, _, _ := torrentCatalogFixtures(t)
	for _, f := range valid {
		t.Run(f.Name, func(t *testing.T) {
			s := newTestServer(t)
			body := globalRPC(t, "torrent_catalog", f.Params, "")
			expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
			id := rpcID(t, s, body)
			queued := s.devices[0].queue[0]
			q := parseTorrentCatalogRequest(f.Params)
			if queued.torrentCatalog == nil || queued.torrentCatalog.Op != q.Op || queued.permission != "torrent_catalog."+q.Op || !validTorrentCatalogEnvelope(queued.data) {
				t.Fatal("missing bound request descriptor/permission")
			}
			good := transferResponse(t, id, f.Status, f.Data)
			expect(t, request(s, "POST", "/api/responses", secondToken, good, nil), 404)
			bad := transferObject(t, f.Data)
			bad["runtime"] = transferJSON(t, "different-page")
			wrong := transferResponse(t, id, f.Status, transferJSON(t, bad))
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("invalid response consumed request")
			}
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, good, nil), 200)
			}
			if s.devices[0].results[id].torrentCatalog == nil {
				t.Fatal("retained response lost descriptor")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != good {
				t.Fatal("duplicate changed retained response")
			}
		})
	}
}

func TestTorrentCatalogHTTPRejectsMalformedBodiesWithoutConsumption(t *testing.T) {
	valid, badRequests, badResults := torrentCatalogFixtures(t)
	for _, f := range badRequests {
		t.Run(f.Name, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, "torrent_catalog", f.Params, ""), nil), 400)
			if len(s.devices[0].queue) != 0 {
				t.Fatal("invalid request queued")
			}
		})
	}
	for _, f := range badResults {
		t.Run(f.Name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, "torrent_catalog", f.Params, ""))
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, f.Status, f.Data), nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("invalid result consumed request")
			}
		})
	}
	for _, f := range []torrentCatalogFixture{valid[0], valid[17]} {
		s := newTestServer(t)
		body := globalRPC(t, "torrent_catalog", f.Params, "")
		over := body[:len(body)-1] + strings.Repeat(" ", MaxBodyBytes) + "}"
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, over, nil), 413)
		duplicate := strings.Replace(body, `"action":`, `"action":"torrent_catalog","action":`, 1)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, duplicate, nil), 400)
		id := rpcID(t, s, body)
		oversizeData := string(f.Data[:len(f.Data)-1]) + strings.Repeat(" ", maxTorrentCatalogResultBytes) + "}"
		oversizeResult := `{"id":"` + id + `","status":"` + f.Status + `","data":` + oversizeData + `}`
		expect(t, request(s, "POST", "/api/responses", firstToken, oversizeResult, nil), 400)
		duplicateResult := `{"id":"` + id + `","id":"` + id + `","status":"` + f.Status + `","data":` + string(f.Data) + `}`
		expect(t, request(s, "POST", "/api/responses", firstToken, duplicateResult, nil), 400)
		s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, f.Status, f.Data), nil), 404)
	}
}

func TestTorrentCatalogHTTPExactOperatorAndDeviceScope(t *testing.T) {
	valid, _, _ := torrentCatalogFixtures(t)
	for _, f := range valid[:9] {
		t.Run(f.Name, func(t *testing.T) {
			c := testConfig()
			reader, other := strings.Repeat("q", 32), strings.Repeat("v", 32)
			q := parseTorrentCatalogRequest(f.Params)
			for _, row := range []struct{ id, token string }{{"owner", reader}, {"other", other}} {
				h := sha256.Sum256([]byte(row.token))
				c.Operators = append(c.Operators, config.CommandOperator{ID: row.id, CredentialSHA256: hex.EncodeToString(h[:]), DeviceIDs: []string{"first"}, Actions: []string{"torrent_catalog." + q.Op}})
			}
			s, err := New(c)
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			body := globalRPC(t, "torrent_catalog", f.Params, "")
			expect(t, request(s, "POST", "/api/requests?device_id=second", reader, body, nil), 403)
			denied := valid[0]
			if q.Op == "read" {
				denied = valid[8]
			}
			expect(t, request(s, "POST", "/api/requests?device_id=first", reader, globalRPC(t, "torrent_catalog", denied.Params, ""), nil), 403)
			accepted := request(s, "POST", "/api/requests?device_id=first", reader, body, nil)
			expect(t, accepted, 202)
			var result map[string]string
			if err = json.Unmarshal(accepted.Body.Bytes(), &result); err != nil {
				t.Fatal(err)
			}
			id := result["id"]
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, f.Status, f.Data), nil), 200)
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, reader, "", nil), 200)
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
		})
	}
}

func TestTorrentCatalogHTTPCapabilityOptionalStrictAndPlayerOwned(t *testing.T) {
	for _, raw := range []string{`{}`, `{"torrent_catalog":{"version":1}}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		body := transferResponse(t, id, "ok", json.RawMessage(raw))
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		if got.Body.String() != body {
			t.Fatal("controller modified capability advertisement")
		}
	}
	for _, raw := range []string{`null`, `true`, `{}`, `{"version":true}`, `{"version":1.0}`, `{"version":2}`, `{"version":1,"version":1}`, `{"version":1,"endpoint":"private"}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		body := `{"id":"` + id + `","status":"ok","data":{"torrent_catalog":` + raw + `}}`
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 400)
	}
}

func TestTorrentCatalogJournalNoPayloadAndNoTransportReplay(t *testing.T) {
	valid, _, _ := torrentCatalogFixtures(t)
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	key := "torrent-metadata-0001"
	f := valid[2]
	params := transferObject(t, f.Params)
	params["source"] = transferJSON(t, "https://example.invalid/private.torrent?token=private-fixture")
	body := globalRPC(t, "torrent_catalog", transferJSON(t, params), key)
	id := rpcID(t, s, body)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 1 {
		t.Fatal("idempotency replay enqueued a second operation")
	}
	params["source"] = transferJSON(t, "https://example.invalid/different.torrent")
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, "torrent_catalog", transferJSON(t, params), key), nil), 409)
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.Data), nil), 200)
	if err = s.Close(); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(c.Journal.Path)
	if err != nil || strings.Contains(string(b), "private-fixture") || strings.Contains(string(b), "source") || strings.Contains(string(b), "params") {
		t.Fatal("journal retained source/payload", err)
	}
	s, err = New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 0 {
		t.Fatal("journal recovery replayed torrent operation")
	}
}
