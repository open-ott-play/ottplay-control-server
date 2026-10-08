package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestOperationHistorySurvivesReloadAndAgentRestartWithoutReplay(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"status":"ok"}`)) }))
	defer s.Close()
	a := &Agent{cfg: Config{Server: s.URL}, client: s.Client(), runtime: "old", journalPath: filepath.Join(t.TempDir(), "journal.json"), journal: map[string]Entry{}, effects: map[string]func() error{}}
	r := Request{ID: strings.Repeat("a", 32), Action: "lifecycle", Params: json.RawMessage(`{"operation":"reload_player"}`)}
	if err := a.recordOperation(r, "accepted"); err != nil {
		t.Fatal(err)
	}
	count := 0
	a.effects[r.ID] = func() error { count++; return nil }
	e := Entry{Action: r.Action, Result: ok(r.ID, map[string]any{"accepted": true}), Expires: time.Now().Add(time.Minute).Unix(), State: "prepared"}
	if err := a.remember(r.ID, e); err != nil {
		t.Fatal(err)
	}
	a.deliver(context.Background(), r, e)
	if count != 1 || a.operationHistory()[0].State != "handler_completed" {
		t.Fatal("effect/receipt missing")
	}
	// Web reload leaves the independent journal intact. New process loads the same
	// history and an existing delivery is answered without resurrecting its effect.
	b := &Agent{cfg: a.cfg, client: a.client, runtime: "new", journalPath: a.journalPath, journal: a.journal, effects: map[string]func() error{}}
	if err := b.loadOperations(); err != nil {
		t.Fatal(err)
	}
	b.deliver(context.Background(), r, b.journal[r.ID])
	if count != 1 || b.operationHistory()[0].Runtime != "old" || b.operationHistory()[0].Evidence != "handler_completed" {
		t.Fatal("replayed or misattributed")
	}
}
func TestInterruptedOperationIsUnknownAndBounded(t *testing.T) {
	a := &Agent{runtime: "old", journalPath: filepath.Join(t.TempDir(), "journal.json")}
	r := Request{ID: strings.Repeat("b", 32), Action: "lifecycle", Params: json.RawMessage(`{"operation":"reboot_device"}`)}
	if err := a.recordOperation(r, "claimed"); err != nil {
		t.Fatal(err)
	}
	a.runtime = "new"
	h := a.operationHistory()
	if h[0].State != "unknown" || h[0].Evidence != "none" {
		t.Fatal("crash inferred as success")
	}
	for i := 0; i < 70; i++ {
		r.ID = strings.Repeat("a", 30) + string("0123456789abcdef"[i/16]) + string("0123456789abcdef"[i%16])
		if err := a.recordOperation(r, "accepted"); err != nil {
			t.Fatal(err)
		}
	}
	if len(a.operations) != 64 {
		t.Fatal("unbounded history")
	}
	a.operations[0].UpdatedAt = time.Now().Add(-25 * time.Hour).UnixMilli()
	if len(a.operationHistory()) != 63 {
		t.Fatal("expired history exported")
	}
}
func TestReceiptWriteFailureNeverInvokesDeferredEffect(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"status":"ok"}`)) }))
	defer s.Close()
	a := &Agent{cfg: Config{Server: s.URL}, client: s.Client(), runtime: "old", journalPath: filepath.Join(t.TempDir(), "journal.json"), journal: map[string]Entry{}, effects: map[string]func() error{}}
	r := Request{ID: strings.Repeat("c", 32), Action: "lifecycle", Params: json.RawMessage(`{"operation":"reload_player"}`)}
	if err := a.recordOperation(r, "accepted"); err != nil {
		t.Fatal(err)
	}
	// Atomic JSON cannot replace a directory. The execution claim still persists.
	if err := os.Remove(a.operationsPath()); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(a.operationsPath(), 0700); err != nil {
		t.Fatal(err)
	}
	count := 0
	a.effects[r.ID] = func() error { count++; return nil }
	e := Entry{Action: r.Action, Result: ok(r.ID, nil), Expires: time.Now().Add(time.Minute).Unix(), State: "prepared"}
	a.deliver(context.Background(), r, e)
	a.deliver(context.Background(), r, a.journal[r.ID])
	if count != 0 {
		t.Fatal("effect ran without durable receipt")
	}
}

func TestDamagedDiagnosticHistoryDoesNotDisableMaintenanceOrEraseJournal(t *testing.T) {
	a := &Agent{runtime: "new", journalPath: filepath.Join(t.TempDir(), "journal.json"), journal: map[string]Entry{"existing": {State: "completed"}}}
	bad := []byte(`[{"request_id":"partially decoded"},`)
	if err := os.WriteFile(a.operationsPath(), bad, 0600); err != nil {
		t.Fatal(err)
	}
	a.restoreOperations()
	if !a.historyReset || len(a.operations) != 0 || a.journal["existing"].State != "completed" {
		t.Fatal("history failure damaged maintenance state")
	}
	saved, err := os.ReadFile(a.operationsPath() + ".invalid")
	if err != nil || string(saved) != string(bad) {
		t.Fatal("damaged history not preserved")
	}
	r := Request{ID: strings.Repeat("d", 32), Action: "lifecycle", Params: json.RawMessage(`{"operation":"reload_player"}`)}
	if err = a.recordOperation(r, "started"); err != nil {
		t.Fatal("new operations disabled", err)
	}
}
