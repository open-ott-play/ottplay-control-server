package control

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestTorrentChildrenSharedContractAndHTTP(t *testing.T) {
	b, err := os.ReadFile("../../contracts/torrent-indexer-children-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures struct {
		Valid       []torrentIndexerFixture `json:"valid"`
		BadRequests []torrentIndexerFixture `json:"invalid_requests"`
		BadResults  []torrentIndexerFixture `json:"invalid_results"`
	}
	if json.Unmarshal(b, &fixtures) != nil || len(fixtures.Valid) == 0 || len(fixtures.BadRequests) == 0 || len(fixtures.BadResults) == 0 {
		t.Fatal("invalid child-indexer contract fixtures")
	}
	for _, f := range fixtures.Valid {
		t.Run(f.Name, func(t *testing.T) {
			q := parseTorrentIndexerRequest(f.Params)
			if q == nil || !validTorrentIndexerResult(f.Data, f.Status, q) {
				t.Fatal("valid child-indexer contract rejected")
			}
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, "torrent_indexer", f.Params, ""))
			queued := s.devices[0].queue[0]
			if queued.torrentIndexer == nil || queued.torrentIndexer.IndexerID != q.IndexerID || queued.permission != "torrent_indexer."+q.Op {
				t.Fatal("request lost child scope or exact permission")
			}
			bad := transferObject(t, f.Data)
			bad["runtime"] = transferJSON(t, "different-runtime")
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, f.Status, transferJSON(t, bad)), nil), 400)
			good := transferResponse(t, id, f.Status, f.Data)
			expect(t, request(s, "POST", "/api/responses", firstToken, good, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, good, nil), 200)
			if s.devices[0].results[id].torrentIndexer.IndexerID != q.IndexerID {
				t.Fatal("retained result lost child scope")
			}
		})
	}
	for _, f := range fixtures.BadRequests {
		t.Run(f.Name, func(t *testing.T) {
			if parseTorrentIndexerRequest(f.Params) != nil {
				t.Fatal("invalid child request accepted")
			}
		})
	}
	for _, f := range fixtures.BadResults {
		t.Run(f.Name, func(t *testing.T) {
			q := parseTorrentIndexerRequest(f.Params)
			if q == nil || validTorrentIndexerResult(f.Data, f.Status, q) {
				t.Fatal("invalid child result accepted or invalid fixture request")
			}
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, "torrent_indexer", f.Params, ""))
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, f.Status, f.Data), nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("invalid result consumed the queued request")
			}
		})
	}
}

func TestTorrentChildrenCapabilityIsAdditiveAndStrict(t *testing.T) {
	marker := `{"version":1,"operations":["indexers"],"scopedOperations":["caps","search"]}`
	for _, tc := range []struct {
		base, marker string
		status       int
	}{
		{`"torrent_indexer":{"version":1},`, marker, 200},
		{`"torrent_indexer":{"version":1},`, strings.Replace(marker, `["caps","search"]`, `["search","caps"]`, 1), 200},
		{"", marker, 400},
		{`"torrent_indexer":{"version":1},`, strings.Replace(marker, `"version":1`, `"version":true`, 1), 400},
		{`"torrent_indexer":{"version":1},`, strings.Replace(marker, `"search"`, `"caps"`, 1), 400},
		{`"torrent_indexer":{"version":1},`, strings.Replace(marker, `"indexers"`, `"configure"`, 1), 400},
		{`"torrent_indexer":{"version":1},`, `{"version":1}`, 400},
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		data := json.RawMessage(`{` + tc.base + `"torrent_indexer_children":` + tc.marker + `}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", data), nil), tc.status)
	}
}

func TestTorrentChildrenBoundedList(t *testing.T) {
	rows := make([]map[string]any, 0, 257)
	for i := range 257 {
		rows = append(rows, map[string]any{"id": strings.Repeat("a", i/26+1) + string(rune('a'+i%26)), "label": "Fixture", "language": nil, "type": nil})
	}
	q := parseTorrentIndexerRequest(json.RawMessage(`{"version":1,"runtime":"runtime","op":"indexers","sourceId":"home"}`))
	for _, count := range []int{256, 257} {
		data := transferJSON(t, map[string]any{"version": 1, "runtime": "runtime", "op": "indexers", "sourceId": "home", "schema": 1, "supported": true, "indexers": rows[:count]})
		if validTorrentIndexerResult(data, "ok", q) != (count == 256) {
			t.Fatal("incorrect indexer count bound", count)
		}
	}
}
