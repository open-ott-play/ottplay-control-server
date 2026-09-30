package control

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type blockedResultWriter struct {
	*httptest.ResponseRecorder
	entered chan struct{}
	release chan struct{}
}

func (w *blockedResultWriter) Write(b []byte) (int, error) {
	close(w.entered)
	<-w.release
	return w.ResponseRecorder.Write(b)
}

func TestResultWriteDoesNotBlockOtherDevices(t *testing.T) {
	for _, state := range []string{"new", "duplicate", "missing", "full"} {
		t.Run(state, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"status","params":{}}`)
			body := `{"id":"` + id + `","status":"ok","data":{}}`
			if state == "duplicate" {
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			} else if state == "missing" {
				body = `{"id":"` + strings.Repeat("0", 32) + `","status":"ok","data":{}}`
			} else if state == "full" {
				s.resultBytes = maxResultBytes
			}
			r := httptest.NewRequest("POST", "/api/responses", strings.NewReader(body))
			r.Header.Set("Authorization", "Bearer "+firstToken)
			r.Header.Set("Content-Type", "application/json")
			w := &blockedResultWriter{httptest.NewRecorder(), make(chan struct{}), make(chan struct{})}
			done := make(chan struct{})
			go func() {
				s.ServeHTTP(w, r)
				close(done)
			}()
			defer func() { close(w.release); <-done }()
			select {
			case <-w.entered:
			case <-time.After(time.Second):
				t.Fatal("result handler did not begin writing")
			}
			poll := make(chan int, 1)
			go func() {
				poll <- request(s, "GET", "/api/webhook/commands?delivery=ack", secondToken, "", nil).Code
			}()
			select {
			case status := <-poll:
				if status != 200 {
					t.Fatalf("other device poll returned %d", status)
				}
			case <-time.After(time.Second):
				t.Fatal("slow result consumer blocked the other device")
			}
		})
	}
}

func TestResultRemovalReleasesQueuePayload(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"provider_settings","params":{"provider":"xtream","settings":{"password":"test-only-secret"}}}`)
	d := s.devices[0]
	backing := d.queue[:cap(d.queue)]
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 200)
	if len(d.queue) != 0 || s.bytes != 0 || backing[0].data != nil || backing[0].id != "" {
		t.Fatal("completed request retained its queue payload")
	}
}

func TestResultReadPreservesBoundedJSON(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"channels","params":{}}`)
	prefix := `{"id":"` + id + `","status":"ok","data":{"text":"`
	suffix := `"}}`
	body := prefix + strings.Repeat("<", maxResponseBytes-len(prefix)-len(suffix)) + suffix
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, result, 200)
	if result.Body.String() != body || result.Header().Get("Content-Type") != "application/json" {
		t.Fatal("reading an accepted result changed its JSON or exceeded the wire limit")
	}
}

func rpcID(t *testing.T, s *Server, payload string) string {
	t.Helper()
	r := request(s, "POST", "/api/requests?device_id=first", adminToken, payload, nil)
	expect(t, r, 202)
	var result map[string]string
	if err := json.Unmarshal(r.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	return result["id"]
}

func TestRequestRoundTripAndIsolation(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"status","params":{}}`)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, firstToken, "", nil), 403)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil), 202)
	expect(t, request(s, "GET", "/api/requests?device_id=second&id="+id, adminToken, "", nil), 404)
	// Legacy polls and command acknowledgements cannot consume RPCs.
	commands(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), false)
	expect(t, request(s, "POST", "/api/webhook/commands/ack", firstToken, `{"ids":["`+id+`"]}`, nil), 200)
	poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
	if !strings.Contains(poll.Body.String(), id) || !strings.Contains(poll.Body.String(), `"requests"`) {
		t.Fatal(poll.Body.String())
	}
	body := `{"id":"` + id + `","status":"ok","data":{"volume":35}}`
	expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
	expect(t, request(s, "POST", "/api/responses", adminToken, body, nil), 403)
	expect(t, request(s, "POST", "/api/responses?device_id=second", firstToken, body, nil), 403)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, result, 200)
	if !strings.Contains(result.Body.String(), `"volume":35`) {
		t.Fatal(result.Body.String())
	}
	if s.bytes != 0 || s.resultBytes != len(body) {
		t.Fatal("incorrect byte accounting")
	}
	s.now = func() time.Time { return time.Now().Add(2 * time.Minute) }
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil), 404)
	if s.resultBytes != 0 {
		t.Fatal("results did not expire")
	}
}

func TestRequestValidationAndOrigins(t *testing.T) {
	s := newTestServer(t)
	for _, body := range []string{`{}`, `{"action":"arbitrary","params":{}}`, `{"action":"status","params":{"token":"secret"}}`, `{"action":"channels","params":{"wrong":"a"}}`, `{"action":"command","params":{"command":"set_volume","volume":101}}`, `{"action":"status","action":"programs","params":{}}`, `{"action":"provider_settings","params":{"provider":"unknown","settings":{}}}`} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, `{"action":"status","params":{}}`, nil), 403)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{}`, map[string]string{"Origin": "https://evil.example"}), 403)
	c := testConfig()
	c.AllowNullOrigin = true
	s, _ = New(c)
	expect(t, request(s, "OPTIONS", "/api/responses", "", "", map[string]string{"Origin": "null", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}), 204)
	expect(t, request(s, "OPTIONS", "/api/requests", "", "", map[string]string{"Origin": "null", "Access-Control-Request-Method": "GET"}), 403)
	id := rpcID(t, s, `{"action":"play","params":{"query":"НОВОСТИ"}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":"`+strings.Repeat("x", maxResponseBytes)+`"}`, nil), 413)
	s.now = func() time.Time { return time.Now().Add(2 * time.Minute) }
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 404)
	if s.bytes != 0 {
		t.Fatal("request did not expire")
	}
}

func TestVPortalRequestRoundTrip(t *testing.T) {
	for _, action := range []string{"vportal", "vportal_search"} {
		for _, value := range []string{"СВАДЬБА Straße Σς İ 😀", strings.Repeat("я", 512)} {
			t.Run(action+"/"+value[:4], func(t *testing.T) {
				s := newTestServer(t)
				body, _ := json.Marshal(map[string]any{"action": action, "params": map[string]string{"query": value}})
				id := rpcID(t, s, string(body))
				poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
				expect(t, poll, 200)
				var response struct {
					Requests []struct {
						ID     string            `json:"id"`
						Action string            `json:"action"`
						Params map[string]string `json:"params"`
					} `json:"requests"`
				}
				if err := json.Unmarshal(poll.Body.Bytes(), &response); err != nil {
					t.Fatal(err)
				}
				if len(response.Requests) != 1 || response.Requests[0].ID != id || response.Requests[0].Action != action || response.Requests[0].Params["query"] != value || len(response.Requests[0].Params) != 1 {
					t.Fatalf("VPortal request changed in transit: %s", poll.Body.String())
				}
				data := `{"items":[{"number":1,"title":"Свадьба"}],"total":1}`
				if action == "vportal" {
					data = `{"items":[{"number":1,"title":"Свадьба"}],"total":1,"loop":true,"dispatched":true}`
				}
				resultBody := `{"id":"` + id + `","status":"ok","data":` + data + `}`
				expect(t, request(s, "POST", "/api/responses", firstToken, resultBody, nil), 200)
				result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
				expect(t, result, 200)
				if result.Body.String() != resultBody || len(s.devices[0].queue) != 0 {
					t.Fatal("VPortal result changed or left the request queued")
				}
			})
		}
	}
}

func TestVPortalRequestRejectsMalformedParams(t *testing.T) {
	for _, action := range []string{"vportal", "vportal_search"} {
		for _, params := range []string{`null`, `[]`, `{}`, `{"query":null}`, `{"query":1}`, `{"query":true}`, `{"query":[]}`, `{"query":{}}`,
			`{"query":""}`, `{"query":" \t\u2003 "}`, `{"search":"video"}`, `{"query":"video","loop":true}`,
			`{"query":"video","token":"secret"}`, `{"query":"first","query":"second"}`,
			`{"query":"` + strings.Repeat("x", 1025) + `"}`, `{"query":"` + strings.Repeat("я", 513) + `"}`} {
			t.Run(action+"/"+params[:min(len(params), 30)], func(t *testing.T) {
				s := newTestServer(t)
				body := `{"action":"` + action + `","params":` + params + `}`
				expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
				if len(s.devices[0].queue) != 0 || s.bytes != 0 {
					t.Fatal("invalid VPortal request entered the queue")
				}
			})
		}
	}
}

func TestVPortalWireActionNamesAreExact(t *testing.T) {
	for _, action := range []string{"vp", "VP", "VPortal", "VPORTAL", "vportal-search", "vportal_search_extra"} {
		t.Run(action, func(t *testing.T) {
			s := newTestServer(t)
			body := `{"action":"` + action + `","params":{"query":"video"}}`
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
			if len(s.devices[0].queue) != 0 {
				t.Fatal("an unsupported VPortal action entered the queue")
			}
		})
	}
}

func TestEpgCatalogAndGuardedPlaybackRequests(t *testing.T) {
	for _, body := range []string{
		`{"action":"epg_catalog","params":{}}`,
		`{"action":"play_catalog","params":{"catalog":"snapshot-1","id":"канал-7"}}`,
		`{"action":"play_catalog","params":{"catalog":"` + strings.Repeat("x", 128) + `","id":"` + strings.Repeat("я", 512) + `"}}`,
		`{"action":"play_catalog","params":{"catalog":"c","id":"` + strings.Repeat("界", 512) + `"}}`,
		`{"action":"play_catalog","params":{"catalog":"c","id":"` + strings.Repeat("x", 2048) + `"}}`,
	} {
		s := newTestServer(t)
		id := rpcID(t, s, body)
		poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
		expect(t, poll, 200)
		var response struct {
			Requests []map[string]json.RawMessage `json:"requests"`
		}
		if err := json.Unmarshal(poll.Body.Bytes(), &response); err != nil || len(response.Requests) != 1 {
			t.Fatalf("invalid request delivery: %s", poll.Body.String())
		}
		var expected map[string]json.RawMessage
		if err := json.Unmarshal([]byte(body), &expected); err != nil {
			t.Fatal(err)
		}
		for _, field := range []string{"action", "params"} {
			if string(response.Requests[0][field]) != string(expected[field]) {
				t.Fatalf("%s changed during delivery", field)
			}
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"rejected","data":{"error":"Catalogue changed"}}`, nil), 200)
		reply := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		expect(t, reply, 200)
		if !strings.Contains(reply.Body.String(), "Catalogue changed") || len(s.devices[0].queue) != 0 {
			t.Fatal("catalogue rejection did not complete the request")
		}
	}
}

func TestEpgCatalogRequestsRejectExtraAndInvalidFields(t *testing.T) {
	for _, body := range []string{
		`{"action":"epg_catalog","params":{"search":"cats"}}`,
		`{"action":"epg_catalog","params":null}`,
		`{"action":"EPG_CATALOG","params":{}}`,
		`{"action":"play_catalog","params":{}}`,
		`{"action":"play_catalog","params":{"catalog":"c"}}`,
		`{"action":"play_catalog","params":{"id":"1"}}`,
		`{"action":"play_catalog","params":{"catalog":"","id":"1"}}`,
		`{"action":"play_catalog","params":{"catalog":" \t","id":"1"}}`,
		`{"action":"play_catalog","params":{"catalog":"c","id":" \u2003"}}`,
		`{"action":"play_catalog","params":{"catalog":false,"id":"1"}}`,
		`{"action":"play_catalog","params":{"catalog":"c","id":7}}`,
		`{"action":"play_catalog","params":{"catalog":"c","id":"1","query":"2"}}`,
		`{"action":"play_catalog","params":{"catalog":"c","id":"1","id":"2"}}`,
		`{"action":"play_catalog","params":{"catalog":"` + strings.Repeat("x", 129) + `","id":"1"}}`,
		`{"action":"play_catalog","params":{"catalog":"c","id":"` + strings.Repeat("я", 1025) + `"}}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("invalid catalogue request entered the queue")
		}
	}
}
