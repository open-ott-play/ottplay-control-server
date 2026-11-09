package control

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestCategoriesKeepNativeOperatorProviderIdentity(t *testing.T) {
	fixture := categoryContract(t)
	for _, operation := range []string{"list", "select"} {
		for _, provider := range []string{"edem", "ottclub", "bestlist/stalker", "d/maxtv", "iptv-ott.ru", "shara.club", "legacy_ID", "family/name-2"} {
			t.Run(operation+"/"+provider, func(t *testing.T) {
				s := newTestServer(t)
				id := rpcID(t, s, string(fixture[operation+"_request"]))
				fields, _ := decodeObject(fixture[operation+"_result"])
				fields["provider"], _ = json.Marshal(provider)
				result, _ := json.Marshal(fields)
				response := screenshotEnvelope(id, "ok", json.RawMessage(result))
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
				if string(s.devices[0].results[id].data) != response {
					t.Fatal("named provider identity was rewritten")
				}
			})
		}
	}
}

func TestCategoriesRejectUnsafeProviderIdentifiers(t *testing.T) {
	fixture := categoryContract(t)
	for _, provider := range []any{nil, true, 42, "", ".", "..", "../m3u", "m3u/..", "m3u/./other", "bestlist//stalker", "bestlist/", "/bestlist", "private?token=x", "https://private.invalid", "bad\nname", strings.Repeat("a", 65)} {
		s := newTestServer(t)
		id := rpcID(t, s, string(fixture["list_request"]))
		fields, _ := decodeObject(fixture["list_result"])
		fields["provider"], _ = json.Marshal(provider)
		result, _ := json.Marshal(fields)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", json.RawMessage(result)), nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("unsafe category provider consumed request")
		}
	}
}
