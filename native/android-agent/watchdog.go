package main

import (
	"context"
	"time"
)

// Clock progress cannot prove that a hardware video surface contains new frames.
// A manual recover command also handles that distinct legacy decoder failure.
type Watchdog struct {
	LastCheck, Progress, LastAction, HealthySince time.Time
	Source                                        string
	Position                                      float64
	Enabled                                       bool
	Attempts                                      int
}

func (w *Watchdog) observe(now time.Time, p map[string]any, responsive, suspended bool) string {
	if suspended {
		w.Progress = now
		return ""
	}
	if responsive {
		k := object(p["kiosk"])
		v := object(p["video"])
		w.Enabled = k["enabled"] == true && k["state"] == "locked"
		if !w.Enabled {
			w.Progress = now
			w.Attempts = 0
			return ""
		}
		source, _ := v["source"].(string)
		position, _ := v["position"].(float64)
		advanced := position > w.Position && source == w.Source
		if source != w.Source && position > 0 {
			advanced = true
		}
		w.Source, w.Position = source, position
		if advanced {
			if now.Sub(w.Progress) > 30*time.Second {
				w.HealthySince = time.Time{}
			}
			if w.HealthySince.IsZero() {
				w.HealthySince = now
			}
			if now.Sub(w.HealthySince) > 5*time.Minute {
				w.Attempts = 0
			}
			w.Progress = now
			return ""
		}
	}
	if !w.Enabled || now.Sub(w.Progress) < 120*time.Second {
		return ""
	}
	// Back off between escalations; at most three automatic interventions per
	// stalled episode. A subsequent healthy playback period re-arms recovery.
	if now.Sub(w.LastAction) < time.Duration(60*(1<<uint(w.Attempts)))*time.Second || w.Attempts >= 3 {
		return ""
	}
	w.Attempts++
	w.LastAction = now
	w.Progress = now
	if responsive && w.Attempts == 1 {
		return "recover"
	}
	return "restart_app"
}
func (a *Agent) watch(ctx context.Context) {
	now := time.Now()
	if now.Sub(a.watchdog.LastCheck) < 15*time.Second {
		return
	}
	a.watchdog.LastCheck = now
	child, cancel := context.WithTimeout(ctx, 7*time.Second)
	defer cancel()
	p, e := a.player(child, "health", nil)
	action := a.watchdog.observe(now, p, e == nil, a.suspended)
	if action == "" {
		return
	}
	a.event("watchdog_" + action)
	if action == "recover" {
		_, _ = a.player(child, "recover", nil)
	} else {
		_ = a.lifecycle(action)
	}
}
