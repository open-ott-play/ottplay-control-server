package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
	"github.com/open-ott-play/ottplay-control-server/internal/homeassistant"
)

func sampleNotification(targets ...string) homeassistant.Notification {
	return homeassistant.Notification{Title: "Home", Message: "Door opened", Severity: "info", DurationMS: 5000, TTLSeconds: 15, Targets: targets}
}

func pollBanners(t *testing.T, s *Server, token, runtime string, active bool) []notification {
	t.Helper()
	state := "false"
	if active {
		state = "true"
	}
	w := request(s, "GET", "/api/notifications?runtime="+runtime+"&active="+state, token, "", nil)
	expect(t, w, 200)
	var got struct {
		Version       int
		ServerTime    float64 `json:"server_time"`
		Notifications []notification
	}
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if got.Version != 1 || got.ServerTime == 0 || got.Notifications == nil {
		t.Fatal("invalid notification envelope")
	}
	return got.Notifications
}

func TestNotificationsAreActiveRuntimeBoundAndSeparateFromCommands(t *testing.T) {
	s := newTestServer(t)
	now := time.Unix(2000000000, 0)
	s.now = func() time.Time { return now }
	// Normal command polling alone does not opt an old or sleeping client in.
	request(s, "GET", "/api/webhook/commands", secondToken, "", nil)
	pollBanners(t, s, firstToken, "runtime-1", true)
	r := s.DeliverNotification(sampleNotification())
	if r.Queued != 1 || r.Skipped != 1 {
		t.Fatalf("delivery: %+v", r)
	}
	enqueue(t, s, "first", `{"command":"popup_message","message":"manual"}`)
	items := pollBanners(t, s, firstToken, "runtime-1", true)
	if len(items) != 1 || items[0].Runtime != "runtime-1" || !commandID.MatchString(items[0].ID) {
		t.Fatalf("items: %+v", items)
	}
	if len(pollBanners(t, s, firstToken, "runtime-1", true)) != 0 {
		t.Fatal("banner replayed")
	}
	if len(commands(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), false)) != 1 {
		t.Fatal("banners affected command queue")
	}
	s.DeliverNotification(sampleNotification("first"))
	if len(pollBanners(t, s, firstToken, "runtime-2", true)) != 0 {
		t.Fatal("old runtime received banner")
	}
	s.DeliverNotification(sampleNotification("first"))
	if len(pollBanners(t, s, firstToken, "runtime-2", false)) != 0 {
		t.Fatal("inactive player received banner")
	}
	if s.DeliverNotification(sampleNotification("first")).Queued != 0 {
		t.Fatal("inactive player queued banner")
	}
	if len(pollBanners(t, s, firstToken, "runtime-2", true)) != 0 {
		t.Fatal("wake replayed banner")
	}
	s.DeliverNotification(sampleNotification("first"))
	now = now.Add(notificationPresenceTTL)
	if len(pollBanners(t, s, firstToken, "runtime-2", true)) != 0 {
		t.Fatal("reconnect revived stale banner")
	}
}

func TestNotificationsTargetExpiryAndBounds(t *testing.T) {
	s := newTestServer(t)
	now := time.Unix(2000000000, 0)
	s.now = func() time.Time { return now }
	pollBanners(t, s, firstToken, "r1", true)
	pollBanners(t, s, secondToken, "r2", true)
	r := s.DeliverNotification(sampleNotification("first"))
	if r.Queued != 1 || r.Skipped != 0 || len(pollBanners(t, s, secondToken, "r2", true)) != 0 {
		t.Fatalf("target leaked: %+v", r)
	}
	for i := 1; i < notificationQueueLimit; i++ {
		s.DeliverNotification(sampleNotification("first"))
	}
	if s.DeliverNotification(sampleNotification("first")).Skipped != 1 {
		t.Fatal("queue overflow accepted")
	}
	if len(pollBanners(t, s, firstToken, "r1", true)) != notificationQueueLimit {
		t.Fatal("queue bound")
	}
	n := sampleNotification("first")
	n.TTLSeconds = 1
	s.DeliverNotification(n)
	now = now.Add(time.Second)
	if len(pollBanners(t, s, firstToken, "r1", true)) != 0 {
		t.Fatal("expired banner displayed")
	}
	for _, mutate := range []func(*homeassistant.Notification){
		func(n *homeassistant.Notification) { n.Message = strings.Repeat("🙂", 257) },
		func(n *homeassistant.Notification) { n.Title = "bad\x1b[0m" },
		func(n *homeassistant.Notification) { n.Severity = "html" },
		func(n *homeassistant.Notification) { n.DurationMS = 15001 },
	} {
		n = sampleNotification()
		mutate(&n)
		if s.DeliverNotification(n).Queued != 0 {
			t.Fatal("invalid notification accepted")
		}
	}
}

func TestNotificationsAuthQueriesAndStatus(t *testing.T) {
	s := newTestServer(t)
	path := "/api/notifications?runtime=r1&active=true"
	expect(t, request(s, "GET", path, "", "", nil), 401)
	expect(t, request(s, "GET", path, adminToken, "", nil), 403)
	expect(t, request(s, "GET", path+"&device_id=second", firstToken, "", nil), 403)
	for _, query := range []string{"runtime=r1", "runtime=r1&active=1", "runtime=r1&active=true&active=false", "runtime=r1&active=true&token=secret", "runtime=%3Cscript%3E&active=true"} {
		expect(t, request(s, "GET", "/api/notifications?"+query, firstToken, "", nil), 400)
	}
	expect(t, request(s, "GET", "/api/home-assistant/status", firstToken, "", nil), 403)
	pollBanners(t, s, firstToken, "r1", true)
	s.DeliverNotification(sampleNotification("first"))
	s.SetHomeAssistantStatus(func() homeassistant.Snapshot { return homeassistant.Snapshot{Enabled: true, State: "connected"} })
	w := request(s, "GET", "/api/home-assistant/status", adminToken, "", nil)
	expect(t, w, 200)
	for _, private := range []string{firstToken, "Door opened", "\"r1\"", "token_file"} {
		if strings.Contains(w.Body.String(), private) {
			t.Fatal("private data in status")
		}
	}
	if !strings.Contains(w.Body.String(), `"queued":1`) {
		t.Fatal("missing counts")
	}
	opToken := strings.Repeat("o", 32)
	hash := sha256.Sum256([]byte(opToken))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "reader", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"status"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	w = request(s, "GET", "/api/home-assistant/status", opToken, "", nil)
	expect(t, w, 200)
	if strings.Contains(w.Body.String(), "second") || strings.Contains(w.Body.String(), `"delivery"`) || strings.Contains(w.Body.String(), `"events_received"`) {
		t.Fatal("operator scope leaked")
	}
	expect(t, request(s, "GET", path, opToken, "", nil), 403)
	s.operators[0].actions = map[string]bool{"capabilities": true}
	expect(t, request(s, "GET", "/api/home-assistant/status", opToken, "", nil), 403)
}
