package control

import (
	"net/http"
	"runtime"
	"time"
)

func debugCount(value uint64) uint64 {
	if value > inspectMaxInteger {
		return inspectMaxInteger
	}
	return value
}

// Each section is sampled independently; there is no cross-subsystem atomic
// snapshot. Reading does not expire entries or otherwise change queue state.
func (s *Server) debug(w http.ResponseWriter, r *http.Request, now time.Time) {
	if _, ok := query(r); !ok {
		failure(w, 400, "unsupported query parameters")
		return
	}
	var memory runtime.MemStats
	runtime.ReadMemStats(&memory)
	uptime := max(int64(0), time.Since(s.started).Milliseconds())
	process := map[string]any{
		"uptimeMs": debugCount(uint64(uptime)), "goroutines": runtime.NumGoroutine(),
		"heapAllocBytes": debugCount(memory.HeapAlloc), "heapSysBytes": debugCount(memory.HeapSys),
	}
	s.mu.Lock()
	pending, queues, results := 0, 0, 0
	for _, device := range s.devices {
		pending += len(device.queue)
		results += len(device.results)
		if len(device.queue) > 0 {
			queues++
		}
	}
	control := map[string]any{
		"devices": len(s.devices), "queues": queues, "pending": pending,
		"queueBytes": s.bytes, "resultEntries": results, "resultBytes": s.resultBytes,
		"commandTtlMs": s.ttl.Milliseconds(), "maxPendingPerDevice": s.maxPending,
	}
	s.mu.Unlock()
	d := s.diagnostics
	d.mu.Lock()
	diagnostics := map[string]any{
		"configured": d.configured, "runtimes": len(d.runtimes), "sessions": len(d.sessions),
		"repairs": len(d.repairs), "eventBytes": d.eventBytes, "reservedStops": d.reservedStops,
		"controlSlotsUsed": len(d.controlSlots), "controlSlotsCapacity": cap(d.controlSlots),
		"eventSlotsUsed": len(d.eventSlots), "eventSlotsCapacity": cap(d.eventSlots),
	}
	d.mu.Unlock()
	reply(w, 200, map[string]any{
		"version": 1, "sampledAt": debugCount(uint64(max(int64(0), now.UnixMilli()))),
		"consistent": false, "process": process, "control": control, "diagnostics": diagnostics,
	})
}
