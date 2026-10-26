package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
)

func perfHLSFixture(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/perf-capture-v2.json")
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	return value
}

func perfHLSReply(t *testing.T, op string, captureVersion int, kinds ...string) map[string]any {
	t.Helper()
	v := perfReply(t, op)
	v["version"] = 2
	s := v["session"].(map[string]any)
	s["capture_version"] = captureVersion
	if op != "read" {
		return v
	}
	f := perfHLSFixture(t)
	rows := []any{}
	for i, kind := range kinds {
		e := perfExample(t, f, f["$defs"].(map[string]any)[kind+"_event"].(map[string]any)).(map[string]any)
		e["seq"], e["at_ms"], e["lane"], e["source_id"] = i+1, i, "main", "s1"
		rows = append(rows, e)
	}
	s["first_seq"], s["next_seq"], s["event_count"], s["bytes"] = 1, len(rows)+1, len(rows), len(rows)*256
	v["events"], v["next_after_seq"] = rows, len(rows)
	return v
}

func TestPerfHLSV1CanaryAndV2Capability(t *testing.T) {
	raw, err := os.ReadFile("../../contracts/perf-capture-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	if hex.EncodeToString(sum[:]) != "e80ee9749d2b8594d3e008c80a873dcdc2201abfadbafd19a376e5a3467838d0" {
		t.Fatal("legacy v1 contract changed")
	}
	f := perfHLSFixture(t)
	cap := f["capability"].(map[string]any)
	b, _ := json.Marshal(cap["perf_capture_hls"])
	if !validPerfHLSCapability(b) {
		t.Fatal("v2 marker rejected")
	}
	for _, bad := range []string{`null`, `[]`, `{"version":true}`, `{"version":1}`, `{"version":2,"extra":0}`} {
		if validPerfHLSCapability([]byte(bad)) {
			t.Fatal("invalid marker admitted", bad)
		}
	}
	for _, bad := range []map[string]any{
		{"perf_capture_hls": cap["perf_capture_hls"]},
		{"perf_capture": cap["perf_capture"], "perf_capture_hls": map[string]any{"version": true}},
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", bad), nil), 400)
	}
}

func TestPerfHLSV2RoundtripAllOperations(t *testing.T) {
	for _, op := range []string{"start", "status", "read", "stop"} {
		t.Run(op, func(t *testing.T) {
			s := newTestServer(t)
			p := perfParams(op)
			p["version"] = 2
			params, _ := json.Marshal(p)
			id := rpcID(t, s, `{"action":"perf_capture","params":`+string(params)+`}`)
			body := perfHLSReply(t, op, 2, "hls_buffer", "hls_fragment", "hls_config", "hls_live")
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", body), nil), 200)
			}
		})
	}
}

func TestPerfHLSReaderAndVersionFences(t *testing.T) {
	for _, op := range []string{"status", "read", "stop"} {
		p := perfParams(op)
		p["version"] = 2
		body := perfHLSReply(t, op, 1, "sample")
		raw, _ := json.Marshal(body)
		if !validPerfCaptureResult(raw, "ok", perfExpected(t, p)) {
			t.Fatal("v2 reader must preserve v1 capture", op)
		}
		if validPerfCaptureResult(raw, "ok", perfExpected(t, perfParams(op))) {
			t.Fatal("v2 result admitted for v1 request", op)
		}
	}
	p := perfParams("start")
	p["version"] = 2
	wrong, _ := json.Marshal(perfHLSReply(t, "start", 1))
	if validPerfCaptureResult(wrong, "ok", perfExpected(t, p)) {
		t.Fatal("v2 start cannot silently downgrade")
	}
	for _, version := range []int{1, 2} {
		p["version"] = version
		negative := map[string]any{"version": version, "runtime": "page-one", "op": "start", "error": "unsupported"}
		raw, _ := json.Marshal(negative)
		if !validPerfCaptureResult(raw, "unsupported", perfExpected(t, p)) {
			t.Fatal("bound negative rejected")
		}
		negative["version"] = 3 - version
		raw, _ = json.Marshal(negative)
		if validPerfCaptureResult(raw, "unsupported", perfExpected(t, p)) {
			t.Fatal("negative crossed protocol fence")
		}
	}
}

func TestPerfHLSDetailsRejectPrivateInvalidAndV1Events(t *testing.T) {
	mutations := map[string]func(map[string]any){
		"capture v1":     func(v map[string]any) { v["session"].(map[string]any)["capture_version"] = 1 },
		"capture bool":   func(v map[string]any) { v["session"].(map[string]any)["capture_version"] = true },
		"lane missing":   func(v map[string]any) { v["events"].([]any)[0].(map[string]any)["lane"] = nil },
		"source missing": func(v map[string]any) { v["events"].([]any)[0].(map[string]any)["source_id"] = nil },
		"private field": func(v map[string]any) {
			v["events"].([]any)[0].(map[string]any)["data"].(map[string]any)["url"] = "private"
		},
		"wrong track": func(v map[string]any) {
			v["events"].([]any)[0].(map[string]any)["data"].(map[string]any)["track"] = "private-name"
		},
		"reversed interval": func(v map[string]any) {
			d := v["events"].([]any)[1].(map[string]any)["data"].(map[string]any)
			d["start_seconds"], d["end_seconds"] = 20, 10
		},
		"duplicate tracks": func(v map[string]any) {
			d := v["events"].([]any)[1].(map[string]any)["data"].(map[string]any)
			r := map[string]any{"track": "audio", "start_pts_seconds": 0, "end_pts_seconds": 10}
			d["elementary_streams"] = []any{r, r}
		},
		"reversed track PTS": func(v map[string]any) {
			d := v["events"].([]any)[1].(map[string]any)["data"].(map[string]any)
			d["elementary_streams"] = []any{map[string]any{"track": "audio", "start_pts_seconds": 20, "end_pts_seconds": 10}}
		},
		"fractional bytes": func(v map[string]any) {
			v["events"].([]any)[2].(map[string]any)["data"].(map[string]any)["max_buffer_size_bytes"] = 2.5
		},
		"private latency": func(v map[string]any) {
			v["events"].([]any)[3].(map[string]any)["data"].(map[string]any)["latency_seconds"] = "private"
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			p := perfParams("read")
			p["version"] = 2
			params, _ := json.Marshal(p)
			id := rpcID(t, s, `{"action":"perf_capture","params":`+string(params)+`}`)
			body := perfHLSReply(t, "read", 2, "hls_buffer", "hls_fragment", "hls_config", "hls_live")
			mutate(body)
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", body), nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("invalid reply consumed queued request")
			}
		})
	}
}

func TestPerfHLSBuffersKeepGapsAndBounds(t *testing.T) {
	body := perfHLSReply(t, "read", 2, "hls_buffer")
	d := body["events"].([]any)[0].(map[string]any)["data"].(map[string]any)
	d["position_seconds"] = 10.1
	d["buffered"] = map[string]any{"range_count": 2, "truncated_count": 0, "ranges": []any{
		map[string]any{"index": 0, "start_seconds": 0, "end_seconds": 10},
		map[string]any{"index": 1, "start_seconds": 10.2, "end_seconds": 20},
	}}
	p := perfParams("read")
	p["version"] = 2
	raw, _ := json.Marshal(body)
	if !validPerfCaptureResult(raw, "ok", perfExpected(t, p)) {
		t.Fatal("a gap containing the playhead must survive validation")
	}
	d["buffered"].(map[string]any)["truncated_count"] = 1
	raw, _ = json.Marshal(body)
	if validPerfCaptureResult(raw, "ok", perfExpected(t, p)) {
		t.Fatal("inconsistent range truncation admitted")
	}
}

func TestPerfHLSLivePreservesOvershootAndAdvertisedBitrates(t *testing.T) {
	body := perfHLSReply(t, "read", 2, "hls_live")
	d := body["events"].([]any)[0].(map[string]any)["data"].(map[string]any)
	d["latency_seconds"], d["effective_buffer_length_seconds"] = -0.75, 2.4
	d["current_level"] = map[string]any{"index": 0, "bitrate": 200000000, "max_bitrate": 210000000}
	d["load_level"] = map[string]any{"index": -1, "bitrate": nil, "max_bitrate": nil}
	p := perfParams("read")
	p["version"] = 2
	raw, _ := json.Marshal(body)
	if !validPerfCaptureResult(raw, "ok", perfExpected(t, p)) {
		t.Fatal("actual signed latency and observed level metadata rejected")
	}
	d["load_level"].(map[string]any)["bitrate"] = 5000000
	raw, _ = json.Marshal(body)
	if validPerfCaptureResult(raw, "ok", perfExpected(t, p)) {
		t.Fatal("unselected level cannot report a bitrate")
	}
}

func TestPerfHLSActualPlayerProducerCorpus(t *testing.T) {
	raw, err := os.ReadFile("../../tests/fixtures/perf_capture_producer_v2.json")
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
	operations, kinds := map[string]bool{}, map[string]bool{}
	for _, row := range rows {
		expected := parsePerfCaptureRequest(row.Params)
		if !validPerfCaptureResult(row.Response.Data, row.Response.Status, expected) {
			t.Fatalf("actual v2 player corpus rejected for %s", row.Params["op"])
		}
		operations[expected.Op] = true
		var value struct {
			Events []struct {
				Kind string `json:"kind"`
			} `json:"events"`
		}
		if err := json.Unmarshal(row.Response.Data, &value); err != nil {
			t.Fatal(err)
		}
		for _, event := range value.Events {
			kinds[event.Kind] = true
		}
	}
	for _, op := range []string{"start", "read", "stop"} {
		if !operations[op] {
			t.Fatal("missing producer operation", op)
		}
	}
	for _, kind := range []string{"hls_buffer", "hls_fragment", "hls_config", "hls_live"} {
		if !kinds[kind] {
			t.Fatal("missing producer event", kind)
		}
	}
}
