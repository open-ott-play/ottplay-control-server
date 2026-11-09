package control

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func edemFavoritesFixture(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	data, err := os.ReadFile("../../contracts/global-favorites-edem.fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	var entries map[string]json.RawMessage
	if json.Unmarshal(data, &entries) != nil || len(entries) != 3 {
		t.Fatal("invalid Edem portable favorites fixtures")
	}
	return entries
}

func TestFavoritesGlobalEdemPortableEntriesAndReadResponses(t *testing.T) {
	for name, entry := range edemFavoritesFixture(t) {
		t.Run(name, func(t *testing.T) {
			identity, valid := favoritesGlobalPortableIdentity(entry)
			if !valid {
				t.Fatal("valid native Edem entry rejected")
			}
			var fields map[string]any
			if err := json.Unmarshal(entry, &fields); err != nil {
				t.Fatal(err)
			}
			delete(fields, "title")
			if identity != string(transferJSON(t, fields)) {
				t.Fatal("origin or locator identity was normalized")
			}
			for _, fixture := range globalItemsFixtures(t)[:2] {
				s := newTestServer(t)
				requestBody := transferJSON(t, map[string]any{"action": fixture.action, "params": fixture.params})
				id := rpcID(t, s, string(requestBody))
				result := transferObject(t, fixture.result)
				if fixture.op == "list" {
					result["entries"] = transferJSON(t, []any{map[string]any{"id": globalTestOperation, "entry": entry}})
				} else {
					result["entry"] = entry
				}
				response := screenshotEnvelope(id, "ok", transferJSON(t, result))
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
				if string(s.devices[0].results[id].data) != response {
					t.Fatal("public Edem metadata was rewritten")
				}
			}
		})
	}
}

func TestFavoritesGlobalEdemPortableIdentityPreservesSubprofile(t *testing.T) {
	entry := transferObject(t, edemFavoritesFixture(t)["portal_entry"])
	origin := transferObject(t, entry["origin"])
	original, _ := favoritesGlobalPortableIdentity(transferJSON(t, entry))
	for _, subprofile := range []any{"Movies/Comedy", "movies / comedy", "Playlist 3", nil} {
		origin["subprofile"] = transferJSON(t, subprofile)
		entry["origin"] = transferJSON(t, origin)
		identity, valid := favoritesGlobalPortableIdentity(transferJSON(t, entry))
		if !valid || identity == original {
			t.Fatal("portable subprofile normalized or lost")
		}
	}
}

func TestFavoritesGlobalEdemPortalAdmissionDoesNotBroadenAliasesOrLocators(t *testing.T) {
	fixture := edemFavoritesFixture(t)["portal_entry"]
	for _, provider := range []string{"ilook", "ilooktv", "Edem", "edem/portal", "operator", "stalker", "bestlist/stalker", "plex", "local"} {
		entry := transferObject(t, fixture)
		origin := transferObject(t, entry["origin"])
		origin["provider"] = transferJSON(t, provider)
		entry["origin"] = transferJSON(t, origin)
		if _, valid := favoritesGlobalPortableIdentity(transferJSON(t, entry)); valid {
			t.Fatal("unrelated provider gained embedded portal authority", provider)
		}
	}
	for _, locator := range []string{
		`{"kind":"vportal-media","idField":"id","id":"47","path":"Movies/Comedy"}`,
		`{"kind":"vportal-media","idField":"url","id":"47"}`,
		`{"kind":"vportal-media","idField":"id","id":"https://private.invalid"}`,
		`{"kind":"vportal-media","idField":"id","id":"47?token=private"}`,
		`{"kind":"vportal-media","idField":"id","id":true}`,
		`{"kind":"vportal-media","idField":"id","id":"47","id":"47"}`,
		`{"kind":"vportal-media","idField":"id","id":"` + strings.Repeat("x", 257) + `"}`,
		`{"kind":"plex-media","serverId":"PMS-1","ratingKey":"47"}`,
		`{"kind":"local-media","rootAlias":"Movies","relativePath":"film.mp4"}`,
	} {
		entry := transferObject(t, fixture)
		entry["locator"] = json.RawMessage(locator)
		if _, valid := favoritesGlobalPortableIdentity(transferJSON(t, entry)); valid {
			t.Fatal("unsafe or unrelated locator accepted")
		}
	}
	for _, field := range []string{"id", "fid", "stream_id", "itemId", "media_id", "episode_id"} {
		entry := transferObject(t, fixture)
		entry["locator"] = transferJSON(t, map[string]string{"kind": "vportal-media", "idField": field, "id": "00047"})
		if _, valid := favoritesGlobalPortableIdentity(transferJSON(t, entry)); !valid {
			t.Fatal("existing attested portal ID field rejected")
		}
	}
}
