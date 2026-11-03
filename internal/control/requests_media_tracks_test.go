package control

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
)

type mediaTracksCase struct {
	Name   string          `json:"name"`
	Params json.RawMessage `json:"params"`
	Status string          `json:"status"`
	Data   json.RawMessage `json:"data"`
}

type mediaTracksContract struct {
	Valid           []mediaTracksCase `json:"valid"`
	InvalidRequests []mediaTracksCase `json:"invalid_requests"`
	InvalidResults  []mediaTracksCase `json:"invalid_results"`
}

func mediaTracksFixture(t testing.TB) mediaTracksContract {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/media-tracks-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture mediaTracksContract
	if err = json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Valid) < 36 || len(fixture.InvalidRequests) < 21 || len(fixture.InvalidResults) < 50 {
		t.Fatal("incomplete shared media tracks fixture")
	}
	return fixture
}

func (f mediaTracksContract) named(t testing.TB, name string) mediaTracksCase {
	t.Helper()
	for _, row := range f.Valid {
		if row.Name == name {
			return row
		}
	}
	t.Fatalf("missing media tracks fixture %q", name)
	return mediaTracksCase{}
}

func TestMediaTracksSharedContract(t *testing.T) {
	f := mediaTracksFixture(t)
	for _, row := range f.Valid {
		t.Run(row.Name, func(t *testing.T) {
			q := parseMediaTracksRequest(row.Params)
			if q == nil || !validMediaTracksResult(row.Data, row.Status, q) {
				t.Fatal("valid shared fixture rejected")
			}
		})
	}
	for _, row := range f.InvalidRequests {
		t.Run(row.Name, func(t *testing.T) {
			if parseMediaTracksRequest(row.Params) != nil {
				t.Fatal("invalid shared request accepted")
			}
		})
	}
	for _, row := range f.InvalidResults {
		t.Run(row.Name, func(t *testing.T) {
			q := parseMediaTracksRequest(row.Params)
			if q == nil {
				t.Fatal("invalid result fixture has invalid request")
			}
			if validMediaTracksResult(row.Data, row.Status, q) {
				t.Fatal("invalid shared result accepted")
			}
		})
	}
}

func TestMediaTracksNestedDuplicatesAndInvalidJSON(t *testing.T) {
	f := mediaTracksFixture(t)
	selectCase := f.named(t, "select-audio-requested")
	for _, key := range []string{"op", "runtime", "playback", "source_generation", "catalog", "target", "kind", "mode", "id"} {
		needle := `"` + key + `":`
		bad := bytes.Replace(selectCase.Params, []byte(needle), []byte(needle+`null,`+needle), 1)
		if parseMediaTracksRequest(bad) != nil {
			t.Fatalf("duplicate request key %s accepted", key)
		}
	}
	listCase := f.named(t, "list-mixed")
	q := parseMediaTracksRequest(listCase.Params)
	for _, key := range []string{"version", "runtime", "op", "scope", "playback", "source_generation", "catalog", "tracks", "id", "kind", "label", "language", "codec", "selectable", "observed", "audio", "state", "track_id", "age_ms"} {
		needle := `"` + key + `":`
		bad := bytes.Replace(listCase.Data, []byte(needle), []byte(needle+`null,`+needle), 1)
		if validMediaTracksResult(bad, "ok", q) {
			t.Fatalf("duplicate result key %s accepted", key)
		}
	}
	for _, raw := range []json.RawMessage{nil, []byte(`null`), []byte(`[]`), append(append([]byte{}, listCase.Data...), []byte(` {}`)...), bytes.Replace(listCase.Data, []byte(`"label":`), []byte(`"label":"\ud800","extra":`), 1)} {
		if validMediaTracksResult(raw, "ok", q) {
			t.Fatal("malformed result accepted")
		}
	}
	if validMediaTracksResult(listCase.Data, "ok", nil) || validMediaTracksResult(listCase.Data, "pending", q) {
		t.Fatal("unbound or unrecognized result accepted")
	}
}

func TestMediaTracksCatalogAndTextBounds(t *testing.T) {
	f := mediaTracksFixture(t).named(t, "list-unobserved")
	q := parseMediaTracksRequest(f.Params)
	m := transferObject(t, f.Data)
	rows := make([]map[string]any, 0, maxMediaTracks)
	for _, kind := range []string{"audio", "subtitle"} {
		for i := range maxMediaTracksPerKind {
			rows = append(rows, map[string]any{"id": fmt.Sprintf("%s-%d", kind, i), "kind": kind, "label": strings.Repeat("é", 64), "language": nil, "codec": nil, "selectable": false})
		}
	}
	m["tracks"] = transferJSON(t, rows)
	if !validMediaTracksResult(transferJSON(t, m), "ok", q) {
		t.Fatal("complete catalog at row and UTF-8 limits rejected")
	}
	rows[64]["kind"] = "audio"
	m["tracks"] = transferJSON(t, rows)
	if validMediaTracksResult(transferJSON(t, m), "ok", q) {
		t.Fatal("65 tracks of one kind accepted")
	}
	rows[64]["kind"] = "subtitle"
	rows = append(rows, map[string]any{"id": "overflow", "kind": "subtitle", "label": "Track 129", "language": nil, "codec": nil, "selectable": true})
	m["tracks"] = transferJSON(t, rows)
	if validMediaTracksResult(transferJSON(t, m), "ok", q) {
		t.Fatal("129 tracks accepted")
	}
	for _, text := range []string{"AC/DC", "English / Commentary", "Русский — AC3", strings.Repeat("é", 64)} {
		if !validMediaTracksLabel(transferJSON(t, text)) {
			t.Fatalf("safe label rejected: %q", text)
		}
	}
	for _, text := range []string{"", "  ", strings.Repeat("é", 65), "name\nvalue", "name\u007fvalue", "name\u0085value", "name\u202evalue", "https://example.invalid/private", "file:///tmp/movie", "magnet:?xt=fixture", "/Users/example/movie", "~/Movies/file", `C:\Movies\file`, `\\host\share`, "token=fixture", "api_key:fixture", "authorization=fixture", "password=fixture"} {
		if validMediaTracksLabel(transferJSON(t, text)) {
			t.Fatalf("unsafe label accepted: %q", text)
		}
	}
}

func TestMediaTracksResultByteBudgetAndExactBooleans(t *testing.T) {
	f := mediaTracksFixture(t).named(t, "select-audio-requested")
	q := parseMediaTracksRequest(f.Params)
	raw := bytes.TrimSpace(f.Data)
	atLimit := append(append(append([]byte{}, raw[:len(raw)-1]...), bytes.Repeat([]byte(" "), maxMediaTracksResultBytes-len(raw))...), '}')
	if len(atLimit) != maxMediaTracksResultBytes || !validMediaTracksResult(atLimit, "ok", q) {
		t.Fatal("result at exact byte budget rejected")
	}
	if validMediaTracksResult(append([]byte(" "), atLimit...), "ok", q) {
		t.Fatal("result beyond byte budget accepted")
	}
	for key, values := range map[string][]json.RawMessage{
		"version":  {[]byte(`true`), []byte(`1.0`), []byte(`1e0`), []byte(`null`)},
		"accepted": {[]byte(`false`), []byte(`1`), []byte(`"true"`), []byte(`null`)},
	} {
		for _, value := range values {
			m := transferObject(t, f.Data)
			m[key] = value
			if validMediaTracksResult(transferJSON(t, m), "ok", q) {
				t.Fatalf("invalid %s=%s accepted", key, value)
			}
		}
	}
}
