package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
)

func perfPolicyFixture(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/perf-capture-v3.json")
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	return value
}

func perfPolicyReply(t *testing.T, op string, captureVersion int, kinds ...string) map[string]any {
	t.Helper()
	v := perfReply(t, op)
	v["version"] = 3
	s := v["session"].(map[string]any)
	s["capture_version"] = captureVersion
	if op != "read" {
		return v
	}
	f := perfPolicyFixture(t)
	rows := []any{}
	for i, kind := range kinds {
		e := perfExample(t, f, f["$defs"].(map[string]any)[kind+"_event"].(map[string]any)).(map[string]any)
		e["seq"], e["at_ms"], e["lane"], e["source_id"] = i+1, i, "main", "s1"
		if kind == "hls_buffer_policy" {
			data := e["data"].(map[string]any)
			data["previous_max_max_buffer_length_seconds"] = nil
			for _, name := range []string{"initial", "current"} {
				for key := range data[name].(map[string]any) {
					data[name].(map[string]any)[key] = nil
				}
			}
		}
		rows = append(rows, e)
	}
	s["first_seq"], s["next_seq"], s["event_count"], s["bytes"] = 1, len(rows)+1, len(rows), len(rows)*256
	v["events"], v["next_after_seq"] = rows, len(rows)
	return v
}

func TestPerfHLSPolicyCapabilityAndLegacyCanary(t *testing.T) {
	for name, expected := range map[string]string{
		"v1": "e80ee9749d2b8594d3e008c80a873dcdc2201abfadbafd19a376e5a3467838d0",
		"v2": "b1c83b2c86fce859647b68c4240368ace2b2b2865389b84eede58803d38a37ee",
	} {
		raw, err := os.ReadFile("../../contracts/perf-capture-" + name + ".json")
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(raw)
		if hex.EncodeToString(sum[:]) != expected {
			t.Fatal("legacy contract changed", name)
		}
	}
	if !validPerfHLSPolicyCapability([]byte(`{"version":3}`)) {
		t.Fatal("v3 marker rejected")
	}
	for _, bad := range []string{`null`, `[]`, `{"version":true}`, `{"version":2}`, `{"version":4}`, `{"version":3,"extra":0}`} {
		if validPerfHLSPolicyCapability([]byte(bad)) {
			t.Fatal("invalid v3 marker admitted", bad)
		}
	}
	cap := perfPolicyFixture(t)["capability"].(map[string]any)
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", cap), nil), 200)
	for _, fields := range []map[string]any{
		{"perf_capture_hls_policy": map[string]any{"version": 3}},
		{"perf_capture": cap["perf_capture"], "perf_capture_hls_policy": map[string]any{"version": true}},
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", fields), nil), 400)
	}
}

func TestPerfHLSPolicyRoundtripAndVersionFences(t *testing.T) {
	for _, op := range []string{"start", "status", "read", "stop"} {
		p := perfParams(op)
		p["version"] = 3
		params, _ := json.Marshal(p)
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"perf_capture","params":`+string(params)+`}`)
		body := perfPolicyReply(t, op, 3, "hls", "hls_fragment", "hls_buffer_policy", "hls_error_action")
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", body), nil), 200)
	}
	for _, capture := range []int{1, 2, 3} {
		p := perfParams("read")
		p["version"] = 3
		raw, _ := json.Marshal(perfPolicyReply(t, "read", capture, "sample"))
		if !validPerfCaptureResult(raw, "ok", perfExpected(t, p)) {
			t.Fatal("v3 reader rejected preserved capture version", capture)
		}
	}
	for _, version := range []int{1, 2} {
		p := perfParams("read")
		p["version"] = version
		reply := perfPolicyReply(t, "read", 3, "sample")
		reply["version"] = version
		raw, _ := json.Marshal(reply)
		if validPerfCaptureResult(raw, "ok", perfExpected(t, p)) {
			t.Fatal("legacy envelope accepted v3 session", version)
		}
	}
	for _, capture := range []int{1, 2} {
		p := perfParams("start")
		p["version"] = 3
		raw, _ := json.Marshal(perfPolicyReply(t, "start", capture))
		if validPerfCaptureResult(raw, "ok", perfExpected(t, p)) {
			t.Fatal("v3 start silently downgraded", capture)
		}
	}
	p := perfParams("start")
	p["version"] = 3
	negative := map[string]any{"version": 3, "op": "start", "runtime": "page-one", "error": "unsupported"}
	raw, _ := json.Marshal(negative)
	if !validPerfCaptureResult(raw, "unsupported", perfExpected(t, p)) {
		t.Fatal("v3 bound error rejected")
	}
	negative["version"] = 2
	raw, _ = json.Marshal(negative)
	if validPerfCaptureResult(raw, "unsupported", perfExpected(t, p)) {
		t.Fatal("error crossed version fence")
	}
}

func TestPerfHLSPolicySemanticsAndUnknowns(t *testing.T) {
	p := perfParams("read")
	p["version"] = 3
	expected := perfExpected(t, p)
	check := func(kind string, edit func(map[string]any), valid bool) {
		t.Helper()
		reply := perfPolicyReply(t, "read", 3, kind)
		data := reply["events"].([]any)[0].(map[string]any)["data"].(map[string]any)
		edit(data)
		raw, _ := json.Marshal(reply)
		if validPerfCaptureResult(raw, "ok", expected) != valid {
			t.Fatalf("unexpected validation for %s: %s", kind, raw)
		}
	}
	check("hls_buffer_policy", func(d map[string]any) {
		d["event"] = "cap_observation_changed"
		d["previous_max_max_buffer_length_seconds"] = 20
		d["current"].(map[string]any)["max_max_buffer_length_seconds"] = 5.005
	}, true)
	check("hls_buffer_policy", func(d map[string]any) {
		d["event"] = "cap_observation_changed"
		d["previous_max_max_buffer_length_seconds"] = 20
	}, true)
	check("hls_buffer_policy", func(d map[string]any) { d["initial_source"] = "unavailable" }, true)
	check("hls_buffer_policy", func(d map[string]any) { d["event"] = "cap_observation_changed" }, false)
	check("hls_buffer_policy", func(d map[string]any) { d["previous_max_max_buffer_length_seconds"] = 20 }, false)
	check("hls_buffer_policy", func(d map[string]any) {
		d["initial_source"] = "unavailable"
		d["initial"].(map[string]any)["max_buffer_length_seconds"] = 20
	}, false)
	check("hls_buffer_policy", func(d map[string]any) { d["cause"] = "buffer_full_error" }, false)
	check("hls_buffer_policy", func(d map[string]any) { d["current"].(map[string]any)["max_buffer_size_bytes"] = 1.5 }, false)
	check("hls_buffer_policy", func(d map[string]any) { d["url"] = "private" }, false)
	check("hls", func(d map[string]any) { d["stats_retry"] = 1; d["detail"] = "buffer_append_no_progress" }, true)
	check("hls", func(d map[string]any) { d["retries"] = 1 }, false)
	check("hls", func(d map[string]any) { d["actual_network_retries"] = 1 }, false)
	check("hls_fragment", func(d map[string]any) {
		d["fragment_gap"] = true
		d["part_gap"] = false
		d["elementary_streams"] = []any{map[string]any{"track": "video", "start_pts_seconds": 0, "end_pts_seconds": 10, "partial": true}}
	}, true)
	check("hls_fragment", func(d map[string]any) { d["part_independent"] = 1 }, false)
	check("hls_error_action", func(d map[string]any) {
		d["action"] = "retry_request"
		d["flags"] = 31
		d["retry_budget_count"] = 1
		d["detail"] = "buffer_add_codec_error"
	}, true)
	check("hls_error_action", func(d map[string]any) { d["flags"] = 32 }, false)
	check("hls_error_action", func(d map[string]any) { d["retry_budget_count"] = true }, false)
	for _, kind := range []string{"hls", "hls_fragment", "hls_buffer_policy", "hls_error_action"} {
		for _, version := range []int{1, 2} {
			reply := perfPolicyReply(t, "read", version, kind)
			raw, _ := json.Marshal(reply)
			if validPerfCaptureResult(raw, "ok", expected) {
				t.Fatal("new payload accepted in old capture", kind, version)
			}
		}
	}
	for _, version := range []int{1, 2} {
		reply := perfPolicyReply(t, "read", version, "hls_legacy")
		raw, _ := json.Marshal(reply)
		if !validPerfCaptureResult(raw, "ok", expected) {
			t.Fatal("legacy hls rejected", version)
		}
	}
}

func TestPerfHLSPolicyActualProducerCorpus(t *testing.T) {
	raw, err := os.ReadFile("../../tests/fixtures/perf_capture_producer_v3.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []struct {
		Params   map[string]json.RawMessage `json:"params"`
		Response struct {
			Data   json.RawMessage `json:"data"`
			Status string          `json:"status"`
		} `json:"response"`
	}
	if err := json.Unmarshal(raw, &rows); err != nil {
		t.Fatal(err)
	}
	versions, captures, kinds := map[int]bool{}, map[int]bool{}, map[string]bool{}
	for i, row := range rows {
		expected := parsePerfCaptureRequest(row.Params)
		if expected == nil || !validPerfCaptureResult(row.Response.Data, row.Response.Status, expected) {
			t.Fatalf("actual v3 producer corpus rejected at %d", i)
		}
		versions[expected.Version] = true
		var value struct {
			Session *struct {
				CaptureVersion int `json:"capture_version"`
			} `json:"session"`
			Events []struct {
				Kind string `json:"kind"`
			} `json:"events"`
		}
		if err := json.Unmarshal(row.Response.Data, &value); err != nil {
			t.Fatal(err)
		}
		if value.Session != nil {
			version := value.Session.CaptureVersion
			if version == 0 {
				version = 1
			}
			captures[version] = true
		}
		for _, event := range value.Events {
			kinds[event.Kind] = true
		}
	}
	for _, version := range []int{1, 2, 3} {
		if !versions[version] || !captures[version] {
			t.Fatal("missing corpus version", version)
		}
	}
	for _, kind := range []string{"hls", "hls_fragment", "hls_buffer_policy", "hls_error_action"} {
		if !kinds[kind] {
			t.Fatal("missing corpus kind", kind)
		}
	}
}
