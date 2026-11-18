package control

import (
	"net/http"
	"time"
)

// readRequestReceipt optionally waits for the requested result. It never
// submits a command, holds the queue lock while waiting, or admits unlimited
// waiting connections. Legacy callers still receive an immediate pending reply.
func (s *Server) readRequestReceipt(w http.ResponseWriter, r *http.Request, d *device, id string, wait time.Duration) {
	deadline := time.Now().Add(wait)
	var timer *time.Timer
	for {
		s.mu.Lock()
		s.expire(s.now())
		result, found := d.results[id]
		filtered := true
		pending := false
		permitted := !found || ownResult(r, result.owner, result.permission)
		for _, e := range d.queue {
			if e.rpc && e.id == id {
				pending = true
				permitted = ownResult(r, e.owner, e.permission)
			}
		}
		remaining := time.Until(deadline)
		if permitted && pending && !found && remaining > 0 && (timer != nil || d.receiptWaiters < 8) {
			if d.resultChanged == nil {
				d.resultChanged = make(chan struct{})
			}
			changed := d.resultChanged
			if timer == nil {
				timer = time.NewTimer(remaining)
				d.receiptWaiters++
			}
			s.mu.Unlock()
			select {
			case <-changed:
				// A receipt for another request may have closed the signal.
				// Keep this reader's timer and admission slot while rechecking.
			case <-timer.C:
			case <-r.Context().Done():
			}
			if r.Context().Err() != nil {
				timer.Stop()
				s.mu.Lock()
				d.receiptWaiters--
				s.mu.Unlock()
				return
			}
			continue
		}
		if timer != nil {
			timer.Stop()
			d.receiptWaiters--
		}
		if found && permitted && result.commandActivity != nil {
			result.data, filtered = s.filterCommandActivityResponse(result.data, result.commandActivity, s.now())
		}
		s.mu.Unlock()
		if !filtered {
			failure(w, 503, "request result unavailable")
		} else if !permitted {
			failure(w, 403, "request scope is not allowed")
		} else if found {
			// Other receipts preserve validated wire bytes exactly. Scoped
			// activity receipts can only lose rows under the current grants.
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(result.data)
		} else if pending {
			reply(w, 202, map[string]string{"status": "pending"})
		} else {
			failure(w, 404, "request expired or not found")
		}
		return
	}
}
