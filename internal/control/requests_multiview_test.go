package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type multiviewContract struct {
	Operations []string `json:"operations"`
	Requests   map[string]struct {
		Action string          `json:"action"`
		Params json.RawMessage `json:"params"`
	} `json:"requests"`
	Results      map[string]json.RawMessage `json:"results"`
	Rejections   map[string]json.RawMessage `json:"rejections"`
	Capability   map[string]json.RawMessage `json:"capability"`
	ZeroCapacity map[string]json.RawMessage `json:"zero_capacity_capability"`
	EmptyStatus  json.RawMessage            `json:"empty_status"`
	PausedStatus json.RawMessage            `json:"paused_status"`
	PublicText   struct {
		Accepted []string `json:"accepted_queries"`
		Rejected []string `json:"rejected"`
	} `json:"public_text_cases"`
	NativeDeliveryRejections []struct {
		Name    string `json:"name"`
		Request struct {
			Action string          `json:"action"`
			Params json.RawMessage `json:"params"`
		} `json:"request"`
		Response struct {
			Status string          `json:"status"`
			Data   json.RawMessage `json:"data"`
		} `json:"response"`
	} `json:"native_delivery_rejections"`
}

func multiviewFixture(t testing.TB) multiviewContract {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/multiview-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var f multiviewContract
	if err = json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	if len(f.Requests) != 14 || len(f.Results) != 14 || len(f.Rejections) != 14 || !reflect.DeepEqual(f.Operations, multiviewOperations) {
		t.Fatal("incomplete multiview contract")
	}
	return f
}

func TestMultiviewSharedContractAndBoundHTTPReceipts(t *testing.T) {
	f := multiviewFixture(t)
	for name, input := range f.Requests {
		for _, status := range []string{"ok", "rejected", "unsupported"} {
			t.Run(name+"/"+status, func(t *testing.T) {
				data := f.Results[name]
				if status != "ok" {
					data = f.Rejections[name]
				}
				if status == "unsupported" {
					m := transferObject(t, data)
					m["reason"] = json.RawMessage(`"unsupported"`)
					data = transferJSON(t, m)
				}
				q := parseMultiviewRequest(input.Params)
				if q == nil || !validMultiviewResult(data, status, q) {
					t.Fatal("shared contract rejected")
				}
				s := newTestServer(t)
				body := globalRPC(t, "multiview", input.Params, "")
				expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
				id := rpcID(t, s, body)
				bound := s.devices[0].queue[0]
				if !reflect.DeepEqual(bound.multiview, q) || bound.permission != "multiview."+q.Op {
					t.Fatal("queue lost request binding")
				}
				response := transferResponse(t, id, status, data)
				expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
				expect(t, request(s, "POST", "/api/responses", adminToken, response, nil), 403)
				bad := transferObject(t, data)
				bad["binding"] = json.RawMessage(`{"scene":"private-wrong"}`)
				wrong := transferResponse(t, id, status, transferJSON(t, bad))
				expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
				if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
					t.Fatal("invalid result consumed request")
				}
				for range 2 {
					expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
				}
				expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
				got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
				expect(t, got, 200)
				if got.Body.String() != response || !reflect.DeepEqual(s.devices[0].results[id].multiview, q) || s.bytes != 0 || s.resultBytes != len(response) {
					t.Fatal("receipt changed or lost binding")
				}
			})
		}
	}
	if !validMultiviewResult(f.EmptyStatus, "ok", parseMultiviewRequest(f.Requests["status"].Params)) {
		t.Fatal("empty status rejected")
	}
	if !validMultiviewResult(f.PausedStatus, "ok", parseMultiviewRequest(f.Requests["status"].Params)) {
		t.Fatal("paused status rejected")
	}
}

func TestMultiviewMalformedRequests(t *testing.T) {
	f := multiviewFixture(t)
	cases := []struct{ name, base, field, value string }{
		{"version-bool", "focus", "version", "true"}, {"version-float", "focus", "version", "1.0"}, {"version-exponent", "focus", "version", "1e0"}, {"version-null", "focus", "version", "null"}, {"version-future", "focus", "version", "2"},
		{"slot-minus", "focus", "slot_id", "-1"}, {"slot-minus-zero", "focus", "slot_id", "-0"}, {"slot-nine", "focus", "slot_id", "9"}, {"slot-bool", "focus", "slot_id", "false"}, {"slot-float", "focus", "slot_id", "0.0"}, {"slot-exponent", "focus", "slot_id", "0e0"}, {"slot-null", "focus", "slot_id", "null"}, {"slot-string", "focus", "slot_id", `"0"`},
		{"unknown-op", "focus", "op", `"start"`}, {"missing-runtime", "focus", "runtime", ""}, {"null-runtime", "focus", "runtime", "null"}, {"private-runtime", "focus", "runtime", `"https://private.invalid/token"`}, {"runtime-length", "focus", "runtime", `"` + strings.Repeat("a", 65) + `"`},
		{"missing-expected", "focus", "expected", ""}, {"bad-expected", "focus", "expected", `{"scene":"scene-1"}`}, {"private-extra", "focus", "url", `"https://private.invalid/token"`},
		{"layout-extra-satellites", "layout", "satellites", "0"}, {"perimeter-missing", "layout_perimeter", "satellites", ""}, {"perimeter-nine", "layout_perimeter", "satellites", "9"}, {"perimeter-float", "layout_perimeter", "satellites", "8.0"},
		{"audio-missing-slot", "audio", "slot_id", ""}, {"audio-follow-slot", "audio_follow", "slot_id", "0"}, {"audio-muted-slot", "audio_muted", "slot_id", "null"},
		{"numeric-profile", "sources", "profile", "1"}, {"null-profile", "sources", "profile", "null"}, {"unsafe-provider", "sources", "provider", `"../m3u"`}, {"empty-cursor", "sources", "cursor", `""`}, {"empty-ticket", "play", "source_ticket", `""`},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			m := transferObject(t, f.Requests[c.base].Params)
			if c.value == "" {
				delete(m, c.field)
			} else {
				m[c.field] = json.RawMessage(c.value)
			}
			raw := transferJSON(t, m)
			if parseMultiviewRequest(raw) != nil {
				t.Fatal("malformed request parsed")
			}
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, "multiview", raw, ""), nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("malformed request queued")
			}
		})
	}
	for _, id := range []int{0, 8} {
		m := transferObject(t, f.Requests["focus"].Params)
		m["slot_id"] = transferJSON(t, id)
		if parseMultiviewRequest(transferJSON(t, m)) == nil {
			t.Fatal("slot boundary rejected")
		}
	}
	for _, query := range []string{"", strings.Repeat("я", 256)} {
		m := transferObject(t, f.Requests["sources"].Params)
		m["query"] = transferJSON(t, query)
		if parseMultiviewRequest(transferJSON(t, m)) == nil {
			t.Fatal("query boundary rejected")
		}
	}
}

func TestMultiviewStrictJSONAndPrivateText(t *testing.T) {
	f := multiviewFixture(t)
	for _, name := range []string{"focus", "sources_filtered", "play", "layout_perimeter"} {
		for _, key := range []string{"version", "runtime", "op", "expected", "scene", "focus_revision", "slot_id", "source_ticket", "satellites"} {
			needle := []byte(`"` + key + `":`)
			if !bytes.Contains(f.Requests[name].Params, needle) {
				continue
			}
			bad := bytes.Replace(f.Requests[name].Params, needle, append(append([]byte{}, needle...), append([]byte(`null,`), needle...)...), 1)
			if parseMultiviewRequest(bad) != nil {
				t.Fatalf("duplicate %s accepted", key)
			}
		}
	}
	for _, raw := range []string{`{"version":1,"op":"status","runtime":"\ud800"}`, `{"version":1,"op":"status","runtime":"\udc00"}`, `{"version":1e999,"op":"status"}`} {
		if parseMultiviewRequest(json.RawMessage(raw)) != nil {
			t.Fatal("unsafe JSON accepted")
		}
	}
	for _, text := range []string{"https://private.invalid/token", "file:/private", "/Volumes/private", "~/private", `C:\private`, "password=secret", "token:secret", "label\nsecret", "label\u200bsecret", strings.Repeat("я", 257)} {
		// Escape literals deliberately for the control and format-character cases.
		text = strings.ReplaceAll(strings.ReplaceAll(text, `\n`, "\n"), `\u200b`, "\u200b")
		m := transferObject(t, f.Requests["sources"].Params)
		m["query"] = transferJSON(t, text)
		if parseMultiviewRequest(transferJSON(t, m)) != nil {
			t.Fatalf("private/invalid query accepted: %q", text)
		}
		data := transferObject(t, f.Results["sources"])
		var items []map[string]json.RawMessage
		_ = json.Unmarshal(data["items"], &items)
		items[0]["label"] = transferJSON(t, text)
		data["items"] = transferJSON(t, items)
		if validMultiviewResult(transferJSON(t, data), "ok", parseMultiviewRequest(f.Requests["sources"].Params)) {
			t.Fatal("private/invalid label accepted")
		}
	}
}

func TestMultiviewNativePublicTextCorpus(t *testing.T) {
	f := multiviewFixture(t)
	if len(f.PublicText.Accepted) == 0 || len(f.PublicText.Rejected) != 18 {
		t.Fatal("missing shared native public-text corpus")
	}
	for _, accepted := range []bool{true, false} {
		values := append(append([]string{}, f.PublicText.Accepted...), "Bearer ", "Authorization documentary", "apiKey=fixture")
		if !accepted {
			values = append(append([]string{}, f.PublicText.Rejected...), "Authorization\u202f=fixture")
		}
		for _, value := range values {
			t.Run(strconv.FormatBool(accepted)+"/"+value, func(t *testing.T) {
				m := transferObject(t, f.Requests["sources"].Params)
				m["query"] = transferJSON(t, value)
				if (parseMultiviewRequest(transferJSON(t, m)) != nil) != accepted {
					t.Fatal("query classification differs from native")
				}
				data := transferObject(t, f.Results["sources"])
				var items []map[string]json.RawMessage
				if err := json.Unmarshal(data["items"], &items); err != nil {
					t.Fatal(err)
				}
				items[0]["label"] = transferJSON(t, value)
				data["items"] = transferJSON(t, items)
				wantLabel := accepted && strings.TrimSpace(value) != ""
				if validMultiviewResult(transferJSON(t, data), "ok", parseMultiviewRequest(f.Requests["sources"].Params)) != wantLabel {
					t.Fatal("source label classification differs from native")
				}
			})
		}
	}
}

func TestMultiviewNativeDeliveryFallbackHTTPReceipts(t *testing.T) {
	f := multiviewFixture(t)
	if len(f.NativeDeliveryRejections) != 4 {
		t.Fatal("missing native fallback corpus")
	}
	for _, row := range f.NativeDeliveryRejections {
		t.Run(row.Name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, row.Request.Action, row.Request.Params, ""))
			q := parseMultiviewRequest(row.Request.Params)
			response := transferResponse(t, id, row.Response.Status, row.Response.Data)
			generic := []string{
				transferResponse(t, id, "rejected", json.RawMessage(`{"reason":"unavailable"}`)),
				transferResponse(t, id, "rejected", json.RawMessage(`{"error":"limit_exceeded"}`)),
			}
			for _, wrong := range generic {
				expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			}
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("generic fallback consumed pending typed request")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			for _, wrong := range generic {
				expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response || !reflect.DeepEqual(s.devices[0].results[id].multiview, q) ||
				len(s.devices[0].queue) != 0 || s.bytes != 0 || s.resultBytes != len(response) {
				t.Fatal("typed fallback changed after duplicate delivery or lost original admission binding")
			}
		})
	}
}

func TestMultiviewClosedBoundResults(t *testing.T) {
	f := multiviewFixture(t)
	for name, input := range f.Requests {
		q := parseMultiviewRequest(input.Params)
		for _, status := range []string{"ok", "rejected", "unsupported"} {
			base := f.Results[name]
			if status != "ok" {
				base = f.Rejections[name]
			}
			for _, field := range []string{"version", "runtime", "op", "binding"} {
				data := transferObject(t, base)
				data[field] = json.RawMessage(`null`)
				if validMultiviewResult(transferJSON(t, data), status, q) {
					t.Fatalf("unbound %s/%s/%s accepted", name, status, field)
				}
			}
		}
	}
	q := parseMultiviewRequest(f.Requests["focus"].Params)
	for _, field := range []string{"ticket", "context", "snapshot", "current", "url", "focus_revision"} {
		data := transferObject(t, f.Rejections["focus"])
		data[field] = json.RawMessage(`"private"`)
		if validMultiviewResult(transferJSON(t, data), "rejected", q) {
			t.Fatal("rejection minted context or private data")
		}
	}
	for _, reason := range multiviewReasons {
		data := transferObject(t, f.Rejections["focus"])
		data["reason"] = transferJSON(t, reason)
		if validMultiviewResult(transferJSON(t, data), "rejected", q) != (reason != "unsupported") || validMultiviewResult(transferJSON(t, data), "unsupported", q) != (reason == "unsupported") {
			t.Fatal("negative status/reason mismatch")
		}
	}
	data := transferObject(t, f.Results["focus"])
	binding := transferObject(t, data["binding"])
	binding["slot_id"] = json.RawMessage(`8`)
	data["binding"] = transferJSON(t, binding)
	if validMultiviewResult(transferJSON(t, data), "ok", q) {
		t.Fatal("wrong slot accepted")
	}
	data = transferObject(t, f.Results["focus"])
	data["binding"] = json.RawMessage(`{"source_revision":"slot-0-source-1","slot_generation":"slot-0-generation-1","slot_id":0,"expected":{"audio_revision":"audio-1","focus_revision":"focus-1","layout_revision":"layout-1","scene":"scene-1"}}`)
	if !validMultiviewResult(transferJSON(t, data), "ok", q) {
		t.Fatal("semantic binding reordered rejected")
	}
	for _, key := range []string{"runtime", "op", "binding", "scene", "slot_id", "accepted"} {
		needle := []byte(`"` + key + `":`)
		bad := bytes.Replace(f.Results["focus"], needle, append(append([]byte{}, needle...), append([]byte(`null,`), needle...)...), 1)
		if validMultiviewResult(bad, "ok", q) {
			t.Fatalf("duplicate result %s accepted", key)
		}
	}
}

func TestMultiviewSourcePages(t *testing.T) {
	f := multiviewFixture(t)
	q := parseMultiviewRequest(f.Requests["sources"].Params)
	for _, mutate := range []func(map[string]json.RawMessage){
		func(m map[string]json.RawMessage) { m["cursor"] = json.RawMessage(`"next"`) },
		func(m map[string]json.RawMessage) { m["complete"] = json.RawMessage(`false`) },
		func(m map[string]json.RawMessage) { m["complete"] = json.RawMessage(`null`) },
		func(m map[string]json.RawMessage) {
			var rows []json.RawMessage
			_ = json.Unmarshal(m["items"], &rows)
			m["items"] = transferJSON(t, append(rows, rows[0]))
		},
		func(m map[string]json.RawMessage) {
			var rows []map[string]json.RawMessage
			_ = json.Unmarshal(m["items"], &rows)
			rows[0]["url"] = json.RawMessage(`"https://private.invalid"`)
			m["items"] = transferJSON(t, rows)
		},
	} {
		m := transferObject(t, f.Results["sources"])
		mutate(m)
		if validMultiviewResult(transferJSON(t, m), "ok", q) {
			t.Fatal("invalid source page accepted")
		}
	}
	m := transferObject(t, f.Results["sources"])
	m["complete"] = json.RawMessage(`false`)
	m["cursor"] = json.RawMessage(`"next"`)
	if !validMultiviewResult(transferJSON(t, m), "ok", q) {
		t.Fatal("incomplete page rejected")
	}
	for _, count := range []int{128, 129} {
		rows := make([]map[string]any, 0, count)
		for i := 0; i < count; i++ {
			rows = append(rows, map[string]any{"source_id": "source_" + strconv.Itoa(i), "ticket": "ticket_" + strconv.Itoa(i), "label": "Fixture", "provider": "bestlist/stalker", "profile": "profile-home", "kind": "live"})
		}
		data := transferObject(t, f.Results["sources"])
		data["items"] = transferJSON(t, rows)
		if validMultiviewResult(transferJSON(t, data), "ok", q) != (count == 128) {
			t.Fatal("source page bound mismatch")
		}
	}
}

func TestMultiviewSnapshotShapeAndOwnership(t *testing.T) {
	f := multiviewFixture(t)
	base := transferObject(t, f.Results["status"])["snapshot"]
	for _, state := range []string{"starting", "playing", "paused", "stalled"} {
		m := transferObject(t, base)
		var rows []map[string]json.RawMessage
		_ = json.Unmarshal(m["slots"], &rows)
		rows[0]["state"] = transferJSON(t, state)
		m["slots"] = transferJSON(t, rows)
		if !validMultiviewSnapshot(transferJSON(t, m)) {
			t.Fatalf("%s with source rejected", state)
		}
		rows[0]["source"] = json.RawMessage(`null`)
		m["slots"] = transferJSON(t, rows)
		if validMultiviewSnapshot(transferJSON(t, m)) {
			t.Fatalf("%s without source accepted", state)
		}
	}
	for _, mutate := range []func(map[string]json.RawMessage){
		func(m map[string]json.RawMessage) { m["focus_slot"] = json.RawMessage(`4`) },
		func(m map[string]json.RawMessage) { m["audio_slot"] = json.RawMessage(`1`) },
		func(m map[string]json.RawMessage) {
			m["audio_policy"] = json.RawMessage(`"slot"`)
			m["audio_slot"] = json.RawMessage(`null`)
		},
		func(m map[string]json.RawMessage) { m["audio_policy"] = json.RawMessage(`"muted"`) },
		func(m map[string]json.RawMessage) { m["layout"] = json.RawMessage(`"grid9"`) },
		func(m map[string]json.RawMessage) { m["satellites"] = json.RawMessage(`1`) },
		func(m map[string]json.RawMessage) {
			var rows []json.RawMessage
			_ = json.Unmarshal(m["slots"], &rows)
			rows[1] = rows[0]
			m["slots"] = transferJSON(t, rows)
		},
		func(m map[string]json.RawMessage) {
			var rows []map[string]json.RawMessage
			_ = json.Unmarshal(m["slots"], &rows)
			rows[0]["state"] = json.RawMessage(`"failed"`)
			m["slots"] = transferJSON(t, rows)
		},
		func(m map[string]json.RawMessage) {
			var rows []map[string]json.RawMessage
			_ = json.Unmarshal(m["slots"], &rows)
			rows[0]["error"] = json.RawMessage(`"https://private.invalid"`)
			m["slots"] = transferJSON(t, rows)
		},
	} {
		m := transferObject(t, base)
		mutate(m)
		if validMultiviewSnapshot(transferJSON(t, m)) {
			t.Fatal("invalid snapshot accepted")
		}
	}
	m := transferObject(t, base)
	var rows []json.RawMessage
	_ = json.Unmarshal(m["slots"], &rows)
	rows[0], rows[3] = rows[3], rows[0]
	m["slots"] = transferJSON(t, rows)
	if !validMultiviewSnapshot(transferJSON(t, m)) {
		t.Fatal("out-of-order contiguous slots rejected")
	}
	m["audio_slot"] = json.RawMessage(`null`)
	if !validMultiviewSnapshot(transferJSON(t, m)) {
		t.Fatal("unobserved follows-focus audio rejected")
	}
}

func TestMultiviewCapabilityStrictSubsetsAndZero(t *testing.T) {
	f := multiviewFixture(t)
	for _, raw := range []json.RawMessage{f.Capability["multiview"], f.ZeroCapacity["multiview"]} {
		if !validMultiviewCapability(raw) {
			t.Fatal("shared capability rejected")
		}
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{"multiview":true}}`)
		body := transferResponse(t, id, "ok", transferJSON(t, map[string]json.RawMessage{"multiview": raw, "player": json.RawMessage(`{"runtime":"native-runtime-1"}`)}))
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	}
	for _, mutate := range []func(map[string]json.RawMessage){
		func(m map[string]json.RawMessage) { m["operations"] = json.RawMessage(`["focus"]`) },
		func(m map[string]json.RawMessage) { m["operations"] = json.RawMessage(`["status","status"]`) },
		func(m map[string]json.RawMessage) { m["max_slots"] = json.RawMessage(`0`) },
		func(m map[string]json.RawMessage) { m["max_slots"] = json.RawMessage(`8`) },
		func(m map[string]json.RawMessage) { m["max_slots"] = json.RawMessage(`9.0`) },
		func(m map[string]json.RawMessage) { m["max_slots"] = json.RawMessage(`true`) },
		func(m map[string]json.RawMessage) { m["layouts"] = json.RawMessage(`[]`) },
		func(m map[string]json.RawMessage) { m["source_kinds"] = json.RawMessage(`[]`) },
		func(m map[string]json.RawMessage) { m["audio_policies"] = json.RawMessage(`[]`) },
		func(m map[string]json.RawMessage) { m["off_modes"] = json.RawMessage(`[]`) },
		func(m map[string]json.RawMessage) { m["url"] = json.RawMessage(`"private"`) },
	} {
		m := transferObject(t, f.Capability["multiview"])
		mutate(m)
		raw := transferJSON(t, m)
		if validMultiviewCapability(raw) {
			t.Fatal("invalid capability accepted")
		}
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, map[string]json.RawMessage{"multiview": raw, "player": json.RawMessage(`{"runtime":"native-runtime-1"}`)})), nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("bad capability consumed request")
		}
	}
}

func TestMultiviewCapabilityRequiresValidRuntime(t *testing.T) {
	f := multiviewFixture(t)
	for _, player := range []json.RawMessage{nil, json.RawMessage(`null`), json.RawMessage(`{}`), json.RawMessage(`{"runtime":null}`), json.RawMessage(`{"runtime":true}`), json.RawMessage(`{"runtime":""}`), json.RawMessage(`{"runtime":"https://private.invalid"}`), json.RawMessage(`{"runtime":"` + strings.Repeat("a", 65) + `"}`), json.RawMessage(`{"runtime":"native-runtime-1","runtime":"other"}`)} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{"multiview":true}}`)
		data := map[string]json.RawMessage{"multiview": f.Capability["multiview"]}
		if player != nil {
			data["player"] = player
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, data)), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid capability runtime consumed request")
		}
	}
}

func TestMultiviewExactOperatorAndReceiptScope(t *testing.T) {
	f := multiviewFixture(t)
	for _, op := range multiviewOperations {
		c := testConfig()
		owner, other := strings.Repeat("q", 32), strings.Repeat("v", 32)
		for _, row := range []struct{ id, token string }{{"owner", owner}, {"other", other}} {
			hash := sha256.Sum256([]byte(row.token))
			c.Operators = append(c.Operators, config.CommandOperator{ID: row.id, CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"multiview." + op}})
		}
		s, err := New(c)
		if err != nil {
			t.Fatal(err)
		}
		defer s.Close()
		body := globalRPC(t, "multiview", f.Requests[op].Params, "")
		expect(t, request(s, "POST", "/api/requests?device_id=second", owner, body, nil), 403)
		denied := "status"
		if op == "status" {
			denied = "focus"
		}
		expect(t, request(s, "POST", "/api/requests?device_id=first", owner, globalRPC(t, "multiview", f.Requests[denied].Params, ""), nil), 403)
		accepted := request(s, "POST", "/api/requests?device_id=first", owner, body, nil)
		expect(t, accepted, 202)
		var receipt map[string]string
		_ = json.Unmarshal(accepted.Body.Bytes(), &receipt)
		id := receipt["id"]
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.Results[op]), nil), 200)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, owner, "", nil), 200)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
	}
}

func TestMultiviewFullEnvelopeLimitsAndTransport(t *testing.T) {
	f := multiviewFixture(t)
	s := newTestServer(t)
	id := rpcID(t, s, globalRPC(t, "multiview", f.Requests["focus"].Params, ""))
	body := transferResponse(t, id, "ok", f.Results["focus"])
	padded := strings.TrimSuffix(body, "}") + strings.Repeat(" ", maxMultiviewResultBytes-len(body)) + "}"
	expect(t, request(s, "POST", "/api/responses", firstToken, padded+" ", nil), 400)
	expect(t, transportCall(s, "POST", "/api/responses", firstToken, padded), 200)
	expect(t, request(s, "POST", "/api/responses", firstToken, padded+" ", nil), 400)
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, strings.Repeat("f", 32), "ok", f.Results["focus"]), nil), 404)
	if s.resultBytes != maxMultiviewResultBytes || len(s.devices[0].results) != 1 {
		t.Fatal("response bound changed storage")
	}
	// Private rejected details are rejected before storage, never reflected.
	s = newTestServer(t)
	id = rpcID(t, s, globalRPC(t, "multiview", f.Requests["focus"].Params, ""))
	bad := transferObject(t, f.Rejections["focus"])
	bad["url"] = json.RawMessage(`"https://private.invalid/credential"`)
	got := request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "rejected", transferJSON(t, bad)), nil)
	expect(t, got, 400)
	if strings.Contains(got.Body.String(), "private.invalid") || s.resultBytes != 0 {
		t.Fatal("private rejection retained/reflected")
	}
	s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.Results["focus"]), nil), 404)
	// Full HTTP request bytes include whitespace, even when params are small.
	input := globalRPC(t, "multiview", f.Requests["focus"].Params, "")
	input = strings.Replace(input, `"scene-1"`, `"`+strings.Repeat(`\u0061`, 64)+`"`, 1)
	if len(input) > maxMultiviewRequestBytes {
		t.Fatal("bad boundary fixture")
	}
	input += strings.Repeat(" ", maxMultiviewRequestBytes-len(input)+1)
	expect(t, request(newTestServer(t), "POST", "/api/requests?device_id=first", adminToken, input, nil), 413)
}

func TestMultiviewJournalAndDuplicateDelivery(t *testing.T) {
	f := multiviewFixture(t)
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	key := "multiview-focus-0001"
	body := globalRPC(t, "multiview", f.Requests["focus"].Params, key)
	id := rpcID(t, s, body)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	for range 2 {
		poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
		expect(t, poll, 200)
		var envelope map[string]json.RawMessage
		_ = json.Unmarshal(poll.Body.Bytes(), &envelope)
		var rows []map[string]json.RawMessage
		_ = json.Unmarshal(envelope["requests"], &rows)
		if len(rows) != 1 || settingString(rows[0]["id"]) != id {
			t.Fatal("pending duplicate changed request")
		}
	}
	changed := strings.Replace(body, `"slot_id":0`, `"slot_id":8`, 1)
	if changed == body {
		t.Fatal("missing mutation")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, changed, nil), 409)
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.Results["focus"]), nil), 200)
	if err = s.Close(); err != nil {
		t.Fatal(err)
	}
	disk, err := os.ReadFile(c.Journal.Path)
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"native-runtime-1", "slot-0-generation-1", "scene-1", "source_revision", "binding"} {
		if strings.Contains(string(disk), forbidden) {
			t.Fatal("multiview context persisted")
		}
	}
	s, err = New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 0 || len(s.devices[0].results) != 0 {
		t.Fatal("historical operation replayed after restart")
	}
	if operationValue(t, s, key)["id"] != id {
		t.Fatal("operation identity changed")
	}
}

func TestMultiviewCapabilityProbeIsRelayOnly(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"capabilities","params":{"multiview":true}}`)
	var queued struct {
		Params map[string]json.RawMessage `json:"params"`
	}
	if err := json.Unmarshal(s.devices[0].queue[0].data, &queued); err != nil || len(queued.Params) != 0 {
		t.Fatal("multiview relay probe was forwarded to player")
	}
	body := `{"id":"` + id + `","status":"ok","data":{}}`
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, got, 200)
	if got.Body.String() != body {
		t.Fatal("controller manufactured or changed player capabilities")
	}
	for _, value := range []string{"false", "null", "1", `"true"`, "{}", "[]"} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":{"multiview":`+value+`}}`, nil), 400)
	}
}

func TestMultiviewCapabilityProbeGuardsAllStatusesAndRetainedDuplicates(t *testing.T) {
	for _, status := range []string{"ok", "rejected", "unsupported"} {
		t.Run(status, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"capabilities","params":{"multiview":true}}`)
			if !s.devices[0].queue[0].multiviewProbe {
				t.Fatal("queued capability request lost multiview probe")
			}
			body := transferResponse(t, id, status, json.RawMessage(`{}`))
			padded := strings.TrimSuffix(body, "}") + strings.Repeat(" ", maxMultiviewResultBytes-len(body)) + "}"
			unsafe := []string{
				padded + " ",
				transferResponse(t, id, status, json.RawMessage(`{"extension":{"value":1,"value":2}}`)),
				transferResponse(t, id, status, json.RawMessage(`{"extension":"\ud800"}`)),
			}
			for _, invalid := range unsafe {
				expect(t, request(s, "POST", "/api/responses", firstToken, invalid, nil), 400)
				if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
					t.Fatal("invalid probe response consumed or stored pending request")
				}
			}
			expect(t, transportCall(s, "POST", "/api/responses", firstToken, padded), 200)
			if !s.devices[0].results[id].multiviewProbe || s.resultBytes != maxMultiviewResultBytes {
				t.Fatal("retained capability result lost probe binding or byte budget")
			}
			for _, invalid := range unsafe {
				expect(t, request(s, "POST", "/api/responses", firstToken, invalid, nil), 400)
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != padded || s.resultBytes != maxMultiviewResultBytes || len(s.devices[0].results) != 1 {
				t.Fatal("probe duplicate replaced original result")
			}
		})
	}
}

func TestMultiviewProbeDoesNotWidenLegacyCapabilitiesValidation(t *testing.T) {
	for _, status := range []string{"ok", "rejected", "unsupported"} {
		for _, data := range []json.RawMessage{json.RawMessage(`{}`), json.RawMessage(`{"extension":{"value":1,"value":2}}`)} {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
			body := transferResponse(t, id, status, data)
			padded := strings.TrimSuffix(body, "}") + strings.Repeat(" ", maxMultiviewResultBytes+1-len(body)) + "}"
			expect(t, request(s, "POST", "/api/responses", firstToken, padded, nil), 200)
			if s.devices[0].results[id].multiviewProbe || s.resultBytes != maxMultiviewResultBytes+1 {
				t.Fatal("unprobed legacy capability inherited multiview probe policy")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, padded, nil), 200)
		}
	}
	// The generic capability ceiling remains 2 MiB, not an unbounded response.
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
	body := transferResponse(t, id, "ok", json.RawMessage(`{}`))
	padded := strings.TrimSuffix(body, "}") + strings.Repeat(" ", maxResponseBytes-len(body)) + "}"
	expect(t, request(s, "POST", "/api/responses", firstToken, padded+" ", nil), 413)
	expect(t, request(s, "POST", "/api/responses", firstToken, padded, nil), 200)
}

func TestMultiviewCommandActionUsesExactOperation(t *testing.T) {
	for _, operation := range []string{"status", "sources", "layout", "play", "focus", "audio", "stop", "off"} {
		params := map[string]json.RawMessage{"op": json.RawMessage(`"` + operation + `"`)}
		if got := commandAction("multiview", params); got != "multiview."+operation {
			t.Fatalf("wrong multiview permission %q", got)
		}
	}
}
