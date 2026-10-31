package control

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
)

type torrentIndexerFixture struct {
	Name   string          `json:"name"`
	Params json.RawMessage `json:"params"`
	Status string          `json:"status"`
	Data   json.RawMessage `json:"data"`
}

func torrentIndexerFixtures(t *testing.T) (valid, badRequests, badResults []torrentIndexerFixture) {
	t.Helper()
	b, err := os.ReadFile("../../contracts/torrent-indexer-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Schema      int                     `json:"schema"`
		Action      string                  `json:"action"`
		Valid       []torrentIndexerFixture `json:"valid"`
		BadRequests []torrentIndexerFixture `json:"invalid_requests"`
		BadResults  []torrentIndexerFixture `json:"invalid_results"`
	}
	if err = json.Unmarshal(b, &fixture); err != nil || fixture.Schema != 1 || fixture.Action != "torrent_indexer" || len(fixture.Valid) == 0 || len(fixture.BadRequests) == 0 || len(fixture.BadResults) == 0 {
		t.Fatal("invalid shared torrent fixture", err)
	}
	return fixture.Valid, fixture.BadRequests, fixture.BadResults
}

func TestTorrentIndexerSharedContract(t *testing.T) {
	valid, badRequests, badResults := torrentIndexerFixtures(t)
	seen := map[string]bool{}
	for _, f := range valid {
		t.Run(f.Name, func(t *testing.T) {
			q := parseTorrentIndexerRequest(f.Params)
			if q == nil || !validTorrentIndexerResult(f.Data, f.Status, q) {
				t.Fatal("valid contract case rejected")
			}
			seen[q.Op] = true
			for field := range transferObject(t, f.Params) {
				bad := transferObject(t, f.Params)
				delete(bad, field)
				if parseTorrentIndexerRequest(transferJSON(t, bad)) != nil {
					t.Fatal("missing request field accepted", field)
				}
			}
			for field := range transferObject(t, f.Data) {
				bad := transferObject(t, f.Data)
				delete(bad, field)
				if validTorrentIndexerResult(transferJSON(t, bad), f.Status, q) {
					t.Fatal("missing result field accepted", field)
				}
			}
		})
	}
	if len(seen) != 6 {
		t.Fatal("fixture must exercise all six operations")
	}
	for _, f := range badRequests {
		t.Run(f.Name, func(t *testing.T) {
			if parseTorrentIndexerRequest(f.Params) != nil {
				t.Fatal("invalid request accepted")
			}
		})
	}
	for _, f := range badResults {
		t.Run(f.Name, func(t *testing.T) {
			q := parseTorrentIndexerRequest(f.Params)
			if q == nil || validTorrentIndexerResult(f.Data, f.Status, q) {
				t.Fatal("invalid result accepted or broken request fixture")
			}
		})
	}
}

func TestTorrentIndexerScalarBounds(t *testing.T) {
	valid, _, _ := torrentIndexerFixtures(t)
	base := transferObject(t, valid[1].Params)
	for key, maximum := range map[string]int{"label": 128, "apiKey": 2048} {
		base[key] = transferJSON(t, strings.Repeat("é", maximum/2))
		if parseTorrentIndexerRequest(transferJSON(t, base)) == nil {
			t.Fatal("maximum UTF-8 field rejected", key)
		}
		base[key] = transferJSON(t, strings.Repeat("é", maximum/2)+"x")
		if parseTorrentIndexerRequest(transferJSON(t, base)) != nil {
			t.Fatal("UTF-8 byte bound ignored", key)
		}
		base = transferObject(t, valid[1].Params)
	}
	for _, raw := range []string{`[1,9007199254740991]`, `[]`} {
		if !torrentIndexerCategories([]byte(raw), 16) {
			t.Fatal("valid category ids rejected")
		}
	}
	for _, raw := range []string{`[0]`, `[-1]`, `[1.5]`, `[1,1]`, `[9007199254740992]`, `null`} {
		if torrentIndexerCategories([]byte(raw), 16) {
			t.Fatal("invalid category ids accepted")
		}
	}
	for _, value := range []string{"", "not-a-date", "2026-02-30T00:00:00Z", "2026-01-01"} {
		if torrentIndexerPublishedAt(transferJSON(t, value)) {
			t.Fatal("invalid publication timestamp accepted")
		}
	}
	for _, value := range []string{"2026-10-30T12:34:56Z", "2026-10-30T12:34:56.123+01:00"} {
		if !torrentIndexerPublishedAt(transferJSON(t, value)) {
			t.Fatal("valid publication timestamp rejected")
		}
	}
	for _, endpoint := range []string{"http://127.0.0.1:0/api", "http://127.0.0.1:65536/api", "http://127.0.0.1:/api", "http://127.0.0.1/api?", "http://127.0.0.1/api#"} {
		if torrentIndexerEndpoint(transferJSON(t, endpoint)) {
			t.Fatal("invalid endpoint accepted")
		}
	}
	for reason := range torrentIndexerReasons {
		data := transferJSON(t, map[string]any{"version": 1, "runtime": "r", "op": "list", "error": torrentIndexerError, "reason": reason})
		if !validTorrentIndexerResult(data, "rejected", &torrentIndexerRequest{Runtime: "r", Op: "list"}) {
			t.Fatal("documented safe reason rejected")
		}
	}
}

func TestTorrentIndexerOpaqueSourceAndOperationEvidence(t *testing.T) {
	good := "ottplay-local-torrent:home/" + strings.Repeat("a", 32)
	if !validTorrentCatalogSource(transferJSON(t, good)) {
		t.Fatal("opaque local source rejected")
	}
	for _, value := range []string{good + "?key=value", good + "#fragment", good + "/extra", good + "\n", "ottplay-local-torrent://home/" + strings.Repeat("a", 32), "ottplay-local-torrent:Home/" + strings.Repeat("a", 32), "ottplay-local-torrent:home/" + strings.Repeat("A", 32), "ottplay-local-torrent:home/../private"} {
		if validTorrentCatalogSource(transferJSON(t, value)) {
			t.Fatal("malformed opaque source accepted")
		}
	}
	for _, action := range []string{"torrent_indexer", "torrent_catalog"} {
		data := transferJSON(t, map[string]any{"operation_id": strings.Repeat("a", 32), "state": "accepted", "action": action, "evidence": map[string]any{"kind": "none", "generation": nil, "position": nil}})
		if !validInspectOperation(data, strings.Repeat("a", 32)) {
			t.Fatal("torrent operation evidence rejected")
		}
	}
}

func TestTorrentIndexerMaximalSearchAndRawJSON(t *testing.T) {
	categories := make([]int64, 32)
	for i := range categories {
		categories[i] = 9007199254740991 - int64(i)
	}
	languages := make([]string, 16)
	for i := range languages {
		languages[i] = strings.Repeat("a", 30) + fmt.Sprintf("%02d", i)
	}
	items := make([]map[string]any, 100)
	for i := range items {
		items[i] = map[string]any{"resultRef": fmt.Sprintf("%032x", i+1), "title": strings.Repeat("<", 1024), "size": nil, "seeders": nil, "publishedAt": nil, "categoryIds": categories, "languages": languages}
	}
	value := map[string]any{"version": 1, "runtime": "r", "op": "search", "schema": 1, "sourceId": "home", "items": items, "nextOffset": nil, "total": 100}
	raw := transferJSON(t, value)
	q := &torrentIndexerRequest{Runtime: "r", Op: "search", SourceID: "home", Limit: 100}
	if len(raw) <= 256*1024 || !validTorrentIndexerResult(raw, "ok", q) {
		t.Fatal("maximal escaped search page rejected", len(raw))
	}
	value["items"] = append(items, items[0])
	if validTorrentIndexerResult(transferJSON(t, value), "ok", q) {
		t.Fatal("oversized item count accepted")
	}
	if validTorrentIndexerResult([]byte(strings.Replace(string(raw), `"schema":1`, `"schema":1,"schema":1`, 1)), "ok", q) {
		t.Fatal("duplicate result field accepted")
	}
	if parseTorrentIndexerRequest([]byte(`{"version":1,"runtime":"r","op":"list","op":"list"}`)) != nil {
		t.Fatal("duplicate request field accepted")
	}
}
