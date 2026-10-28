package control

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"
)

func globalItemsEntry(t *testing.T) json.RawMessage {
	t.Helper()
	return json.RawMessage(`{"title":"News","origin":{"provider":"m3u","profile":"Home","subprofile":null,"sourceAlias":"TV"},"locator":{"kind":"channel","channelId":"001"}}`)
}
func globalItemsFixtures(t *testing.T) []globalHTTPFixture {
	t.Helper()
	revision, context := strings.Repeat("1", 64), strings.Repeat("2", 64)
	result := []globalHTTPFixture{}
	for _, op := range []string{"list", "current", "play"} {
		q := map[string]any{"op": op, "runtime": "items-runtime", "subject": "global"}
		r := map[string]any{"version": 1, "op": op, "runtime": "items-runtime", "subject": "global", "revision": revision, "context_revision": context}
		switch op {
		case "list":
			q["cursor"], q["max_entries"] = nil, 100
			r["cursor"], r["page_offset"], r["total"], r["next_cursor"], r["unresolved_legacy_count"] = nil, 0, 1, nil, 0
			r["entries"] = []any{map[string]any{"id": globalTestOperation, "entry": globalItemsEntry(t)}}
		case "current":
			r["entry"], r["unavailable_reason"] = globalItemsEntry(t), nil
		case "play":
			q["id"], q["expected_revision"], q["expected_context_revision"] = globalTestOperation, revision, context
			r["id"], r["dispatched"] = globalTestOperation, true
		}
		result = append(result, globalHTTPFixture{"favorites_global_items", op, transferJSON(t, q), transferJSON(t, r)})
	}
	return result
}
func TestGlobalItemsStrictThreeOperations(t *testing.T) {
	for _, f := range globalItemsFixtures(t) {
		t.Run(f.op, func(t *testing.T) {
			q := parseFavoritesGlobalItemsRequest(f.params)
			if q == nil || !validFavoritesGlobalItemsResult(f.result, "ok", q) {
				t.Fatal("valid fixture rejected")
			}
			for field := range transferObject(t, f.params) {
				bad := transferObject(t, f.params)
				delete(bad, field)
				if parseFavoritesGlobalItemsRequest(transferJSON(t, bad)) != nil {
					t.Fatal("missing request field", field)
				}
			}
			for field := range transferObject(t, f.result) {
				bad := transferObject(t, f.result)
				delete(bad, field)
				if validFavoritesGlobalItemsResult(transferJSON(t, bad), "ok", q) {
					t.Fatal("missing result field", field)
				}
			}
			for _, field := range []string{"runtime", "op", "subject"} {
				bad := transferObject(t, f.result)
				bad[field] = transferJSON(t, "other")
				if validFavoritesGlobalItemsResult(transferJSON(t, bad), "ok", q) {
					t.Fatal("uncorrelated result", field)
				}
			}
			for _, status := range []string{"rejected", "unsupported", "unknown"} {
				if validFavoritesGlobalItemsResult(f.result, status, q) {
					t.Fatal("success under wrong status")
				}
			}
			bad := transferObject(t, f.result)
			bad["token"] = transferJSON(t, "private")
			if validFavoritesGlobalItemsResult(transferJSON(t, bad), "ok", q) {
				t.Fatal("unknown result field")
			}
			errorData := transferJSON(t, map[string]any{"version": 1, "runtime": q.Runtime, "op": q.Op, "subject": "global", "error": "runtime_changed"})
			for _, status := range []string{"rejected", "unsupported"} {
				if !validFavoritesGlobalItemsResult(errorData, status, q) {
					t.Fatal("valid error rejected")
				}
			}
			if validFavoritesGlobalItemsResult(errorData, "ok", q) {
				t.Fatal("error claimed success")
			}
			padded := append(append([]byte{}, f.result[:len(f.result)-1]...), []byte(strings.Repeat(" ", maxFavoritesGlobalItemsMetadata))...)
			padded = append(padded, '}')
			if validFavoritesGlobalItemsResult(padded, "ok", q) {
				t.Fatal("raw metadata cap bypassed")
			}
			duplicate := strings.Replace(string(f.params), `"subject":"global"`, `"subject":"global","subject":"global"`, 1)
			if parseFavoritesGlobalItemsRequest(json.RawMessage(duplicate)) != nil {
				t.Fatal("duplicate key accepted")
			}
		})
	}
}
func TestGlobalItemsAdaptivePaginationAndCorrelations(t *testing.T) {
	f := globalItemsFixtures(t)[0]
	q := parseFavoritesGlobalItemsRequest(f.params)
	first := transferObject(t, f.result)
	first["total"] = json.RawMessage("2")
	first["next_cursor"] = transferJSON(t, "page-2")
	if !validFavoritesGlobalItemsResult(transferJSON(t, first), "ok", q) {
		t.Fatal("adaptive short page rejected")
	}
	for field, value := range map[string]any{"cursor": "different", "page_offset": 1, "total": 0, "next_cursor": nil, "unresolved_legacy_count": 101} {
		bad := transferObject(t, transferJSON(t, first))
		bad[field] = transferJSON(t, value)
		if validFavoritesGlobalItemsResult(transferJSON(t, bad), "ok", q) {
			t.Fatal("invalid first page", field)
		}
	}
	next := transferObject(t, transferJSON(t, first))
	next["cursor"] = transferJSON(t, "page-2")
	next["page_offset"] = json.RawMessage("1")
	next["next_cursor"] = json.RawMessage("null")
	continuation := *q
	continuation.Cursor = "page-2"
	if !validFavoritesGlobalItemsResult(transferJSON(t, next), "ok", &continuation) {
		t.Fatal("continuation rejected")
	}
	next["next_cursor"] = transferJSON(t, "page-2")
	if validFavoritesGlobalItemsResult(transferJSON(t, next), "ok", &continuation) {
		t.Fatal("cursor self-loop accepted")
	}
	empty := transferObject(t, f.result)
	empty["total"] = json.RawMessage("0")
	empty["entries"] = json.RawMessage(`[]`)
	if !validFavoritesGlobalItemsResult(transferJSON(t, empty), "ok", q) {
		t.Fatal("empty list rejected")
	}
	for _, value := range []json.RawMessage{json.RawMessage("true"), json.RawMessage("1.0"), json.RawMessage("0"), json.RawMessage("101")} {
		bad := transferObject(t, f.params)
		bad["max_entries"] = value
		if parseFavoritesGlobalItemsRequest(transferJSON(t, bad)) != nil {
			t.Fatal("invalid integer accepted")
		}
	}
	entries := []any{map[string]any{"id": globalTestOperation, "entry": globalItemsEntry(t)}, map[string]any{"id": globalTestTransaction, "entry": globalItemsEntry(t)}}
	duplicate := transferObject(t, transferJSON(t, first))
	duplicate["entries"] = transferJSON(t, entries)
	duplicate["next_cursor"] = json.RawMessage("null")
	if validFavoritesGlobalItemsResult(transferJSON(t, duplicate), "ok", q) {
		t.Fatal("duplicate portable identity accepted")
	}
	changedEntry := transferObject(t, globalItemsEntry(t))
	changedEntry["title"] = transferJSON(t, "Other title")
	entries[1] = map[string]any{"id": globalTestTransaction, "entry": changedEntry}
	duplicate["entries"] = transferJSON(t, entries)
	if validFavoritesGlobalItemsResult(transferJSON(t, duplicate), "ok", q) {
		t.Fatal("title-only difference hid duplicate identity")
	}
	current := globalItemsFixtures(t)[1]
	cq := parseFavoritesGlobalItemsRequest(current.params)
	unavailable := transferObject(t, current.result)
	unavailable["entry"] = json.RawMessage("null")
	unavailable["unavailable_reason"] = transferJSON(t, "standby")
	if !validFavoritesGlobalItemsResult(transferJSON(t, unavailable), "ok", cq) {
		t.Fatal("explicit unavailable current rejected")
	}
	unavailable["entry"] = globalItemsEntry(t)
	if validFavoritesGlobalItemsResult(transferJSON(t, unavailable), "ok", cq) {
		t.Fatal("entry with unavailable reason accepted")
	}
	play := globalItemsFixtures(t)[2]
	pq := parseFavoritesGlobalItemsRequest(play.params)
	for field, value := range map[string]any{"revision": strings.Repeat("9", 64), "context_revision": strings.Repeat("9", 64), "id": globalTestTransaction, "dispatched": false, "outcome": "verified"} {
		bad := transferObject(t, play.result)
		bad[field] = transferJSON(t, value)
		if validFavoritesGlobalItemsResult(transferJSON(t, bad), "ok", pq) {
			t.Fatal("invalid play result", field)
		}
	}
}
func TestGlobalItemsPortableV3Corpus(t *testing.T) {
	raw, err := os.ReadFile("../../contracts/global-favorites-portable-v1.fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Names struct {
			Cases []struct {
				ID    string          `json:"id"`
				Entry json.RawMessage `json:"entry"`
				Valid bool            `json:"valid"`
			} `json:"cases"`
		} `json:"v3_channel_name"`
		Text struct {
			Cases []struct {
				ID       string          `json:"id"`
				Value    json.RawMessage `json:"value"`
				Maximum  int             `json:"max_utf16_units"`
				Expected bool            `json:"expected"`
			} `json:"cases"`
		} `json:"v3_text_semantics"`
	}
	if err = json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	if len(corpus.Names.Cases) == 0 || len(corpus.Text.Cases) == 0 {
		t.Fatal("missing parity fixtures")
	}
	for _, row := range corpus.Names.Cases {
		_, valid := favoritesGlobalPortableIdentity(row.Entry)
		if valid != row.Valid {
			t.Error("portable name parity", row.ID)
		}
	}
	for _, row := range corpus.Text.Cases {
		_, valid := favoritesGlobalPortableText(row.Value, row.Maximum)
		valid = valid && validFavoritesTransferJSON(row.Value)
		if valid != row.Expected {
			t.Error("portable text parity", row.ID)
		}
	}
	for _, row := range []struct {
		provider string
		locator  map[string]any
	}{
		{"plex", map[string]any{"kind": "plex-media", "serverId": "PMS-1", "ratingKey": "123"}},
		{"local", map[string]any{"kind": "local-media", "rootAlias": "Movies", "relativePath": "Dir/file.mp4"}},
		{"vportal", map[string]any{"kind": "vportal-media", "idField": "stream_id", "id": "001"}},
	} {
		entry := transferObject(t, globalItemsEntry(t))
		origin := transferObject(t, entry["origin"])
		origin["provider"] = transferJSON(t, row.provider)
		entry["origin"] = transferJSON(t, origin)
		entry["locator"] = transferJSON(t, row.locator)
		if _, valid := favoritesGlobalPortableIdentity(transferJSON(t, entry)); !valid {
			t.Fatal("valid provider rejected", row.provider)
		}
		row.locator["token"] = "private"
		entry["locator"] = transferJSON(t, row.locator)
		if _, valid := favoritesGlobalPortableIdentity(transferJSON(t, entry)); valid {
			t.Fatal("private field accepted")
		}
	}
	for _, path := range []string{"/root/file", "../file", "Dir/../file", "C:file", "Dir\\file", "Dir//file", "https://example.test/x"} {
		entry := transferObject(t, globalItemsEntry(t))
		origin := transferObject(t, entry["origin"])
		origin["provider"] = transferJSON(t, "local")
		entry["origin"] = transferJSON(t, origin)
		entry["locator"] = transferJSON(t, map[string]any{"kind": "local-media", "rootAlias": "Movies", "relativePath": path})
		if _, valid := favoritesGlobalPortableIdentity(transferJSON(t, entry)); valid {
			t.Fatal("nonportable local path accepted")
		}
	}
	entry := transferObject(t, globalItemsEntry(t))
	entry["locator"] = transferJSON(t, map[string]any{"kind": "channel", "channelId": "channel:001"})
	if _, valid := favoritesGlobalPortableIdentity(transferJSON(t, entry)); valid {
		t.Fatal("ordinal channel identity accepted")
	}
}
func TestGlobalItemsPortableV4NestedM3UPortal(t *testing.T) {
	raw, err := os.ReadFile("../../contracts/global-favorites-nested-m3u-vportal.fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Contract string `json:"contract"`
		Cases    []struct {
			ID    string          `json:"id"`
			Entry json.RawMessage `json:"entry"`
			Valid bool            `json:"valid"`
		} `json:"cases"`
	}
	if err = json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	if corpus.Contract != "joint-unactivated-portable-v1-v4" || len(corpus.Cases) != 5 {
		t.Fatal("missing V4 parity fixtures")
	}
	for _, row := range corpus.Cases {
		t.Run(row.ID, func(t *testing.T) {
			identity, valid := favoritesGlobalPortableIdentity(row.Entry)
			if valid != row.Valid {
				t.Fatal("nested M3U portal parity")
			}
			if valid {
				var original, got map[string]any
				if json.Unmarshal(row.Entry, &original) != nil || json.Unmarshal([]byte(identity), &got) != nil {
					t.Fatal("invalid portable identity JSON")
				}
				delete(original, "title")
				if !reflect.DeepEqual(got, original) {
					t.Fatal("outer origin or locator changed")
				}
			}
			for _, f := range globalItemsFixtures(t)[:2] {
				result := transferObject(t, f.result)
				if f.op == "list" {
					result["entries"] = transferJSON(t, []any{map[string]any{"id": globalTestOperation, "entry": row.Entry}})
				} else {
					result["entry"] = row.Entry
				}
				if validFavoritesGlobalItemsResult(transferJSON(t, result), "ok", parseFavoritesGlobalItemsRequest(f.params)) != row.Valid {
					t.Fatal("nested M3U portal result parity", f.op)
				}
			}
		})
	}
}

func TestGlobalItemsPortableV4ProviderBoundary(t *testing.T) {
	portal := json.RawMessage(`{"kind":"vportal-media","idField":"id","id":"42"}`)
	for _, row := range []struct {
		provider string
		locator  json.RawMessage
		valid    bool
	}{
		{"m3u", portal, true},
		{"vportal", portal, true},
		{"plex", portal, false},
		{"local", portal, false},
		{"stalker", portal, false},
		{"other", portal, false},
		{"m3u", json.RawMessage(`{"kind":"plex-media","serverId":"PMS-1","ratingKey":"42"}`), false},
		{"m3u", json.RawMessage(`{"kind":"local-media","rootAlias":"Movies","relativePath":"movie.mp4"}`), false},
		{"m3u", json.RawMessage(`{"kind":"channel","channelId":"42"}`), true},
		{"m3u", json.RawMessage(`{"kind":"channel-name","name":"News","group":null}`), true},
		{"vportal", json.RawMessage(`{"kind":"channel","channelId":"42"}`), false},
		{"vportal", json.RawMessage(`{"kind":"channel-name","name":"News","group":null}`), false},
	} {
		entry := transferObject(t, globalItemsEntry(t))
		origin := transferObject(t, entry["origin"])
		origin["provider"] = transferJSON(t, row.provider)
		entry["origin"], entry["locator"] = transferJSON(t, origin), row.locator
		if _, valid := favoritesGlobalPortableIdentity(transferJSON(t, entry)); valid != row.valid {
			t.Fatal("provider boundary", row.provider, string(row.locator))
		}
	}
}

func TestGlobalItemsHundredRowsAndRawByteBoundary(t *testing.T) {
	f := globalItemsFixtures(t)[0]
	q := parseFavoritesGlobalItemsRequest(f.params)
	list := transferObject(t, f.result)
	rows := []any{}
	for i := 0; i < 100; i++ {
		entry := transferObject(t, globalItemsEntry(t))
		entry["locator"] = transferJSON(t, map[string]any{"kind": "channel", "channelId": fmt.Sprintf("id-%d", i)})
		rows = append(rows, map[string]any{"id": fmt.Sprintf("00000000-0000-4000-8000-%012d", i), "entry": entry})
	}
	list["entries"] = transferJSON(t, rows)
	list["total"] = json.RawMessage("100")
	raw := transferJSON(t, list)
	if !validFavoritesGlobalItemsResult(raw, "ok", q) {
		t.Fatal("100 bounded rows rejected")
	}
	exact := append(append([]byte{}, raw[:len(raw)-1]...), []byte(strings.Repeat(" ", maxFavoritesGlobalItemsMetadata-len(raw)))...)
	exact = append(exact, '}')
	if len(exact) != maxFavoritesGlobalItemsMetadata || !validFavoritesGlobalItemsResult(exact, "ok", q) {
		t.Fatal("exact raw byte boundary rejected")
	}
	tooLarge := append(append([]byte{}, exact[:len(exact)-1]...), ' ', '}')
	if validFavoritesGlobalItemsResult(tooLarge, "ok", q) {
		t.Fatal("raw byte boundary exceeded")
	}
}
