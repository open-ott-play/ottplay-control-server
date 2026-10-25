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
	ID         string                       `json:"id"`
	Runtime    string                       `json:"runtime"`
	Title      string                       `json:"title"`
	Message    string                       `json:"message"`
	Severity   string                       `json:"severity"`
	DurationMS int                          `json:"duration_ms"`
	ExpiresAt  float64                      `json:"expires_at"`
	Media      *notificationMediaDescriptor `json:"media,omitempty"`
}

type notificationPresence struct {
	runtime   string
	active    bool
	enabled   bool
	mediaJPEG bool
	seen      time.Time
	queue     []notification
}

type notificationCounters struct {
	Queued  uint64 `json:"queued"`
	Polled  uint64 `json:"polled"`
	Skipped uint64 `json:"skipped"`
	Dropped uint64 `json:"dropped"`
}

func notificationTime(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

func (p *notificationPresence) fresh(now time.Time) bool {
	return p.active && p.enabled && !p.seen.IsZero() && !now.Before(p.seen) && now.Sub(p.seen) < notificationPresenceTTL
}

func (s *Server) expireNotifications(d *device, now time.Time) {
	p := &d.notifications
	if !p.fresh(now) {
		s.notificationMedia.revokeDevice(d.id)
	}
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

// Recipient snapshots precede camera acquisition. Delivery rechecks the runtime
// and registry generation so a restarted or re-registered player never gains a
// frame acquired for its past session. These receipts stay inside the server.
func (s *Server) NotificationTargets(n homeassistant.Notification) map[string]string {
	return s.notificationTargets(n, false)
}

func (s *Server) NotificationMediaTargets(n homeassistant.Notification) map[string]string {
	return s.notificationTargets(n, true)
}

func (s *Server) notificationTargets(n homeassistant.Notification, media bool) map[string]string {
	result := make(map[string]string)
	targets := make(map[string]bool, len(n.Targets))
	for _, id := range n.Targets {
		targets[id] = true
	}
	now := s.now()
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, d := range s.currentDevices() {
		if len(n.Targets) > 0 && !targets[d.id] {
			continue
		}
		s.expireNotifications(d, now)
		p := &d.notifications
		if p.fresh(now) && len(p.queue) < notificationQueueLimit && (!media || p.mediaJPEG) {
			result[d.id] = s.notificationRecipient(d)
		}
	}
	return result
}

// The queue lock is held, so the device list and generation cannot change while
// this opaque recipient receipt is constructed or compared.
func (s *Server) notificationRecipient(d *device) string {
	if snapshot := s.registryView.Load(); snapshot != nil {
		return snapshot.identities[d.id].Generation + ":" + d.notifications.runtime
	}
	return d.notifications.runtime
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
	expiry := now.Add(time.Duration(n.TTLSeconds) * time.Second)
	if !n.ExpiresAt.IsZero() && n.ExpiresAt.Before(expiry) {
		expiry = n.ExpiresAt
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, d := range s.currentDevices() {
		if targeted && !targets[d.id] {
			continue
		}
		delete(targets, d.id)
		s.expireNotifications(d, now)
		p := &d.notifications
		recipient := s.notificationRecipient(d)
		if !p.fresh(now) || !expiry.After(now) || len(p.queue) >= notificationQueueLimit ||
			(n.TargetRuntimes != nil && n.TargetRuntimes[d.id] != recipient) {
			result.Skipped++
			s.notificationStats.Skipped++
			continue
		}
		var media *notificationMediaDescriptor
		if n.Media != nil && p.mediaJPEG && n.MediaTargetRuntimes[d.id] == recipient {
			media = s.grantNotificationMedia(id, d.id, p.runtime, n.Media, expiry, now)
		}
		p.queue = append(p.queue, notification{ID: id, Runtime: p.runtime, Title: n.Title,
			Message: n.Message, Severity: n.Severity, DurationMS: n.DurationMS,
			ExpiresAt: notificationTime(expiry), Media: media})
		result.Queued++
		s.notificationStats.Queued++
	}
	result.Skipped += len(targets)
	s.notificationStats.Skipped += uint64(len(targets))
	return result
}

func (s *Server) pollNotifications(w http.ResponseWriter, r *http.Request, d *device, now time.Time) {
	q, ok := query(r, "device_id", "runtime", "active", "enabled", "media")
	if !ok || !notificationRuntime.MatchString(q.Get("runtime")) || (q.Get("active") != "true" && q.Get("active") != "false") {
		failure(w, 400, "valid runtime and active=true|false are required")
		return
	}
	if (q.Has("enabled") && q.Get("enabled") != "true" && q.Get("enabled") != "false") ||
		(q.Has("media") && q.Get("media") != "jpeg-v1") {
		failure(w, 400, "invalid notification capabilities")
		return
	}
	if q.Has("device_id") && q.Get("device_id") != d.id {
		failure(w, 403, "device_id does not match credentials")
		return
	}
	s.mu.Lock()
	p := &d.notifications
	enabled := !q.Has("enabled") || q.Get("enabled") == "true"
	mediaJPEG := q.Get("media") == "jpeg-v1"
	// Expire before refreshing presence: reconnect must not revive a stale queue.
	s.expireNotifications(d, now)
	if p.runtime != q.Get("runtime") || q.Get("active") == "false" || !enabled {
		s.notificationStats.Dropped += uint64(len(p.queue))
		clear(p.queue)
		p.queue = nil
		s.notificationMedia.revokeDevice(d.id)
	}
	if p.mediaJPEG && !mediaJPEG {
		s.notificationMedia.revokeDevice(d.id)
		for i := range p.queue {
			p.queue[i].Media = nil
		}
	}
	p.runtime, p.active, p.enabled, p.mediaJPEG, p.seen = q.Get("runtime"), q.Get("active") == "true" && enabled, enabled, mediaJPEG, now
	items := append([]notification{}, p.queue...)
	s.notificationStats.Polled += uint64(len(items))
	clear(p.queue)
	p.queue = nil
	s.mu.Unlock()
	reply(w, 200, map[string]any{"version": 1, "server_time": notificationTime(now), "poll_after_ms": 5000,
		"notifications": items, "notifications_enabled": enabled,
		"capabilities": map[string]any{"enabled": true, "media": []string{"jpeg-v1"}}})
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
	players := make([]map[string]any, 0, len(s.currentDevices()))
	for _, d := range s.currentDevices() {
		if operator != nil && !operator.devices[d.id] {
			continue
		}
		s.expireNotifications(d, now)
		row := map[string]any{"device_id": d.id, "active": d.notifications.fresh(now), "pending": len(d.notifications.queue)}
		if !d.notifications.seen.IsZero() {
			row["last_poll_at"] = d.notifications.seen.UTC().Format(time.RFC3339)
			row["notifications_enabled"] = d.notifications.enabled
			row["media_jpeg"] = d.notifications.mediaJPEG
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
