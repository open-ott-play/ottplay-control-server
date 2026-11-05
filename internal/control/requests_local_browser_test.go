package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type localBrowserCase struct {
	Name    string          `json:"name"`
	Request json.RawMessage `json:"request"`
	Status  string          `json:"status"`
	Data    json.RawMessage `json:"data"`
}

type localBrowserFixture struct {
	Capabilities   map[string]json.RawMessage `json:"capabilities"`
	BrowserCases   []localBrowserCase         `json:"browser_cases"`
	HandleCases    []localBrowserCase         `json:"handle_cases"`
	ContractSHA256 string                     `json:"contract_sha256"`
}

func readLocalBrowserFixture(t *testing.T) localBrowserFixture {
	t.Helper()
	b, err := os.ReadFile("../../testdata/local_browser_v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture localBrowserFixture
	if json.Unmarshal(b, &fixture) != nil || fixture.ContractSHA256 != "4ebbb877dfe540ae471bca7df74305cb0951eb332cf4d5038ae9a623e72ee2db" || len(fixture.BrowserCases) < 12 || len(fixture.HandleCases) != 3 {
		t.Fatal("invalid shared Local browser fixture")
	}
	return fixture
}

func (f localBrowserFixture) named(t *testing.T, name string) localBrowserCase {
	t.Helper()
	for _, row := range append(append([]localBrowserCase{}, f.BrowserCases...), f.HandleCases...) {
		if row.Name == name {
			return row
		}
	}
	t.Fatal("missing fixture", name)
	return localBrowserCase{}
}

func TestLocalBrowserSharedHTTPContractAndRetainedCorrelation(t *testing.T) {
	f := readLocalBrowserFixture(t)
	for _, row := range f.BrowserCases {
		t.Run(row.Name, func(t *testing.T) {
			s := newTestServer(t)
			body := globalRPC(t, "local_browser", row.Request, "")
			expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
			id := rpcID(t, s, body)
			bound := parseLocalBrowserRequest(row.Request)
			entry := s.devices[0].queue[0]
			if bound == nil || entry.localBrowser == nil || *entry.localBrowser != *bound || entry.permission != "local_browser."+bound.Op {
				t.Fatal("browser request lost correlation or permission")
			}
			good := transferResponse(t, id, row.Status, row.Data)
			expect(t, request(s, "POST", "/api/responses", secondToken, good, nil), 404)
			bad := transferObject(t, row.Data)
			bad["runtime"] = transferJSON(t, "another-runtime")
			wrong := transferResponse(t, id, row.Status, transferJSON(t, bad))
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("bad result consumed request")
			}
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, good, nil), 200)
			}
			retained := s.devices[0].results[id]
			if retained.localBrowser == nil || *retained.localBrowser != *bound {
				t.Fatal("retained result lost correlation")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != good || s.bytes != 0 || s.resultBytes != len(good) {
				t.Fatal("result altered or counted twice")
			}
		})
	}
}

func TestLocalBrowserRejectsMalformedRequestsBeforeQueue(t *testing.T) {
	f := readLocalBrowserFixture(t)
	for _, row := range f.BrowserCases[:6] {
		for _, field := range []string{"version", "runtime", "op"} {
			value := transferObject(t, row.Request)
			delete(value, field)
			if parseLocalBrowserRequest(transferJSON(t, value)) != nil {
				t.Fatal("missing required field accepted", row.Name, field)
			}
		}
	}
	for _, params := range []string{
		`{}`, `null`, `[]`,
		`{"version":1,"runtime":"native-qa-1","op":"roots","path":"/tmp/usb/private"}`,
		`{"version":1,"runtime":"native-qa-1","op":"roots","generation":"old"}`,
		`{"version":1,"runtime":"native-qa-1","op":"list","generation":"g","entry_id":"e","cursor":"c"}`,
		`{"version":1,"runtime":"native-qa-1","op":"list","generation":"g"}`,
		`{"version":1,"runtime":"native-qa-1","op":"more","generation":"g","entry_id":"e"}`,
		`{"version":1,"runtime":"native-qa-1","op":"select","generation":"g","select_id":null}`,
		`{"version":1,"runtime":"native-qa-1","op":"roots","op":"roots"}`,
		`{"version":1,"runtime":"native-qa-1","op":"root\ud800s"}`,
		`{"version":1.0,"runtime":"native-qa-1","op":"roots"}`,
		`{"version":true,"runtime":"native-qa-1","op":"roots"}`,
		`{"version":1,"runtime":"native-qa-1","op":"select","generation":"g","select_id":"/tmp/usb"}`,
		`{"version":1,"runtime":"native_qa","op":"roots"}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"local_browser","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("invalid request was queued")
		}
	}
	row := f.named(t, "directory_page")
	for _, token := range []string{"", "has space", "токен", strings.Repeat("a", 257)} {
		value := transferObject(t, row.Request)
		value["entry_id"] = transferJSON(t, token)
		if parseLocalBrowserRequest(transferJSON(t, value)) != nil {
			t.Fatal("invalid token accepted", token)
		}
	}
	value := transferObject(t, row.Request)
	value["entry_id"] = transferJSON(t, strings.Repeat("a", 256))
	if parseLocalBrowserRequest(transferJSON(t, value)) == nil {
		t.Fatal("256-byte token rejected")
	}
}

func TestLocalBrowserViewsRejectWrongShapeGenerationAndPrivateFields(t *testing.T) {
	f := readLocalBrowserFixture(t)
	for _, name := range []string{"available_roots", "directory_page", "bounded_more"} {
		row := f.named(t, name)
		for _, mutation := range []func(map[string]json.RawMessage){
			func(v map[string]json.RawMessage) { v["runtime"] = transferJSON(t, "other") },
			func(v map[string]json.RawMessage) { v["op"] = transferJSON(t, "select") },
			func(v map[string]json.RawMessage) { v["state"] = transferJSON(t, "complete") },
			func(v map[string]json.RawMessage) { v["generation"] = transferJSON(t, "/private/generation") },
			func(v map[string]json.RawMessage) { v["next_cursor"] = transferJSON(t, 1) },
			func(v map[string]json.RawMessage) { v["parent_id"] = transferJSON(t, false) },
			func(v map[string]json.RawMessage) { v["partial"] = transferJSON(t, 1) },
			func(v map[string]json.RawMessage) { v["limit_reached"] = transferJSON(t, "false") },
			func(v map[string]json.RawMessage) {
				v["limit_reached"] = transferJSON(t, true)
				v["partial"] = transferJSON(t, false)
			},
			func(v map[string]json.RawMessage) { v["path"] = transferJSON(t, "/private/usb-secret") },
			func(v map[string]json.RawMessage) { v["entries"] = transferJSON(t, nil) },
			func(v map[string]json.RawMessage) {
				v["entries"] = json.RawMessage(`[{"id":"e","name":"one","name":"two","kind":"directory"}]`)
			},
		} {
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, "local_browser", row.Request, ""))
			value := transferObject(t, row.Data)
			mutation(value)
			got := request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, value)), nil)
			expect(t, got, 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 || strings.Contains(got.Body.String(), "usb-secret") {
				t.Fatal("malformed view consumed or leaked")
			}
		}
		q := parseLocalBrowserRequest(row.Request)
		value := transferObject(t, row.Data)
		if q.Generation != "" {
			value["generation"] = transferJSON(t, q.Generation)
			if validLocalBrowserResult(transferJSON(t, value), "ok", q) {
				t.Fatal("old view generation accepted")
			}
		}
	}
	for _, name := range []string{"available_roots", "directory_page"} {
		row := f.named(t, name)
		q := parseLocalBrowserRequest(row.Request)
		for _, field := range []string{"parent_id", "select_id", "next_cursor", "partial", "limit_reached"} {
			value := transferObject(t, row.Data)
			delete(value, field)
			if validLocalBrowserResult(transferJSON(t, value), "ok", q) {
				t.Fatal("missing view field accepted", field)
			}
		}
	}
}

func TestLocalBrowserSelectionIsBoundAndNeverClaimsPersistence(t *testing.T) {
	row := readLocalBrowserFixture(t).named(t, "select_without_playback_or_save")
	q := parseLocalBrowserRequest(row.Request)
	for _, mutation := range []func(map[string]json.RawMessage){
		func(v map[string]json.RawMessage) { v["generation"] = transferJSON(t, "new-view") },
		func(v map[string]json.RawMessage) { v["persisted"] = transferJSON(t, true) },
		func(v map[string]json.RawMessage) { v["activated"] = transferJSON(t, true) },
		func(v map[string]json.RawMessage) { v["persisted"] = transferJSON(t, 0) },
		func(v map[string]json.RawMessage) { delete(v, "activated") },
		func(v map[string]json.RawMessage) { v["count"] = transferJSON(t, 2) },
	} {
		value := transferObject(t, row.Data)
		mutation(value)
		if validLocalBrowserResult(transferJSON(t, value), "ok", q) {
			t.Fatal("bad selection accepted")
		}
	}
	for _, field := range []string{"id", "name", "kind", "handle", "stable_identity"} {
		value := transferObject(t, row.Data)
		source := transferObject(t, value["source"])
		delete(source, field)
		value["source"] = transferJSON(t, source)
		if validLocalBrowserResult(transferJSON(t, value), "ok", q) {
			t.Fatal("missing source field accepted", field)
		}
	}
	for _, patch := range []map[string]any{{"handle": "/tmp/usb"}, {"kind": "file"}, {"stable_identity": 1}, {"path": "private"}, {"usb": map[string]any{"uuid": "secret"}}} {
		value := transferObject(t, row.Data)
		source := transferObject(t, value["source"])
		for key, v := range patch {
			source[key] = transferJSON(t, v)
		}
		value["source"] = transferJSON(t, source)
		if validLocalBrowserResult(transferJSON(t, value), "ok", q) {
			t.Fatal("bad source projection accepted", patch)
		}
	}
	value := transferObject(t, row.Data)
	source := transferObject(t, value["source"])
	source["stable_identity"] = transferJSON(t, false)
	value["source"] = transferJSON(t, source)
	if !validLocalBrowserResult(transferJSON(t, value), "ok", q) {
		t.Fatal("truthful unstable selection rejected")
	}
}

func TestLocalBrowserRowsNamesFlagsAndFullEnvelopeBound(t *testing.T) {
	row := readLocalBrowserFixture(t).named(t, "directory_page")
	q := parseLocalBrowserRequest(row.Request)
	for _, name := range []string{" ", "..", "A/B", `A\B`, "Kids: Cartoons", strings.Repeat("я", 256)} {
		if !localBrowserName(transferJSON(t, name)) {
			t.Fatal("display name rejected", name)
		}
	}
	for _, name := range []string{"", "x\n", "x\x7f", "x\u0085", strings.Repeat("я", 256) + "x"} {
		if localBrowserName(transferJSON(t, name)) {
			t.Fatal("invalid display name accepted", name)
		}
	}
	for _, count := range []int{0, 64, 65} {
		value := transferObject(t, row.Data)
		rows := []any{}
		for i := 0; i < count; i++ {
			rows = append(rows, map[string]any{"id": "entry-" + strconv.Itoa(i), "name": "Folder", "kind": "directory"})
		}
		value["entries"] = transferJSON(t, rows)
		if validLocalBrowserResult(transferJSON(t, value), "ok", q) != (count <= 64) {
			t.Fatal("entry bound", count)
		}
	}
	value := transferObject(t, row.Data)
	value["entries"] = json.RawMessage(`[{"id":"same","name":"one","kind":"directory"},{"id":"same","name":"two","kind":"directory"}]`)
	if validLocalBrowserResult(transferJSON(t, value), "ok", q) {
		t.Fatal("duplicate row accepted")
	}
	// Raw envelope bytes, including whitespace, count toward the budget.
	for _, size := range []int{maxLocalBrowserEnvelopeBytes, maxLocalBrowserEnvelopeBytes + 1} {
		s := newTestServer(t)
		id := rpcID(t, s, globalRPC(t, "local_browser", row.Request, ""))
		body := transferResponse(t, id, "ok", row.Data)
		body = body[:len(body)-1] + strings.Repeat(" ", size-len(body)) + "}"
		code := 200
		if size > maxLocalBrowserEnvelopeBytes {
			code = 400
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), code)
	}
	// 64 legal names may still exceed the encoded envelope after JSON escaping.
	value = transferObject(t, row.Data)
	rows := []any{}
	for i := 0; i < 64; i++ {
		rows = append(rows, map[string]any{"id": "entry-" + strconv.Itoa(i), "name": strings.Repeat("<", 512), "kind": "directory"})
	}
	value["entries"] = transferJSON(t, rows)
	if validLocalBrowserResult(transferJSON(t, value), "ok", q) {
		t.Fatal("escaping expanded result beyond limit")
	}
}

func TestLocalBrowserClosedErrorsAndResultExpiry(t *testing.T) {
	row := readLocalBrowserFixture(t).named(t, "roots_busy")
	q := parseLocalBrowserRequest(row.Request)
	for code := range localBrowserErrors {
		value := transferObject(t, row.Data)
		value["error"] = transferJSON(t, code)
		for _, status := range []string{"ok", "rejected", "unsupported"} {
			if validLocalBrowserResult(transferJSON(t, value), status, q) != (status != "ok") {
				t.Fatal("error envelope status", code, status)
			}
		}
	}
	for _, patch := range []map[string]any{{"error": "secret"}, {"generation": "g"}, {"runtime": "other"}, {"op": "select"}, {"error": true}} {
		value := transferObject(t, row.Data)
		for key, v := range patch {
			value[key] = transferJSON(t, v)
		}
		if validLocalBrowserResult(transferJSON(t, value), "rejected", q) {
			t.Fatal("invalid error accepted", patch)
		}
	}
	s := newTestServer(t)
	id := rpcID(t, s, globalRPC(t, "local_browser", row.Request, ""))
	s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, row.Status, row.Data), nil), 404)
	if len(s.devices[0].results) != 0 {
		t.Fatal("expired result stored")
	}
}

func TestLocalBrowserViewKindsAndScanFlagsAreIndependent(t *testing.T) {
	f := readLocalBrowserFixture(t)
	for _, name := range []string{"available_roots", "directory_page", "bounded_more"} {
		row := f.named(t, name)
		q := parseLocalBrowserRequest(row.Request)
		for _, partial := range []bool{false, true} {
			for _, limit := range []bool{false, true} {
				for _, cursor := range []any{nil, "retained-next"} {
					value := transferObject(t, row.Data)
					value["partial"], value["limit_reached"], value["next_cursor"] = transferJSON(t, partial), transferJSON(t, limit), transferJSON(t, cursor)
					if validLocalBrowserResult(transferJSON(t, value), "ok", q) != (!limit || partial) {
						t.Fatal("scan completeness confused with retained pagination", name, partial, limit, cursor)
					}
				}
			}
		}
		value := transferObject(t, row.Data)
		value["kind"] = transferJSON(t, "files")
		if validLocalBrowserResult(transferJSON(t, value), "ok", q) {
			t.Fatal("unsupported view kind accepted")
		}
		value = transferObject(t, row.Data)
		if q.Op == "roots" {
			value["select_id"] = transferJSON(t, "selection")
		} else {
			value["select_id"] = transferJSON(t, nil)
		}
		if validLocalBrowserResult(transferJSON(t, value), "ok", q) {
			t.Fatal("wrong view selection shape accepted")
		}
	}
	// A parent entry, as well as a roots cursor, can lead back to a roots view.
	parent := f.named(t, "directory_page")
	rootPage := transferObject(t, f.named(t, "available_roots").Data)
	rootPage["op"], rootPage["generation"] = transferJSON(t, "list"), transferJSON(t, "parent-roots")
	if !validLocalBrowserResult(transferJSON(t, rootPage), "ok", parseLocalBrowserRequest(parent.Request)) {
		t.Fatal("parent roots view rejected")
	}
}

func TestLocalBrowserRawJSONRejectsDuplicatesAndInvalidUnicode(t *testing.T) {
	row := readLocalBrowserFixture(t).named(t, "directory_page")
	for _, replacement := range []string{`"runtime":"native-qa-1","runtime":`, `"runtime":"\ud800","runtime":`} {
		body := strings.Replace(globalRPC(t, "local_browser", row.Request, ""), `"runtime":`, replacement, 1)
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("ambiguous raw request queued")
		}
	}
	for _, replacement := range []string{`"name":"first","name":`, `"name":"\ud800","name":`} {
		s := newTestServer(t)
		id := rpcID(t, s, globalRPC(t, "local_browser", row.Request, ""))
		body := strings.Replace(transferResponse(t, id, "ok", row.Data), `"name":`, replacement, 1)
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("ambiguous raw response consumed request")
		}
	}
	for _, raw := range []json.RawMessage{json.RawMessage(`"\ud800"`), json.RawMessage{'"', 0xff, '"'}} {
		if localBrowserName(raw) {
			t.Fatal("invalid Unicode display text accepted")
		}
	}
}

func TestLocalBrowserOperatorScopesAndLegacyQueueIsolation(t *testing.T) {
	f := readLocalBrowserFixture(t)
	for _, name := range []string{"available_roots", "directory_page", "bounded_more", "select_without_playback_or_save"} {
		row := f.named(t, name)
		op := parseLocalBrowserRequest(row.Request).Op
		credential := strings.Repeat("v", 32)
		hash := sha256.Sum256([]byte(credential))
		c := testConfig()
		c.Operators = []config.CommandOperator{{ID: "browser", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"local_browser." + op}}}
		s, err := New(c)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = s.Close() })
		body := globalRPC(t, "local_browser", row.Request, "")
		expect(t, request(s, "POST", "/api/requests?device_id=second", credential, body, nil), 403)
		accepted := request(s, "POST", "/api/requests?device_id=first", credential, body, nil)
		expect(t, accepted, 202)
		var queued map[string]string
		if json.Unmarshal(accepted.Body.Bytes(), &queued) != nil {
			t.Fatal("queue receipt")
		}
		id := queued["id"]
		other := f.named(t, "available_roots")
		if op == "roots" {
			other = f.named(t, "bounded_more")
		}
		expect(t, request(s, "POST", "/api/requests?device_id=first", credential, globalRPC(t, "local_browser", other.Request, ""), nil), 403)
		if len(commands(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), false)) != 0 {
			t.Fatal("browser request leaked to legacy queue")
		}
		expect(t, request(s, "POST", "/api/webhook/commands/ack", firstToken, `{"ids":["`+id+`"]}`, nil), 200)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("legacy acknowledgement consumed browser request")
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, row.Status, row.Data), nil), 200)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, credential, "", nil), 200)
	}
}
