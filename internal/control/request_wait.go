package control

import (
	"net/http"
	"time"
)

// readRequestReceipt optionally waits for one result notification. It never
// submits a command, holds the queue lock while waiting, or admits unlimited
// waiting connections. Legacy callers still receive an immediate pending reply.
func (s *Server) readRequestReceipt(w http.ResponseWriter, r *http.Request, d *device, id string, wait time.Duration) {
	for {
		s.mu.Lock()
		s.expire(s.now())
		result, found := d.results[id]
		pending := false
		permitted := !found || ownResult(r, result.owner, result.permission)
		for _, e := range d.queue {
			if e.rpc && e.id == id {
				pending = true
				permitted = ownResult(r, e.owner, e.permission)
			}
		}
		if permitted && pending && !found && wait > 0 && d.receiptWaiters < 8 {
			if d.resultChanged == nil {
				d.resultChanged = make(chan struct{})
			}
			changed := d.resultChanged
			d.receiptWaiters++
			s.mu.Unlock()
			timer := time.NewTimer(wait)
			select {
			case <-changed:
			case <-timer.C:
			case <-r.Context().Done():
			}
			timer.Stop()
			s.mu.Lock()
			d.receiptWaiters--
			s.mu.Unlock()
			if r.Context().Err() != nil {
				return
			}
			wait = 0
			continue
		}
		s.mu.Unlock()
		if !permitted {
			failure(w, 403, "request scope is not allowed")
		} else if found {
			// Preserve the already validated, bounded wire bytes exactly.
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
