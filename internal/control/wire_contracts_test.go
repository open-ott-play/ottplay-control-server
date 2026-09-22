package control

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestCapturedWire(t *testing.T) {
	dir := "testdata/wire-contracts"
	var inputs []struct {
		Name string `json:"name"`
		Body string `json:"body"`
	}
	b, e := os.ReadFile(dir + "/inputs.json")
	if e != nil {
		t.Fatal(e)
	}
	if e = json.Unmarshal(b, &inputs); e != nil {
		t.Fatal(e)
	}
	var rows []map[string]any
	for _, c := range inputs {
		m, err := validateCommand([]byte(c.Body))
		row := map[string]any{"name": c.Name}
		if err != nil {
			row["error"] = err.Error()
		} else {
			row["command"] = m
		}
		s := newTestServer(t)
		s.now = func() time.Time { return time.Unix(1700000000, 0) }
		w := request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, c.Body, nil)
		var body map[string]any
		if e = json.Unmarshal(w.Body.Bytes(), &body); e != nil {
			t.Fatal(e)
		}
		if id, ok := body["id"]; ok {
			if !commandID.MatchString(id.(string)) {
				t.Fatal("invalid generated id")
			}
			body["id"] = "<generated-hex32>"
		}
		row["http"] = map[string]any{"status": w.Code, "body": body}
		rows = append(rows, row)
	}
	for _, body := range []string{`{}`, `{"ids":null}`, `{"ids":[]}`, `{"ids":["` + strings.Repeat("a", 32) + `"]}`, `{"ids":["` + strings.Repeat("A", 32) + `"]}`, `{"ids":["a"]}`, `{"ids":[],"extra":1}`, `{"ids":[],"ids":[]}`, `{"ids":[]} {}`} {
		s := newTestServer(t)
		w := request(s, "POST", "/api/webhook/commands/ack", firstToken, body, nil)
		var result any
		json.Unmarshal(w.Body.Bytes(), &result)
		rows = append(rows, map[string]any{"ack": body, "status": w.Code, "body": result})
	}
	b, e = json.MarshalIndent(rows, "", "  ")
	if e != nil {
		t.Fatal(e)
	}
	b = append(b, '\n')
	p := dir + "/before-go.json"
	want, e := os.ReadFile(p)
	if e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(b, want) {
		t.Fatal("wire validator/HTTP output differs from immutable pre-migration fixtures")
	}
	t.Logf("%d actual validator/HTTP contracts", len(rows))
}
