package control

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

type torrentCatalogFixture struct {
	Name   string          `json:"name"`
	Params json.RawMessage `json:"params"`
	Status string          `json:"status"`
	Data   json.RawMessage `json:"data"`
}

func torrentCatalogFixtures(t *testing.T) (valid, badRequests, badResults []torrentCatalogFixture) {
	t.Helper()
	b, err := os.ReadFile("../../contracts/torrent-catalog-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Schema      int                     `json:"schema"`
		Action      string                  `json:"action"`
		Valid       []torrentCatalogFixture `json:"valid"`
		BadRequests []torrentCatalogFixture `json:"invalid_requests"`
		BadResults  []torrentCatalogFixture `json:"invalid_results"`
	}
	if err = json.Unmarshal(b, &fixture); err != nil || fixture.Schema != 1 || fixture.Action != "torrent_catalog" || len(fixture.Valid) == 0 || len(fixture.BadRequests) == 0 || len(fixture.BadResults) == 0 {
		t.Fatal("invalid shared torrent fixture", err)
	}
	return fixture.Valid, fixture.BadRequests, fixture.BadResults
}

func TestTorrentCatalogSharedContract(t *testing.T) {
	valid, badRequests, badResults := torrentCatalogFixtures(t)
	seen := map[string]bool{}
	for _, f := range valid {
		t.Run(f.Name, func(t *testing.T) {
			q := parseTorrentCatalogRequest(f.Params)
			if q == nil || !validTorrentCatalogResult(f.Data, f.Status, q) {
				t.Fatal("valid contract case rejected")
			}
			seen[q.Op] = true
			for field := range transferObject(t, f.Params) {
				bad := transferObject(t, f.Params)
				delete(bad, field)
				if parseTorrentCatalogRequest(transferJSON(t, bad)) != nil {
					t.Fatal("missing request field accepted", field)
				}
			}
			for field := range transferObject(t, f.Data) {
				bad := transferObject(t, f.Data)
				delete(bad, field)
				if validTorrentCatalogResult(transferJSON(t, bad), f.Status, q) {
					t.Fatal("missing result field accepted", field)
				}
			}
		})
	}
	if len(seen) != 9 {
		t.Fatal("fixture must exercise all nine operations")
	}
	for _, f := range badRequests {
		t.Run(f.Name, func(t *testing.T) {
			if parseTorrentCatalogRequest(f.Params) != nil {
				t.Fatal("invalid request accepted")
			}
		})
	}
	for _, f := range badResults {
		t.Run(f.Name, func(t *testing.T) {
			q := parseTorrentCatalogRequest(f.Params)
			if q == nil || validTorrentCatalogResult(f.Data, f.Status, q) {
				t.Fatal("invalid result accepted or broken request fixture")
			}
		})
	}
}

func TestTorrentCatalogRawJSONAndBounds(t *testing.T) {
	valid, _, _ := torrentCatalogFixtures(t)
	for _, f := range valid {
		q := parseTorrentCatalogRequest(f.Params)
		bad := strings.Replace(string(f.Params), `"runtime":`, `"runtime":"duplicate","runtime":`, 1)
		if parseTorrentCatalogRequest([]byte(bad)) != nil {
			t.Fatal("duplicate request field accepted")
		}
		bad = strings.Replace(string(f.Data), `"version":`, `"version":1,"version":`, 1)
		if validTorrentCatalogResult([]byte(bad), f.Status, q) {
			t.Fatal("duplicate result field accepted")
		}
		bad = string(f.Data[:len(f.Data)-1]) + strings.Repeat(" ", maxTorrentCatalogResultBytes) + "}"
		if validTorrentCatalogResult([]byte(bad), f.Status, q) {
			t.Fatal("raw result budget bypassed by whitespace")
		}
		bad = strings.Replace(string(f.Data), `"version": 1`, `"version": 1.0`, 1)
		if bad == string(f.Data) || validTorrentCatalogResult([]byte(bad), f.Status, q) {
			t.Fatal("noninteger version accepted")
		}
	}
	for _, runtime := range []string{`"\ud800"`, `"\udfff"`, `null`, `true`, `1`} {
		if parseTorrentCatalogRequest([]byte(`{"op":"read","runtime":`+runtime+`}`)) != nil {
			t.Fatal("invalid runtime accepted")
		}
	}
	if parseTorrentCatalogRequest([]byte("{\"op\":\"read\",\"runtime\":\"\xff\"}")) != nil {
		t.Fatal("invalid UTF-8 accepted")
	}
	for _, n := range []int{128, 129} {
		runtime := strings.Repeat("r", n)
		q := parseTorrentCatalogRequest(transferJSON(t, map[string]any{"op": "read", "runtime": runtime}))
		if (q != nil) != (n == 128) {
			t.Fatal("wrong runtime length boundary")
		}
		if q != nil {
			data := transferObject(t, valid[0].Data)
			data["runtime"] = transferJSON(t, runtime)
			if !validTorrentCatalogResult(transferJSON(t, data), "ok", q) {
				t.Fatal("128-byte runtime failed result correlation")
			}
		}
	}
	for _, n := range []int{0, 1, maxTorrentCatalogChunkBytes, maxTorrentCatalogChunkBytes + 1} {
		encoded := transferJSON(t, base64.StdEncoding.EncodeToString(make([]byte, n)))
		count, ok := torrentCatalogChunk(encoded, maxTorrentCatalogChunkBytes)
		if ok != (n >= 1 && n <= maxTorrentCatalogChunkBytes) || ok && count != n {
			t.Fatal("wrong decoded chunk bound", n)
		}
	}
	// Relay only: arbitrary bytes, including non-JSON bytes, belong to the
	// authenticated immutable snapshot. Full document validation is end-to-end.
	if n, ok := torrentCatalogChunk([]byte(`"/w=="`), 1); !ok || n != 1 {
		t.Fatal("relay interpreted snapshot content")
	}
}

func TestTorrentCatalogSourceSyntaxWithoutFetching(t *testing.T) {
	for _, source := range []string{"magnet:?xt=urn:btih:abc", "https://example.invalid/a.TORRENT?key=x", "http://127.0.0.1/file.torrent", "https://[::1]/file.torrent"} {
		if !validTorrentCatalogSource(transferJSON(t, source)) {
			t.Fatal("valid source syntax rejected")
		}
	}
	// Public-network policy belongs to the owning native service; the relay
	// does not resolve either public or private addresses above.
	for _, source := range []string{"", "file:///private/a.torrent", "https://u:p@example.invalid/a.torrent", "https:///a.torrent", "https://example.invalid/a.mp4", "https://example.invalid/a.torrent\n", "magnet:?x=\u00a0", strings.Repeat("x", 8193)} {
		if validTorrentCatalogSource(transferJSON(t, source)) {
			t.Fatal("invalid source syntax accepted")
		}
	}
}

func TestTorrentCatalogOperationStateAndKind(t *testing.T) {
	valid, _, _ := torrentCatalogFixtures(t)
	for _, f := range valid {
		data := transferObject(t, f.Data)
		if data["operation"] == nil {
			continue
		}
		q := parseTorrentCatalogRequest(f.Params)
		operation := transferObject(t, data["operation"])
		for field := range operation {
			bad := transferObject(t, data["operation"])
			delete(bad, field)
			if validTorrentCatalogOperation(transferJSON(t, bad), q) {
				t.Fatal("missing operation field accepted", f.Name, field)
			}
		}
		operation["unknown"] = transferJSON(t, true)
		if validTorrentCatalogOperation(transferJSON(t, operation), q) {
			t.Fatal("unknown operation field accepted")
		}
	}
}
