package control

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	"image/jpeg"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/homeassistant"
)

func mediaTestFrame(t *testing.T) *homeassistant.CameraFrame {
	t.Helper()
	var b bytes.Buffer
	if err := jpeg.Encode(&b, image.NewRGBA(image.Rect(0, 0, 16, 9)), nil); err != nil {
		t.Fatal(err)
	}
	return &homeassistant.CameraFrame{JPEG: b.Bytes(), Width: 16, Height: 9}
}
func mediaPresence(t *testing.T, s *Server, token, runtime string) {
	t.Helper()
	expect(t, request(s, "GET", "/api/notifications?runtime="+runtime+"&active=true&enabled=true&media=jpeg-v1", token, "", nil), 200)
}

func TestMediaRequiresAdmittedDeviceRuntimeAndUnexpiredEnabledPresence(t *testing.T) {
	s := newTestServer(t)
	now := time.Now()
	s.now = func() time.Time { return now }
	mediaPresence(t, s, firstToken, "r1")
	mediaPresence(t, s, secondToken, "r2")
	n := sampleNotification("first")
	n.Media = mediaTestFrame(t)
	n.TargetRuntimes = s.NotificationTargets(n)
	n.MediaTargetRuntimes = s.NotificationMediaTargets(n)
	if s.DeliverNotification(n).Queued != 1 {
		t.Fatal("notification not queued")
	}
	w := request(s, "GET", "/api/notifications?runtime=r1&active=true&media=jpeg-v1", firstToken, "", nil)
	expect(t, w, 200)
	var envelope struct{ Notifications []notification }
	if err := json.Unmarshal(w.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	if len(envelope.Notifications) != 1 || envelope.Notifications[0].Media == nil {
		t.Fatal("missing descriptor")
	}
	id := envelope.Notifications[0].Media.ID
	path := "/api/notifications/media?id=" + id + "&runtime=r1"
	for _, probe := range []struct {
		token, path string
		code        int
	}{{"", path, 401}, {adminToken, path, 403}, {secondToken, path, 404}, {firstToken, path + "&device_id=second", 403}, {firstToken, strings.Replace(path, "runtime=r1", "runtime=old", 1), 404}, {firstToken, path + "&url=https://other.invalid", 400}} {
		expect(t, request(s, "GET", probe.path, probe.token, "", nil), probe.code)
	}
	w = request(s, "GET", path, firstToken, "", nil)
	expect(t, w, 200)
	if w.Header().Get("Cache-Control") != "no-store" || w.Body.Len() > 224*1024 {
		t.Fatal("unsafe response size/cache policy")
	}
	var body struct {
		Data    string  `json:"data_base64"`
		Expires float64 `json:"expires_at"`
		Runtime string  `json:"runtime"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	decoded, err := base64.StdEncoding.DecodeString(body.Data)
	if err != nil || !bytes.Equal(decoded, n.Media.JPEG) || body.Runtime != "r1" {
		t.Fatal("wrong frame")
	}
	expect(t, request(s, "GET", "/api/notifications?runtime=r1&active=true&enabled=false&media=jpeg-v1", firstToken, "", nil), 200)
	expect(t, request(s, "GET", path, firstToken, "", nil), 404)
	mediaPresence(t, s, firstToken, "r1")
	expect(t, request(s, "GET", path, firstToken, "", nil), 404)
}

func TestMediaStoreRuntimeExpiryRevocationAndBounds(t *testing.T) {
	s := newTestServer(t)
	now := time.Now()
	frame := mediaTestFrame(t)
	id := strings.Repeat("a", 32)
	if s.grantNotificationMedia(id, "first", "r1", frame, now.Add(time.Second), now) == nil {
		t.Fatal("grant failed")
	}
	if s.grantNotificationMedia(id, "second", "r2", frame, now.Add(time.Second), now) == nil {
		t.Fatal("shared grant failed")
	}
	if s.notificationMedia.bytes != len(frame.JPEG) {
		t.Fatal("shared frame duplicated")
	}
	if _, _, ok := s.notificationMedia.get(id, "first", "wrong", now); ok {
		t.Fatal("runtime bypass")
	}
	s.notificationMedia.revokeDevice("first")
	if _, _, ok := s.notificationMedia.get(id, "first", "r1", now); ok {
		t.Fatal("revoked grant survived")
	}
	if _, _, ok := s.notificationMedia.get(id, "second", "r2", now); !ok {
		t.Fatal("unrelated grant removed")
	}
	if _, _, ok := s.notificationMedia.get(id, "second", "r2", now.Add(time.Second)); ok {
		t.Fatal("expired frame survived")
	}
	if s.notificationMedia.bytes != 0 {
		t.Fatal("expired bytes retained")
	}
	bad := *frame
	bad.Width++
	if s.grantNotificationMedia(id, "first", "r1", &bad, now.Add(time.Second), now) != nil {
		t.Fatal("mismatched dimensions accepted")
	}
	for i := range notificationMediaMaxEntries {
		if s.grantNotificationMedia(fmt.Sprintf("%032x", i), "first", "r1", frame, now.Add(time.Minute), now) == nil {
			t.Fatal("premature store cap")
		}
	}
	if s.grantNotificationMedia(id, "first", "r1", frame, now.Add(time.Minute), now) != nil {
		t.Fatal("store entry cap exceeded")
	}
	s.notificationMedia.revokeDevice("first")
	if len(s.notificationMedia.entries) != 0 || s.notificationMedia.bytes != 0 {
		t.Fatal("revocation did not release bytes")
	}
}

func TestMediaExpiryTimerReleasesWithoutAnotherRequest(t *testing.T) {
	s := newTestServer(t)
	now := time.Now()
	id := strings.Repeat("b", 32)
	if s.grantNotificationMedia(id, "first", "r1", mediaTestFrame(t), now.Add(20*time.Millisecond), now) == nil {
		t.Fatal("grant failed")
	}
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		s.notificationMedia.mu.Lock()
		empty := len(s.notificationMedia.entries) == 0 && s.notificationMedia.bytes == 0
		s.notificationMedia.mu.Unlock()
		if empty {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("timer retained expired camera data")
}
