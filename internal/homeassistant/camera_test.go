package homeassistant

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"image"
	"image/jpeg"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func cameraConfig(t *testing.T) config.HomeAssistant {
	t.Helper()
	c := testConfig()
	c.TokenFile = filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(c.TokenFile, []byte(strings.Repeat("a", 32)), 0600); err != nil {
		t.Fatal(err)
	}
	c.Cameras = map[string]config.HomeAssistantCamera{"garage": {EntityID: "camera.garage_notifications", Label: "Garage"}}
	c.Rules = []config.HomeAssistantRule{{ID: "garage", EventType: "cisco_motion_snapshot_requested", CameraKey: "garage", Title: "Motion", Message: "{{name}}: motion detected"}}
	c.Defaults()
	return c
}
func cameraJPEG(t *testing.T, w, h int) []byte {
	t.Helper()
	var b bytes.Buffer
	if err := jpeg.Encode(&b, image.NewRGBA(image.Rect(0, 0, w, h)), nil); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}
func cameraEvent(t *testing.T, now time.Time, key, id string, at float64) json.RawMessage {
	t.Helper()
	raw, e := json.Marshal(map[string]any{"event_type": "cisco_motion_snapshot_requested", "time_fired": now, "data": map[string]any{"camera_key": key, "event_id": id, "event_at": at, "url": "https://never-follow.invalid", "token": "NEVER_DISPLAY"}})
	if e != nil {
		t.Fatal(e)
	}
	return raw
}

func TestCameraFirstFrameClosesLiveStreamAndStripsMetadata(t *testing.T) {
	raw := cameraJPEG(t, 1280, 720)
	// An APP segment containing EOI-looking bytes cannot truncate the first frame.
	segment := []byte{0xff, 0xe1, 0, 10, 'P', 'R', 0xff, 0xd9, 'I', 'V', 'A', 'T'}
	raw = append(append(append([]byte{}, raw[:2]...), segment...), raw[2:]...)
	closed := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ha/api/camera_proxy_stream/camera.garage_notifications" || r.Header.Get("Authorization") != "Bearer "+strings.Repeat("a", 32) {
			t.Error("wrong authenticated camera target")
		}
		// Match HA async_get_still_stream: parameter already includes dashes.
		w.Header().Set("Content-Type", "multipart/x-mixed-replace; boundary=--frame")
		io.WriteString(w, "--frame\r\nContent-Type: image/jpeg\r\n\r\n")
		w.Write(raw)
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		close(closed)
	}))
	defer server.Close()
	c := cameraConfig(t)
	c.URL = server.URL + "/ha"
	frame, err := AcquireCameraFrame(context.Background(), c, "garage")
	if err != nil || frame.Width != 640 || frame.Height != 360 || len(frame.JPEG) > CameraJPEGMaxBytes {
		t.Fatalf("bad frame: %v %#v", err, frame)
	}
	if bytes.Contains(frame.JPEG, segment) {
		t.Fatal("metadata survived sanitization")
	}
	if _, err = jpeg.Decode(bytes.NewReader(frame.JPEG)); err != nil {
		t.Fatal(err)
	}
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("camera stream remained open")
	}
}

func TestCameraRejectsRedirectsUnconfiguredKeysAndBadFrames(t *testing.T) {
	var targetHits atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { targetHits.Add(1) }))
	defer target.Close()
	for name, handler := range map[string]http.HandlerFunc{
		"redirect": func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, 302) },
		"html": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "text/html")
			io.WriteString(w, "<img>")
		},
		"truncated": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "image/jpeg")
			w.Write([]byte{0xff, 0xd8, 0xff, 0xda})
		},
		"oversized": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "image/jpeg")
			w.Write(append([]byte{0xff, 0xd8, 0xff, 0xda, 0, 2}, bytes.Repeat([]byte{1}, cameraSourceMaxBytes)...))
		},
		"dimensions": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "image/jpeg")
			w.Write(cameraJPEG(t, 16385, 1))
		},
	} {
		t.Run(name, func(t *testing.T) {
			s := httptest.NewServer(handler)
			defer s.Close()
			c := cameraConfig(t)
			c.URL = s.URL
			if _, err := AcquireCameraFrame(context.Background(), c, "garage"); err == nil {
				t.Fatal("accepted unsafe camera response")
			}
		})
	}
	if targetHits.Load() != 0 {
		t.Fatal("followed credential-bearing redirect")
	}
	c := cameraConfig(t)
	c.URL = target.URL
	if _, err := AcquireCameraFrame(context.Background(), c, "not_configured"); err == nil || targetHits.Load() != 0 {
		t.Fatal("unconfigured camera attempted network")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := AcquireCameraFrame(ctx, c, "garage"); err == nil || targetHits.Load() != 0 {
		t.Fatal("cancelled camera attempted network")
	}
}

func TestCameraEventsRejectReplayOriginalAgeAndWrongCamera(t *testing.T) {
	now := time.Now()
	var delivered []Notification
	b := New(cameraConfig(t), func(n Notification) DeliveryResult {
		delivered = append(delivered, n)
		return DeliveryResult{Queued: 1}
	})
	b.now = func() time.Time { return now }
	b.SetCameraTargets(func(Notification) map[string]string { return map[string]string{"first": "r1"} }, func(Notification) map[string]string { return nil })
	for _, raw := range []json.RawMessage{cameraEvent(t, now, "other", "valid_event_id", float64(now.Unix())), cameraEvent(t, now, "garage", "bad id", float64(now.Unix())), cameraEvent(t, now, "garage", "old_event_id", float64(now.Unix()-91)), cameraEvent(t, now, "garage", "future_event_id", float64(now.Unix()+7)), cameraEvent(t, now.Add(-31*time.Second), "garage", "old_envelope_id", float64(now.Unix()-35))} {
		b.processEvent(raw, "cisco_motion_snapshot_requested")
	}
	if len(delivered) != 0 {
		t.Fatal("invalid event delivered")
	}
	b.processEvent(cameraEvent(t, now, "garage", "same_event_id", float64(now.Unix()-35)), "cisco_motion_snapshot_requested")
	if len(delivered) != 1 || delivered[0].Message != "Garage: motion detected" || delivered[0].Media != nil || delivered[0].TargetRuntimes["first"] != "r1" {
		t.Fatal("text fallback/warmed-up event failed", delivered)
	}
	now = now.Add(61 * time.Second)
	var repeated map[string]any
	if err := json.Unmarshal(cameraEvent(t, now, "garage", "same_event_id", float64(now.Unix())), &repeated); err != nil {
		t.Fatal(err)
	}
	repeated["data"].(map[string]any)["event_at"] = strconv.FormatInt(now.Unix(), 10)
	raw, err := json.Marshal(repeated)
	if err != nil {
		t.Fatal(err)
	}
	b.processEvent(raw, "cisco_motion_snapshot_requested")
	if len(delivered) != 1 {
		t.Fatal("changed timestamp bypassed original event ID dedupe")
	}
}

func TestCameraEventTimeAcceptsOnlyFiniteNumbersOrStrictDecimalStrings(t *testing.T) {
	for _, raw := range []string{`1792800000`, `1792800000.5`, `"1792800000"`, `"1792800000.5"`} {
		if value, ok := cameraEventTime(json.RawMessage(raw)); !ok || value < 1792800000 {
			t.Fatalf("rejected supported timestamp %s", raw)
		}
	}
	for _, raw := range []string{`null`, `true`, `[]`, `0`, `-1`, `1e999`, `"NaN"`, `"Infinity"`, `" 1792800000"`, `"1792800000 "`, `"1e9"`, `"1792800000.1234567890"`, `""`} {
		if _, ok := cameraEventTime(json.RawMessage(raw)); ok {
			t.Fatalf("accepted malformed timestamp %s", raw)
		}
	}
}

func TestCameraOriginalFreshnessCapsNotificationDeadline(t *testing.T) {
	now := time.Unix(1792800000, 0)
	var got Notification
	b := New(cameraConfig(t), func(n Notification) DeliveryResult { got = n; return DeliveryResult{Queued: 1} })
	b.now = func() time.Time { return now }
	b.SetCameraTargets(func(Notification) map[string]string { return map[string]string{"first": "r1"} }, nil)
	b.processEvent(cameraEvent(t, now, "garage", "near_expiry_event", float64(now.Unix()-89)), "cisco_motion_snapshot_requested")
	if !got.ExpiresAt.Equal(now.Add(time.Second)) {
		t.Fatal("admission extended original freshness", got.ExpiresAt)
	}
}

func TestCameraReplayWindowIsIsolatedFromBusyUnmatchedStateEvents(t *testing.T) {
	now := time.Now()
	delivered := 0
	b := New(cameraConfig(t), func(Notification) DeliveryResult { delivered++; return DeliveryResult{} })
	b.now = func() time.Time { return now }
	b.SetCameraTargets(func(Notification) map[string]string { return map[string]string{"first": "r"} }, nil)
	for i := range 1024 {
		b.seen[sha256.Sum256([]byte(fmt.Sprint(i)))] = now.Add(time.Minute)
	}
	b.processEvent(cameraEvent(t, now, "garage", "busy_ha_event", float64(now.Unix())), "cisco_motion_snapshot_requested")
	if delivered != 1 || len(b.cameraSeen) != 1 {
		t.Fatal("ordinary event window starved camera notification")
	}
	clear(b.seen)
	now = now.Add(61 * time.Second)
	b.processEvent(cameraEvent(t, now, "garage", "busy_ha_event", float64(now.Unix())), "cisco_motion_snapshot_requested")
	if delivered != 1 {
		t.Fatal("ordinary event eviction removed camera replay protection")
	}
}

func TestCameraPreambleBoundAndStandardMultipart(t *testing.T) {
	raw := cameraJPEG(t, 1, 1)
	for _, prefix := range []string{"--frame\r\nContent-Type: image/jpeg\r\n\r\n", "--frameboundary\r\nContent-Length: 123\r\n\r\n"} {
		r, err := cameraJPEGStart(io.MultiReader(strings.NewReader(prefix), bytes.NewReader(raw)))
		if err != nil {
			t.Fatal(err)
		}
		got, err := firstCameraJPEG(r)
		if err != nil || !bytes.Equal(got, raw) {
			t.Fatal("multipart first frame mismatch")
		}
	}
	if _, err := cameraJPEGStart(io.MultiReader(strings.NewReader(strings.Repeat("x", 32*1024)), bytes.NewReader(raw))); err == nil {
		t.Fatal("unbounded preamble accepted")
	}
}

func TestCameraWorkerIsBoundedDoesNotBlockTextAndBindsAdmission(t *testing.T) {
	c := cameraConfig(t)
	c.Rules = append(c.Rules, config.HomeAssistantRule{ID: "light", EventType: "state_changed", EntityIDs: []string{"light.room"}, Title: "Lights", Message: "{{state}}"})
	c.Defaults()
	delivered := make(chan Notification, 4)
	started := make(chan struct{}, 1)
	release := make(chan struct{})
	b := New(c, func(n Notification) DeliveryResult { delivered <- n; return DeliveryResult{Queued: 1} })
	b.SetCameraTargets(func(Notification) map[string]string { return map[string]string{"first": "original"} }, func(Notification) map[string]string { return map[string]string{"first": "original"} })
	b.cameraFetch = func(ctx context.Context, _ config.HomeAssistant, _ string) (*CameraFrame, error) {
		started <- struct{}{}
		select {
		case <-release:
			return &CameraFrame{JPEG: []byte("fixture"), Width: 1, Height: 1}, nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	stop := b.startCameraWorkers(context.Background())
	defer stop()
	now := time.Now()
	b.processEvent(cameraEvent(t, now, "garage", "camera_event_01", float64(now.Unix())), "cisco_motion_snapshot_requested")
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("worker not started")
	}
	b.processEvent(stateEvent(now, "light.room", state("on"), state("off")), "state_changed")
	select {
	case n := <-delivered:
		if n.Message != "off" {
			t.Fatal("light off blocked")
		}
	case <-time.After(time.Second):
		t.Fatal("camera blocked ordinary text")
	}
	close(release)
	select {
	case n := <-delivered:
		if n.Media == nil || n.TargetRuntimes["first"] != "original" || n.ExpiresAt.After(now.Add(31*time.Second)) {
			t.Fatal("lost admission binding")
		}
	case <-time.After(time.Second):
		t.Fatal("camera missing")
	}
}

func TestCameraNoActiveTargetsAndFullQueueDoNotCapture(t *testing.T) {
	now := time.Now()
	delivered := 0
	b := New(cameraConfig(t), func(Notification) DeliveryResult { delivered++; return DeliveryResult{} })
	b.SetCameraTargets(func(Notification) map[string]string { return nil }, func(Notification) map[string]string { return nil })
	b.processEvent(cameraEvent(t, now, "garage", "no_target_event", float64(now.Unix())), "cisco_motion_snapshot_requested")
	if len(b.cameraQueue) != 0 || delivered != 0 {
		t.Fatal("queued inactive event")
	}
	b = New(cameraConfig(t), func(Notification) DeliveryResult { delivered++; return DeliveryResult{} })
	b.SetCameraTargets(func(Notification) map[string]string { return map[string]string{"first": "r"} }, func(Notification) map[string]string { return map[string]string{"first": "r"} })
	for range cap(b.cameraQueue) {
		b.cameraQueue <- cameraJob{}
	}
	b.processEvent(cameraEvent(t, now, "garage", "full_queue_event", float64(now.Unix())), "cisco_motion_snapshot_requested")
	if len(b.cameraQueue) != 4 || delivered != 1 {
		t.Fatal("full queue did not fall back to text")
	}
}

func TestCameraQueuedWorkRechecksRecipientBeforeNetwork(t *testing.T) {
	var fetched atomic.Int32
	done := make(chan Notification, 1)
	b := New(cameraConfig(t), func(n Notification) DeliveryResult { done <- n; return DeliveryResult{} })
	b.SetCameraTargets(nil, func(Notification) map[string]string { return map[string]string{"first": "new-runtime"} })
	b.cameraFetch = func(context.Context, config.HomeAssistant, string) (*CameraFrame, error) {
		fetched.Add(1)
		return nil, errCamera
	}
	b.cameraQueue <- cameraJob{"garage", Notification{TargetRuntimes: map[string]string{"first": "old-runtime"}, MediaTargetRuntimes: map[string]string{"first": "old-runtime"}, ExpiresAt: time.Now().Add(time.Second)}}
	stop := b.startCameraWorkers(context.Background())
	defer stop()
	select {
	case n := <-done:
		if n.Media != nil {
			t.Fatal("retired target got media")
		}
	case <-time.After(time.Second):
		t.Fatal("worker did not drain retired job")
	}
	if fetched.Load() != 0 {
		t.Fatal("captured for retired runtime")
	}
}

func TestCameraRequestDeadlineIncludesSlowBody(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/jpeg")
		w.Write([]byte{0xff, 0xd8})
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer s.Close()
	c := cameraConfig(t)
	c.URL = s.URL
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	started := time.Now()
	if _, err := AcquireCameraFrame(ctx, c, "garage"); err == nil || time.Since(started) > time.Second {
		t.Fatal("slow camera body outlived deadline")
	}
}

func TestCameraURLMatchesWebSocketBaseNormalization(t *testing.T) {
	raw := cameraJPEG(t, 1, 1)
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ha/api/camera_proxy_stream/camera.garage_notifications" {
			t.Error("incorrect camera base", r.URL.Path)
		}
		w.Header().Set("Content-Type", "image/jpeg")
		w.Write(raw)
	}))
	defer s.Close()
	for _, suffix := range []string{"/ha", "/ha/", "/ha/api/websocket", "/ha/api/websocket/"} {
		t.Run(suffix, func(t *testing.T) {
			c := cameraConfig(t)
			c.URL = s.URL + suffix
			if _, err := AcquireCameraFrame(context.Background(), c, "garage"); err != nil {
				t.Fatal(err)
			}
		})
	}
}
