package control

import "testing"

func TestCapacitorAppUpdate(t *testing.T) {
	for _, body := range []string{
		`{"action":"app_update","params":{"operation":"status"}}`,
		`{"action":"app_update","params":{"operation":"prepare","url":"https://example.org/app.apk","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`,
		`{"action":"app_update","params":{"operation":"install","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`,
	} {
		s := newTestServer(t)
		id := rpcID(t, s, body)
		expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
		expect(t, request(s, "POST", "/api/responses", secondToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 404)
		expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 200)
	}
	for _, body := range []string{
		`{"action":"app_update","params":{"operation":"status","url":"https://example.org"}}`,
		`{"action":"app_update","params":{"operation":"install","sha256":"bad"}}`,
		`{"action":"app_update","params":{"operation":"prepare","url":"http://example.org/a","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`,
		`{"action":"app_update","params":{"operation":"prepare","url":"https://user:pass@example.org/a","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`,
		`{"action":"app_update","params":{"operation":"prepare","url":"https://example.org/a#fragment","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`,
		`{"action":"app_update","params":{"operation":"shell","command":"pm install"}}`,
	} {
		expect(t, request(newTestServer(t), "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
	}
}
