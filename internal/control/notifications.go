package control

import (
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"regexp"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/open-ott-play/ottplay-control-server/internal/homeassistant"
)

const notificationPresenceTTL = 15 * time.Second
const notificationQueueLimit = 4

var notificationRuntime = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,128}$`)

type notification struct {
	ID         string  `json:"id"`
	Runtime    string  `json:"runtime"`
	Title      string  `json:"title"`
	Message    string  `json:"message"`
	Severity   string  `json:"severity"`
	DurationMS int     `json:"duration_ms"`
	ExpiresAt  float64 `json:"expires_at"`
}

type notificationPresence struct {
	runtime string
	active  bool
	seen    time.Time
	queue   []notification
}

type notificationCounters struct {
	Queued  uint64 `json:"queued"`
	Polled  uint64 `json:"polled"`
	Skipped uint64 `json:"skipped"`
	Dropped uint64 `json:"dropped"`
}

func notificationTime(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

func (p *notificationPresence) fresh(now time.Time) bool {
	return p.active && !p.seen.IsZero() && !now.Before(p.seen) && now.Sub(p.seen) < notificationPresenceTTL
}

func (s *Server) expireNotifications(d *device, now time.Time) {
	p := &d.notifications
	kept := p.queue[:0]
	for _, n := range p.queue {
		if p.fresh(now) && n.Runtime == p.runtime && n.ExpiresAt > notificationTime(now) {
			kept = append(kept, n)
		} else {
			s.notificationStats.Dropped++
		}
	}
	clear(p.queue[len(kept):])
	p.queue = kept
}

// SetHomeAssistantStatus installs a credential-free snapshot callback. It must
// not return configuration, event payloads, URLs or token-file paths.
func (s *Server) SetHomeAssistantStatus(snapshot func() homeassistant.Snapshot) {
	s.mu.Lock()
	s.homeAssistantStatus = snapshot
	s.mu.Unlock()
}

func boundedNotificationText(value string, limit int) bool {
	if strings.TrimSpace(value) == "" || !utf8.ValidString(value) {
		return false
	}
	units := 0
	for _, r := range value {
		if unicode.IsControl(r) && r != '\n' {
			return false
		}
		units++
		if r > 0xffff {
			units++
		}
	}
	return units <= limit
}

// DeliverNotification queues bounded, ephemeral banners only for runtimes that
// recently reported themselves active. This never enters the command journal,
// wakes playback, or claims that a player actually displayed the notification.
func (s *Server) DeliverNotification(n homeassistant.Notification) homeassistant.DeliveryResult {
	result := homeassistant.DeliveryResult{}
	if !boundedNotificationText(n.Title, 128) || !boundedNotificationText(n.Message, 512) ||
		(n.Severity != "info" && n.Severity != "warning" && n.Severity != "critical") ||
		n.DurationMS < 1000 || n.DurationMS > 15000 || n.TTLSeconds < 1 || n.TTLSeconds > 60 {
		return result
	}
	var random [16]byte
	if _, err := rand.Read(random[:]); err != nil {
		return result
	}
	id := hex.EncodeToString(random[:])
	targets := make(map[string]bool, len(n.Targets))
	targeted := len(n.Targets) > 0
	for _, id := range n.Targets {
		targets[id] = true
	}
	now := s.now()
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, d := range s.devices {
		if targeted && !targets[d.id] {
			continue
		}
		delete(targets, d.id)
		s.expireNotifications(d, now)
		p := &d.notifications
		if !p.fresh(now) || len(p.queue) >= notificationQueueLimit {
			result.Skipped++
			s.notificationStats.Skipped++
			continue
		}
		p.queue = append(p.queue, notification{ID: id, Runtime: p.runtime, Title: n.Title,
			Message: n.Message, Severity: n.Severity, DurationMS: n.DurationMS,
			ExpiresAt: notificationTime(now.Add(time.Duration(n.TTLSeconds) * time.Second))})
		result.Queued++
		s.notificationStats.Queued++
	}
	result.Skipped += len(targets)
	s.notificationStats.Skipped += uint64(len(targets))
	return result
}

func (s *Server) pollNotifications(w http.ResponseWriter, r *http.Request, d *device, now time.Time) {
	q, ok := query(r, "device_id", "runtime", "active")
	if !ok || !notificationRuntime.MatchString(q.Get("runtime")) || (q.Get("active") != "true" && q.Get("active") != "false") {
		failure(w, 400, "valid runtime and active=true|false are required")
		return
	}
	if q.Has("device_id") && q.Get("device_id") != d.id {
		failure(w, 403, "device_id does not match credentials")
		return
	}
	s.mu.Lock()
	p := &d.notifications
	// Expire before refreshing presence: reconnect must not revive a stale queue.
	s.expireNotifications(d, now)
	if p.runtime != q.Get("runtime") || q.Get("active") == "false" {
		s.notificationStats.Dropped += uint64(len(p.queue))
		clear(p.queue)
		p.queue = nil
	}
	p.runtime, p.active, p.seen = q.Get("runtime"), q.Get("active") == "true", now
	items := append([]notification{}, p.queue...)
	s.notificationStats.Polled += uint64(len(items))
	clear(p.queue)
	p.queue = nil
	s.mu.Unlock()
	reply(w, 200, map[string]any{"version": 1, "server_time": notificationTime(now), "poll_after_ms": 5000, "notifications": items})
}

func (s *Server) notificationStatus(w http.ResponseWriter, r *http.Request, now time.Time) {
	operator := operatorFor(r)
	if operator != nil && !operator.actions["status"] {
		failure(w, 403, "status scope is required")
		return
	}
	if _, ok := query(r); !ok {
		failure(w, 400, "unsupported query parameters")
		return
	}
	s.mu.Lock()
	players := make([]map[string]any, 0, len(s.devices))
	for _, d := range s.devices {
		if operator != nil && !operator.devices[d.id] {
			continue
		}
		s.expireNotifications(d, now)
		row := map[string]any{"device_id": d.id, "active": d.notifications.fresh(now), "pending": len(d.notifications.queue)}
		if !d.notifications.seen.IsZero() {
			row["last_poll_at"] = d.notifications.seen.UTC().Format(time.RFC3339)
		}
		players = append(players, row)
	}
	snapshot, counters := s.homeAssistantStatus, s.notificationStats
	s.mu.Unlock()
	// Never call the bridge while holding the queue lock: delivery takes it too.
	bridge := homeassistant.Snapshot{Enabled: false, State: "disabled"}
	if snapshot != nil {
		bridge = snapshot()
	}
	result := map[string]any{"version": 1, "bridge": bridge, "players": players}
	if operator == nil {
		result["delivery"] = counters
	} else {
		result["bridge"] = map[string]any{"enabled": bridge.Enabled, "state": bridge.State, "last_error_code": bridge.LastErrorCode}
	}
	reply(w, 200, result)
}
