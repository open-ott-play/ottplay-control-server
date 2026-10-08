package control

import "testing"

func TestMaintenanceAndVPortalQueue(t *testing.T) {
	valid := []string{
		`{"action":"maintenance","params":{"operation":"health"}}`,
		`{"action":"maintenance","params":{"operation":"logs"}}`,
		`{"action":"maintenance","params":{"operation":"recover_video"}}`,
		`{"action":"maintenance","params":{"operation":"update","manifest":"https://example.org/release.json","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`,
		`{"action":"vportal_queue","params":{"operation":"play","ids":[47677,44819,47674],"loop":true}}`,
		`{"action":"vportal_queue","params":{"operation":"restart"}}`,
	}
	for _, body := range valid {
		s := newTestServer(t)
		id := rpcID(t, s, body)
		expect(t, request(s, "POST", "/api/responses", secondToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 404)
		expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
		expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 200)
	}
	invalid := []string{
		`{"action":"maintenance","params":{"operation":"shell","command":"id"}}`,
		`{"action":"maintenance","params":{"operation":"recover_video","script":"alert(1)"}}`,
		`{"action":"maintenance","params":{"operation":"health","operation":"logs"}}`,
		`{"action":"maintenance","params":{"operation":"update","manifest":"http://example.org/a","sha256":"bad"}}`,
		`{"action":"vportal_queue","params":{"operation":"play","ids":[],"loop":true}}`,
		`{"action":"vportal_queue","params":{"operation":"play","ids":[1,1],"loop":true}}`,
		`{"action":"vportal_queue","params":{"operation":"play","ids":[1.5],"loop":true}}`,
		`{"action":"vportal_queue","params":{"operation":"play","ids":[1],"loop":null}}`,
		`{"action":"vportal_queue","params":{"operation":"restart","ids":[1]}}`,
	}
	for _, body := range invalid {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
	}
}
