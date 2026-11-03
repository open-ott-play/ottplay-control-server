package control

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

type nativePlaybackContract struct {
	Runtime string `json:"runtime"`
	Section string `json:"section"`
	Cases   []struct {
		Name string          `json:"name"`
		Data json.RawMessage `json:"expected_data"`
	} `json:"cases"`
	Errors []struct {
		Status string          `json:"status"`
		Data   json.RawMessage `json:"data"`
	} `json:"error_envelopes"`
}

func nativePlaybackFixture(t testing.TB) nativePlaybackContract {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/native-playback-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture nativePlaybackContract
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Runtime == "" || fixture.Section != "native_playback" || len(fixture.Cases) != 8 || len(fixture.Errors) != 4 {
		t.Fatal("incomplete shared native playback contract fixture")
	}
	return fixture
}

func (f nativePlaybackContract) body(t testing.TB, name string) map[string]any {
	t.Helper()
	for _, row := range f.Cases {
		if row.Name == name {
			var data map[string]any
			if err := json.Unmarshal(row.Data, &data); err != nil {
				t.Fatal(err)
			}
			return map[string]any{"version": 1, "runtime": f.Runtime, "section": f.Section, "data": data}
		}
	}
	t.Fatalf("missing fixture %q", name)
	return nil
}

func (f nativePlaybackContract) expected() *inspectRequest {
	return &inspectRequest{Version: 1, Runtime: f.Runtime, Section: f.Section}
}

func (f nativePlaybackContract) request() string {
	return `{"action":"inspect","params":{"version":1,"runtime":"` + f.Runtime + `","section":"native_playback"}}`
}

func TestNativePlaybackSharedContractRoundTrip(t *testing.T) {
	f := nativePlaybackFixture(t)
	for _, row := range f.Cases {
		t.Run(row.Name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, f.request())
			if got := s.devices[0].queue[0].inspect; got == nil || *got != *f.expected() {
				t.Fatal("request lost its native playback binding")
			}
			body := screenshotEnvelope(id, "ok", f.body(t, row.Name))
			expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
			expect(t, request(s, "POST", "/api/responses", adminToken, body, nil), 403)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != body || s.resultBytes != len(body) || s.bytes != 0 || len(s.devices[0].queue) != 0 {
				t.Fatal("native playback result changed, duplicated, or remained queued")
			}
			bad := f.body(t, row.Name)
			bad["runtime"] = "different-runtime"
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", bad), nil), 400)
			retained := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			if retained.Body.String() != body || s.resultBytes != len(body) {
				t.Fatal("mismatched duplicate replaced the retained result")
			}
		})
	}
	for _, row := range f.Errors {
		t.Run("error_"+string(row.Data), func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, f.request())
			body := screenshotEnvelope(id, row.Status, row.Data)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != body || s.bytes != 0 {
				t.Fatal("shared negative fixture did not round trip exactly")
			}
		})
	}
}

func TestNativePlaybackRequestVersionAndExactShape(t *testing.T) {
	f := nativePlaybackFixture(t)
	params := map[string]any{"version": 1, "runtime": f.Runtime, "section": f.Section}
	for _, runtime := range []string{"a", "A_0.-", strings.Repeat("a", 96)} {
		params["runtime"] = runtime
		if parseInspectRequest(transferObject(t, transferJSON(t, params))) == nil {
			t.Fatalf("valid runtime rejected: %q", runtime)
		}
	}
	for _, bad := range []string{
		`{}`, `null`, `[]`,
		`{"version":2,"runtime":"native_fixture_1","section":"native_playback"}`,
		`{"version":true,"runtime":"native_fixture_1","section":"native_playback"}`,
		`{"version":1.0,"runtime":"native_fixture_1","section":"native_playback"}`,
		`{"version":1,"runtime":"","section":"native_playback"}`,
		`{"version":1,"runtime":"with space","section":"native_playback"}`,
		`{"version":1,"runtime":"native/fixture","section":"native_playback"}`,
		`{"version":1,"runtime":"нативный","section":"native_playback"}`,
		`{"version":1,"runtime":"` + strings.Repeat("a", 97) + `","section":"native_playback"}`,
		`{"version":1,"runtime":"native_fixture_1","section":"native_playback","params":{}}`,
		`{"version":1,"runtime":"native_fixture_1","section":"native_playback","operation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}`,
		`{"version":1,"runtime":"native_fixture_1","section":"native_playback","section":"debug"}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"inspect","params":`+bad+`}`, nil), 400)
		if s.bytes != 0 || len(s.devices[0].queue) != 0 {
			t.Fatal("invalid native playback request was queued")
		}
	}
}

func TestNativePlaybackCapabilityOptionalStrictAndIndependent(t *testing.T) {
	for _, marker := range []string{"", `{"version":1}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		// The old inspect sections remain producer-owned and unchanged.
		data := `{"inspect":{"version":1,"sections":["doctor","snapshot","operation","debug"]}}`
		if marker != "" {
			data = strings.TrimSuffix(data, "}") + `,"native_playback_diagnostics":` + marker + `}`
		}
		body := screenshotEnvelope(id, "ok", json.RawMessage(data))
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		if got.Body.String() != body {
			t.Fatal("optional native capability rewrote legacy capabilities")
		}
	}
	for _, bad := range []string{`{}`, `null`, `[]`, `true`, `1`, `{"version":null}`, `{"version":true}`, `{"version":0}`, `{"version":2}`, `{"version":1.0}`, `{"version":1e0}`, `{"version":"1"}`, `{"version":1,"version":1}`, `{"version":1,"url":"https://private.invalid/fixture-secret"}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		body := `{"id":"` + id + `","status":"ok","data":{"native_playback_diagnostics":` + bad + `}}`
		got := request(s, "POST", "/api/responses", firstToken, body, nil)
		expect(t, got, 400)
		if s.resultBytes != 0 || len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || strings.Contains(got.Body.String(), "fixture-secret") {
			t.Fatal("invalid capability consumed request or retained private data")
		}
	}
}

func TestNativePlaybackAllEnumsAndNullableMeasurements(t *testing.T) {
	f := nativePlaybackFixture(t)
	valid := func(body map[string]any) {
		t.Helper()
		if !validInspectResult(transferJSON(t, body), "ok", f.expected()) {
			t.Fatal("contract-valid native playback sample rejected")
		}
	}
	for _, phase := range []string{"idle", "resolving", "opening", "buffering", "playing", "paused", "seeking", "stopping", "error", "standby", "unknown"} {
		body := f.body(t, "idle")
		body["data"].(map[string]any)["phase"] = phase
		valid(body)
	}
	for _, stage := range []string{"admitted", "waiting_catalog", "resolving_source"} {
		for _, age := range []any{nil, 0, json.Number("9007199254740991")} {
			body := f.body(t, "pending")
			body["data"].(map[string]any)["pending_command"] = map[string]any{"stage": stage, "age_ms": age}
			valid(body)
		}
	}
	for _, code := range []string{"media_source", "playback_interrupted", "tv_pipeline", "load_timeout", "release_timeout", "resolve_failed", "unspecified"} {
		body := f.body(t, "load_timeout")
		body["data"].(map[string]any)["last_error"] = map[string]any{"code": code}
		valid(body)
	}
	for _, epoch := range []any{1, json.Number("4294967295")} {
		body := f.body(t, "displayed_intervals")
		body["data"].(map[string]any)["native_epoch"] = epoch
		valid(body)
	}
	body := f.body(t, "displayed_intervals")
	data := body["data"].(map[string]any)
	// Some firmware reports Displayed without verified interval semantics.
	// A report and its age do not make the frame count knowable.
	data["counters"].(map[string]any)["video_displayed_frames"] = nil
	valid(body)
	for _, group := range []string{"counters", "ages_ms"} {
		for key := range data[group].(map[string]any) {
			candidate := f.body(t, "displayed_intervals")
			candidate["data"].(map[string]any)[group].(map[string]any)[key] = nil
			valid(candidate) // An individual measurement can become unrepresentable.
		}
	}
}

func TestNativePlaybackMeasurementIntegerBounds(t *testing.T) {
	f := nativePlaybackFixture(t)
	fields := map[string][]string{
		"counters":        {"video_submitted_units", "audio_submitted_units", "video_position_events", "video_displayed_reports", "video_displayed_frames"},
		"ages_ms":         {"video_submission", "audio_submission", "video_position_event", "video_displayed_report"},
		"pending_command": {"age_ms"},
	}
	for group, keys := range fields {
		for _, key := range keys {
			t.Run(group+"/"+key, func(t *testing.T) {
				for _, value := range []any{nil, 0, json.Number("9007199254740991"), true, false, -1, "0", []any{}, map[string]any{}, json.Number("0.5"), json.Number("1.0"), json.Number("1e0"), json.Number("9007199254740990.9"), json.Number("9007199254740992"), json.Number("9223372036854775808"), json.Number("1e999")} {
					body := f.body(t, "displayed_intervals")
					data := body["data"].(map[string]any)
					data["pending_command"] = map[string]any{"stage": "admitted", "age_ms": nil}
					data[group].(map[string]any)[key] = value
					encoded := transferJSON(t, value)
					want := value == nil || string(encoded) == "0" || string(encoded) == "9007199254740991"
					if validInspectResult(transferJSON(t, body), "ok", f.expected()) != want {
						t.Fatalf("wrong acceptance for %s", encoded)
					}
				}
			})
		}
	}
	for _, value := range []any{true, false, 0, -1, "1", 0.5, json.Number("1.0"), json.Number("1e0"), json.Number("4294967296"), json.Number("9007199254740991")} {
		body := f.body(t, "idle")
		body["data"].(map[string]any)["native_epoch"] = value
		if validInspectResult(transferJSON(t, body), "ok", f.expected()) {
			t.Fatalf("invalid epoch accepted: %v", value)
		}
	}
	for _, key := range []string{"video_demuxed_units", "audio_demuxed_units", "video_decoded_frames", "audio_decoded_frames", "audio_presented_frames"} {
		for _, value := range []any{0, 1, true, "unavailable", map[string]any{}} {
			body := f.body(t, "displayed_intervals")
			body["data"].(map[string]any)["counters"].(map[string]any)[key] = value
			if validInspectResult(transferJSON(t, body), "ok", f.expected()) {
				t.Fatalf("v1 accepted an unmeasured %s: %v", key, value)
			}
		}
	}
}

func TestNativePlaybackExactFieldsAndNoEpochEvidence(t *testing.T) {
	f := nativePlaybackFixture(t)
	full := func() map[string]any {
		body := f.body(t, "displayed_intervals")
		data := body["data"].(map[string]any)
		data["phase"] = "error"
		data["pending_command"] = map[string]any{"stage": "admitted", "age_ms": 0}
		data["last_error"] = map[string]any{"code": "load_timeout"}
		return body
	}
	objectAt := func(body map[string]any, path []string) map[string]any {
		for _, key := range path {
			body = body[key].(map[string]any)
		}
		return body
	}
	paths := [][]string{{}, {"data"}, {"data", "counters"}, {"data", "ages_ms"}, {"data", "pending_command"}, {"data", "last_error"}}
	for _, path := range paths {
		t.Run(strings.Join(path, "/"), func(t *testing.T) {
			for key := range objectAt(full(), path) {
				body := full()
				delete(objectAt(body, path), key)
				if validInspectResult(transferJSON(t, body), "ok", f.expected()) {
					t.Fatalf("missing field %s accepted", key)
				}
			}
			body := full()
			objectAt(body, path)["private_url"] = "https://private.invalid/?token=fixture-secret"
			if validInspectResult(transferJSON(t, body), "ok", f.expected()) {
				t.Fatal("extra private field accepted")
			}
			for _, wrongType := range []any{false, "", []any{}, 1} {
				body := full()
				var document any = body
				if len(path) == 0 {
					document = wrongType
				} else {
					objectAt(body, path[:len(path)-1])[path[len(path)-1]] = wrongType
				}
				if validInspectResult(transferJSON(t, document), "ok", f.expected()) {
					t.Fatal("non-object accepted")
				}
			}
		})
	}
	for _, group := range []string{"counters", "ages_ms"} {
		for key := range f.body(t, "idle")["data"].(map[string]any)[group].(map[string]any) {
			body := f.body(t, "idle")
			body["data"].(map[string]any)[group].(map[string]any)[key] = 0
			if validInspectResult(transferJSON(t, body), "ok", f.expected()) {
				t.Fatalf("%s.%s reported evidence without an epoch", group, key)
			}
		}
	}
}

func TestNativePlaybackRejectedRepliesNeverConsumeStoreOrLeak(t *testing.T) {
	f := nativePlaybackFixture(t)
	mutations := map[string]func(map[string]any){
		"runtime":          func(body map[string]any) { body["runtime"] = "another-page" },
		"section":          func(body map[string]any) { body["section"] = "debug" },
		"version":          func(body map[string]any) { body["version"] = 2 },
		"private envelope": func(body map[string]any) { body["url"] = "https://private.invalid/?token=fixture-secret" },
		"inner version":    func(body map[string]any) { body["data"].(map[string]any)["version"] = true },
		"scope":            func(body map[string]any) { body["data"].(map[string]any)["scope"] = "player" },
		"phase":            func(body map[string]any) { body["data"].(map[string]any)["phase"] = "complete" },
		"private phase":    func(body map[string]any) { body["data"].(map[string]any)["phase"] = "fixture-secret" },
		"error outside terminal phase": func(body map[string]any) {
			body["data"].(map[string]any)["last_error"] = map[string]any{"code": "load_timeout"}
		},
		"private error": func(body map[string]any) {
			data := body["data"].(map[string]any)
			data["phase"] = "error"
			data["last_error"] = map[string]any{"code": "fixture-secret"}
		},
		"private error detail": func(body map[string]any) {
			data := body["data"].(map[string]any)
			data["phase"] = "error"
			data["last_error"] = map[string]any{"code": "load_timeout", "message": "fixture-secret"}
		},
		"unsupported pending stage": func(body map[string]any) {
			body["data"].(map[string]any)["pending_command"] = map[string]any{"stage": "waiting_playback", "age_ms": 0}
		},
		"private pending command": func(body map[string]any) {
			body["data"].(map[string]any)["pending_command"] = map[string]any{"stage": "admitted", "age_ms": 0, "params": "fixture-secret"}
		},
		"fractional epoch": func(body map[string]any) { body["data"].(map[string]any)["native_epoch"] = 7.5 },
		"overflow measurement": func(body map[string]any) {
			body["data"].(map[string]any)["counters"].(map[string]any)["video_submitted_units"] = json.Number("9007199254740992")
		},
		"invented decoder measurement": func(body map[string]any) {
			body["data"].(map[string]any)["counters"].(map[string]any)["video_decoded_frames"] = 0
		},
		"private counter field": func(body map[string]any) {
			body["data"].(map[string]any)["counters"].(map[string]any)["url"] = "fixture-secret"
		},
		"private event field": func(body map[string]any) {
			body["data"].(map[string]any)["ages_ms"].(map[string]any)["token"] = "fixture-secret"
		},
	}
	s := newTestServer(t)
	id := rpcID(t, s, f.request())
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			body := f.body(t, "displayed_intervals")
			mutate(body)
			got := request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", body), nil)
			expect(t, got, 400)
			if s.resultBytes != 0 || len(s.devices[0].results) != 0 || len(s.devices[0].queue) != 1 {
				t.Fatal("rejected reply changed the pending request or stored a result")
			}
			pending := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, pending, 202)
			if strings.Contains(got.Body.String()+pending.Body.String(), "fixture-secret") {
				t.Fatal("rejected private value leaked to a response")
			}
		})
	}
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", f.body(t, "displayed_intervals")), nil), 200)
}

func TestNativePlaybackNegativeStatusAndBinding(t *testing.T) {
	f := nativePlaybackFixture(t)
	for _, row := range f.Errors {
		for _, status := range []string{"ok", "rejected", "unsupported", "error", "accepted"} {
			want := status == row.Status
			if validInspectResult(row.Data, status, f.expected()) != want {
				t.Fatalf("wrong status acceptance for %s with %s", row.Data, status)
			}
		}
		for _, change := range []struct {
			key   string
			value any
		}{
			{"runtime", "different-runtime"}, {"runtime", nil}, {"section", "doctor"},
			{"version", 2}, {"version", true}, {"error", "fixture-secret"},
			{"error", map[string]any{"code": "unavailable"}}, {"data", nil},
		} {
			s := newTestServer(t)
			id := rpcID(t, s, f.request())
			body := transferObject(t, row.Data)
			body[change.key] = transferJSON(t, change.value)
			got := request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, row.Status, body), nil)
			expect(t, got, 400)
			if s.resultBytes != 0 || len(s.devices[0].results) != 0 || len(s.devices[0].queue) != 1 || strings.Contains(got.Body.String(), "fixture-secret") {
				t.Fatal("unbound negative reply consumed or leaked request data")
			}
		}
	}
}

func TestNativePlaybackMalformedJSONAndFullEnvelopeBudget(t *testing.T) {
	f := nativePlaybackFixture(t)
	body := f.body(t, "load_timeout")
	body["data"].(map[string]any)["pending_command"] = map[string]any{"stage": "admitted", "age_ms": 0}
	compact := string(transferJSON(t, body))
	mutations := []string{
		`{"version":1,` + compact[1:],
		strings.Replace(compact, `"data":{`, `"data":{"version":1,`, 1),
		strings.Replace(compact, `"counters":{`, `"counters":{"video_submitted_units":null,`, 1),
		strings.Replace(compact, `"ages_ms":{`, `"ages_ms":{"video_submission":null,`, 1),
		strings.Replace(compact, `"pending_command":{`, `"pending_command":{"stage":"admitted",`, 1),
		strings.Replace(compact, `"last_error":{`, `"last_error":{"code":"load_timeout",`, 1),
		compact + `{}`, strings.TrimSuffix(compact, "}"),
		strings.Replace(compact, `"native_epoch":null`, `"native_epoch":NaN`, 1),
		strings.Replace(compact, `"version":1`, `"version":1.0`, 1),
	}
	s := newTestServer(t)
	id := rpcID(t, s, f.request())
	postRaw := func(raw string, expectedStatus int) {
		t.Helper()
		// Preserve raw duplicate keys and whitespace; json.Marshal compacts RawMessage.
		wire := `{"id":"` + id + `","status":"ok","data":` + raw + `}`
		expect(t, request(s, "POST", "/api/responses", firstToken, wire, nil), expectedStatus)
	}
	for _, raw := range mutations {
		if raw == compact || validInspectResult([]byte(raw), "ok", f.expected()) {
			t.Fatal("malformed JSON accepted or mutation did not apply")
		}
		postRaw(raw, 400)
		if s.resultBytes != 0 || len(s.devices[0].results) != 0 || len(s.devices[0].queue) != 1 {
			t.Fatal("malformed reply consumed request")
		}
	}
	// The inner envelope, not just data or the transport wrapper, has a 4096-byte limit.
	padded := compact[:1] + strings.Repeat(" ", maxNativePlaybackResultBytes-len(compact)) + compact[1:]
	if len(padded) != 4096 || !validInspectResult([]byte(padded), "ok", f.expected()) {
		t.Fatal("exact inner envelope budget rejected")
	}
	tooLarge := padded[:1] + " " + padded[1:]
	if validInspectResult([]byte(tooLarge), "ok", f.expected()) {
		t.Fatal("oversized native envelope accepted")
	}
	postRaw(tooLarge, 400)
	postRaw(padded, 200)
	for _, row := range f.Errors {
		raw := bytes.TrimSpace(row.Data)
		padding := bytes.Repeat([]byte(" "), maxNativePlaybackResultBytes-len(raw))
		atLimit := append(append(append([]byte{}, raw[:1]...), padding...), raw[1:]...)
		if !validInspectResult(atLimit, row.Status, f.expected()) || validInspectResult(append(atLimit, ' '), row.Status, f.expected()) {
			t.Fatal("negative response ignored the full inner envelope budget")
		}
	}
}

func TestNativePlaybackDoesNotSubstituteForLegacyInspect(t *testing.T) {
	f := nativePlaybackFixture(t)
	for _, section := range []string{"doctor", "snapshot", "operation", "debug"} {
		for _, version := range []int{1, 2, 3} {
			body := f.body(t, "idle")
			body["section"], body["version"] = section, version
			expected := &inspectRequest{Runtime: f.Runtime, Section: section, Version: version}
			if validInspectResult(transferJSON(t, body), "ok", expected) {
				t.Fatalf("native payload accepted as legacy %s v%d", section, version)
			}
		}
	}
	for _, version := range []int{0, 2, 3} {
		body := f.body(t, "idle")
		body["version"] = version
		expected := f.expected()
		expected.Version = version
		if validInspectResult(transferJSON(t, body), "ok", expected) {
			t.Fatal("unnegotiated native version accepted")
		}
	}
	if validInspectResult(transferJSON(t, f.body(t, "idle")), "ok", nil) {
		t.Fatal("unbound native result accepted")
	}
}
