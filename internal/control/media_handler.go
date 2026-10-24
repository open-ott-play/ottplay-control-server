package control

import (
	"bytes"
	"encoding/base64"
	"image/jpeg"
	"net/http"
	"regexp"
	"sync"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/homeassistant"
)

const notificationMediaMaxBytes = 4 * 1024 * 1024
const notificationMediaMaxEntries = 32

var notificationMediaID = regexp.MustCompile(`^[a-f0-9]{32}$`)

type notificationMediaDescriptor struct {
	ID          string `json:"id"`
	Kind        string `json:"kind"`
	ContentType string `json:"content_type"`
	Width       int    `json:"width"`
	Height      int    `json:"height"`
	Bytes       int    `json:"bytes"`
}

type notificationMediaEntry struct {
	frame   homeassistant.CameraFrame
	grants  map[string]string
	expires time.Time
	timer   *time.Timer
}

// The store owns immutable bytes, never a file, journal, or public URL. Lock
// order is Server.mu -> store.mu; expiry timers take only store.mu.
type notificationMediaStore struct {
	mu      sync.Mutex
	entries map[string]*notificationMediaEntry
	bytes   int
}

func (m *notificationMediaStore) removeLocked(id string) {
	if item := m.entries[id]; item != nil {
		item.timer.Stop()
		m.bytes -= len(item.frame.JPEG)
		delete(m.entries, id)
	}
}

func (m *notificationMediaStore) expireLocked(now time.Time) {
	for id, item := range m.entries {
		if !now.Before(item.expires) {
			m.removeLocked(id)
		}
	}
}

func (m *notificationMediaStore) revokeDevice(deviceID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for id, item := range m.entries {
		delete(item.grants, deviceID)
		if len(item.grants) == 0 {
			m.removeLocked(id)
		}
	}
}

func (s *Server) grantNotificationMedia(id, deviceID, runtime string, frame *homeassistant.CameraFrame, expires, now time.Time) *notificationMediaDescriptor {
	if frame == nil || !notificationMediaID.MatchString(id) || !notificationRuntime.MatchString(runtime) || len(frame.JPEG) == 0 || len(frame.JPEG) > homeassistant.CameraJPEGMaxBytes || frame.Width < 1 || frame.Width > 640 || frame.Height < 1 || frame.Height > 360 || !now.Before(expires) || expires.Sub(now) > 60*time.Second {
		return nil
	}
	info, err := jpeg.DecodeConfig(bytes.NewReader(frame.JPEG))
	if err != nil || info.Width != frame.Width || info.Height != frame.Height {
		return nil
	}
	m := &s.notificationMedia
	m.mu.Lock()
	defer m.mu.Unlock()
	m.expireLocked(now)
	if m.entries == nil {
		m.entries = map[string]*notificationMediaEntry{}
	}
	item := m.entries[id]
	if item == nil {
		if len(m.entries) >= notificationMediaMaxEntries || m.bytes+len(frame.JPEG) > notificationMediaMaxBytes {
			return nil
		}
		item = &notificationMediaEntry{frame: homeassistant.CameraFrame{JPEG: append([]byte(nil), frame.JPEG...), Width: frame.Width, Height: frame.Height}, grants: map[string]string{}, expires: expires}
		m.entries[id] = item
		m.bytes += len(frame.JPEG)
		captured := item
		item.timer = time.AfterFunc(expires.Sub(now), func() {
			m.mu.Lock()
			defer m.mu.Unlock()
			if m.entries[id] == captured {
				m.removeLocked(id)
			}
		})
	} else if !item.expires.Equal(expires) || item.frame.Width != frame.Width || item.frame.Height != frame.Height || !bytes.Equal(item.frame.JPEG, frame.JPEG) {
		return nil
	}
	item.grants[deviceID] = runtime
	return &notificationMediaDescriptor{ID: id, Kind: "image", ContentType: "image/jpeg", Width: frame.Width, Height: frame.Height, Bytes: len(frame.JPEG)}
}

func (m *notificationMediaStore) get(id, deviceID, runtime string, now time.Time) (homeassistant.CameraFrame, time.Time, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.expireLocked(now)
	item := m.entries[id]
	if item == nil || item.grants[deviceID] != runtime {
		return homeassistant.CameraFrame{}, time.Time{}, false
	}
	// Bytes remain immutable even if expiry drops the store's reference.
	return item.frame, item.expires, true
}

func (s *Server) serveNotificationMedia(w http.ResponseWriter, r *http.Request, d *device, now time.Time) {
	q, ok := query(r, "id", "runtime", "device_id")
	if !ok || !notificationMediaID.MatchString(q.Get("id")) || !notificationRuntime.MatchString(q.Get("runtime")) {
		failure(w, 400, "valid media id and runtime are required")
		return
	}
	if d == nil || q.Has("device_id") && q.Get("device_id") != d.id {
		failure(w, 403, "device credentials are required")
		return
	}
	s.mu.Lock()
	p := &d.notifications
	if !p.fresh(now) || !p.enabled || !p.mediaJPEG || p.runtime != q.Get("runtime") {
		s.mu.Unlock()
		failure(w, 404, "media unavailable")
		return
	}
	frame, expires, found := s.notificationMedia.get(q.Get("id"), d.id, p.runtime, now)
	s.mu.Unlock()
	if !found {
		failure(w, 404, "media unavailable")
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	reply(w, 200, map[string]any{"version": 1, "id": q.Get("id"), "runtime": q.Get("runtime"), "content_type": "image/jpeg", "data_base64": base64.StdEncoding.EncodeToString(frame.JPEG), "width": frame.Width, "height": frame.Height, "bytes": len(frame.JPEG), "server_time": notificationTime(now), "expires_at": notificationTime(expires)})
}
