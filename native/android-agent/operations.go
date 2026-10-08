package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"time"
)

// Receipts contain no request payload, settings, stream URL, or response body.
// The execution journal remains the only duplicate-delivery guard. This longer
// history provides diagnostics after a page reload or an agent restart.
type OperationReceipt struct {
	RequestID string `json:"request_id"`
	Action    string `json:"action"`
	Operation string `json:"operation"`
	State     string `json:"state"`
	Runtime   string `json:"runtime"`
	BootID    string `json:"boot_id"`
	UpdatedAt int64  `json:"updated_at"`
	Evidence  string `json:"evidence"`
}

func mutationOperation(r Request) string {
	p, e := params(r.Params)
	if e != nil {
		return ""
	}
	op := stringParam(p, "operation")
	allowed := map[string]map[string]bool{
		"maintenance":   {"recover_video": true, "update": true},
		"lifecycle":     {"restart_app": true, "reload_player": true, "reboot_device": true, "wake": true, "standby": true},
		"playback":      {"pause": true, "resume": true, "seek": true},
		"vportal_queue": {"play": true, "next": true, "previous": true, "restart": true, "stop": true},
	}
	if allowed[r.Action][op] {
		return op
	}
	return ""
}
func (a *Agent) operationsPath() string {
	return filepath.Join(filepath.Dir(a.journalPath), "operations.json")
}
func (a *Agent) loadOperations() error {
	b, e := os.ReadFile(a.operationsPath())
	if os.IsNotExist(e) {
		return nil
	}
	if e != nil {
		return e
	}
	if len(b) > 128*1024 || json.Unmarshal(b, &a.operations) != nil || len(a.operations) > 64 {
		return errors.New("invalid operation history")
	}
	return nil
}
func (a *Agent) restoreOperations() {
	if a.loadOperations() == nil {
		return
	}
	// Diagnostic metadata is not the execution journal. Its failure must not
	// disable independent maintenance or erase the duplicate-delivery guard.
	_ = os.Rename(a.operationsPath(), a.operationsPath()+".invalid")
	a.operations = nil
	a.historyReset = true
	a.event("operation_history_reset")
}
func (a *Agent) recordOperation(r Request, state string) error {
	op := mutationOperation(r)
	// Deferred delivery retries retain action/operation in the existing receipt.
	var row OperationReceipt
	for _, old := range a.operations {
		if old.RequestID == r.ID {
			row = old
			break
		}
	}
	if op == "" && row.RequestID == "" {
		return nil
	}
	if row.RequestID == "" {
		row = OperationReceipt{RequestID: r.ID, Action: r.Action, Operation: op, Runtime: a.runtime, BootID: a.bootID}
	}
	row.State = state
	row.UpdatedAt = time.Now().UnixMilli()
	row.Evidence = "none"
	if state == "handler_completed" {
		row.Evidence = "handler_completed"
	}
	next := make([]OperationReceipt, 0, 64)
	for _, old := range a.operations {
		if old.RequestID != r.ID && old.UpdatedAt > row.UpdatedAt-24*60*60*1000 {
			next = append(next, old)
		}
	}
	if len(next) >= 64 {
		next = next[len(next)-63:]
	}
	next = append(next, row)
	if e := atomicJSON(a.operationsPath(), next); e != nil {
		return e
	}
	a.operations = next
	return nil
}
func (a *Agent) operationHistory() []OperationReceipt {
	result := make([]OperationReceipt, 0, len(a.operations))
	now := time.Now().UnixMilli()
	for _, row := range a.operations {
		if row.UpdatedAt > now || now-row.UpdatedAt > 24*60*60*1000 {
			continue
		}
		// An interrupted receipt never becomes a successful effect or replay plan.
		if row.State == "started" || row.State == "claimed" || row.State == "accepted" && row.Runtime != a.runtime {
			row.State = "unknown"
		}
		result = append(result, row)
	}
	return result
}
