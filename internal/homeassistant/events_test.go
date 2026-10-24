package homeassistant

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
	"unicode/utf16"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func stateEvent(now time.Time, entity string, old, next any) json.RawMessage {
	b, _ := json.Marshal(map[string]any{"event_type": "state_changed", "time_fired": now.Format(time.RFC3339Nano), "data": map[string]any{"entity_id": entity, "old_state": old, "new_state": next}})
	return b
}

func state(value string) map[string]any {
	return map[string]any{"state": value, "attributes": map[string]any{"friendly_name": "Front door", "private_attribute": "DO_NOT_DISPLAY"}}
}

func noticeEvent(now time.Time, title, message string) json.RawMessage {
	b, _ := json.Marshal(map[string]any{"event_type": "ottplay_notification", "time_fired": now.Format(time.RFC3339Nano), "data": map[string]any{"title": title, "message": message, "private_attribute": "DO_NOT_DISPLAY"}})
	return b
}

func testConfig() config.HomeAssistant {
	c := config.HomeAssistant{Enabled: true, URL: "http://localhost", TokenFile: "/test/token", Rules: []config.HomeAssistantRule{{ID: "door", EventType: "state_changed", EntityIDs: []string{"binary_sensor.door"}, From: []string{"off"}, To: []string{"on"}, Title: "Home", Message: "{{name}}: {{old_state}} → {{state}}", Targets: []string{"tv"}}}}
	c.Defaults()
	return c
}

func TestStateEventsFilterInitialSnapshotsAttributesAgeAndEntities(t *testing.T) {
	now := time.Date(2026, 10, 23, 12, 0, 0, 0, time.UTC)
	cases := map[string]json.RawMessage{
		"initial":      stateEvent(now, "binary_sensor.door", nil, state("on")),
		"removed":      stateEvent(now, "binary_sensor.door", state("off"), nil),
		"attributes":   stateEvent(now, "binary_sensor.door", state("on"), state("on")),
		"other entity": stateEvent(now, "binary_sensor.other", state("off"), state("on")),
		"wrong from":   stateEvent(now, "binary_sensor.door", state("unknown"), state("on")),
		"wrong to":     stateEvent(now, "binary_sensor.door", state("on"), state("off")),
		"stale":        stateEvent(now.Add(-31*time.Second), "binary_sensor.door", state("off"), state("on")),
		"future":       stateEvent(now.Add(6*time.Second), "binary_sensor.door", state("off"), state("on")),
		"missing time": json.RawMessage(`{"event_type":"state_changed","data":{}}`),
		"malformed":    json.RawMessage(`{"event_type":`),
	}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) {
			called := false
			b := New(testConfig(), func(Notification) DeliveryResult { called = true; return DeliveryResult{Queued: 1} })
			b.now = func() time.Time { return now }
			b.processEvent(raw, "state_changed")
			if called || b.Snapshot().EventsDropped != 1 {
				t.Fatal("event should have been suppressed", b.Snapshot())
			}
		})
	}
	var got Notification
	b := New(testConfig(), func(n Notification) DeliveryResult { got = n; return DeliveryResult{Queued: 1, Skipped: 2} })
	b.now = func() time.Time { return now }
	b.processEvent(stateEvent(now, "binary_sensor.door", state("off"), state("on")), "state_changed")
	if got.Title != "Home" || got.Message != "Front door: off → on" || len(got.Targets) != 1 || got.Targets[0] != "tv" {
		t.Fatal("unexpected notification", got)
	}
	s := b.Snapshot()
	if s.EventsMatched != 1 || s.NotificationsQueued != 1 || s.NotificationsSkipped != 2 || s.EventsDropped != 0 {
		t.Fatal("incorrect metrics", s)
	}
}

func TestReplayCooldownRateAndPerEntityIsolation(t *testing.T) {
	now := time.Now().UTC()
	c := testConfig()
	c.Rules[0].EntityIDs = []string{"binary_sensor.door", "binary_sensor.other"}
	c.MaxPerMinute = 2
	calls := 0
	b := New(c, func(Notification) DeliveryResult { calls++; return DeliveryResult{Queued: 1} })
	b.now = func() time.Time { return now }
	first := stateEvent(now, "binary_sensor.door", state("off"), state("on"))
	b.processEvent(first, "state_changed")
	b.processEvent(first, "state_changed") // replay
	now = now.Add(time.Second)
	b.processEvent(stateEvent(now, "binary_sensor.door", state("off"), state("on")), "state_changed") // cooldown
	b.processEvent(stateEvent(now, "binary_sensor.other", state("off"), state("on")), "state_changed")
	if calls != 2 || b.Snapshot().EventsDropped != 2 {
		t.Fatal("dedup/cooldown or entity isolation failed", calls, b.Snapshot())
	}
	// Even when another rule has no cooldown yet, it cannot exceed the global rate.
	b.config.Rules = append(b.config.Rules, config.HomeAssistantRule{ID: "other-rule", EventType: "ottplay_notification", Title: "Home", Message: "{{message}}", CooldownSeconds: 1})
	b.processEvent(noticeEvent(now, "Home", "test"), "ottplay_notification")
	if calls != 2 {
		t.Fatal("global rate was not enforced")
	}
	now = now.Add(time.Minute)
	b.processEvent(stateEvent(now, "binary_sensor.door", state("off"), state("on")), "state_changed")
	if calls != 3 {
		t.Fatal("cooldown/rate failed to recover")
	}
}

func TestCustomRenderingIsBoundedPlainTextAndNeverDumpsAttributes(t *testing.T) {
	c := testConfig()
	c.Rules = []config.HomeAssistantRule{{ID: "notice", EventType: "ottplay_notification", Title: "{{title}}", Message: "{{message}}"}}
	var got Notification
	b := New(c, func(n Notification) DeliveryResult { got = n; return DeliveryResult{Queued: 1} })
	now := time.Now()
	b.now = func() time.Time { return now }
	b.processEvent(noticeEvent(now, strings.Repeat("😀", 100), "<b>text</b>\u202e\x00\n"+strings.Repeat("😀", 300)), "ottplay_notification")
	if len(utf16.Encode([]rune(got.Title))) > 120 || len(utf16.Encode([]rune(got.Message))) > 512 || !strings.HasPrefix(got.Message, "<b>text</b> ") || strings.ContainsAny(got.Message, "\u202e\x00") || strings.Contains(got.Message, "DO_NOT_DISPLAY") {
		t.Fatal("unsafe or unbounded rendering", got)
	}
	raw, _ := json.Marshal(b.Snapshot())
	if strings.Contains(string(raw), "text</b>") || strings.Contains(string(raw), "DO_NOT_DISPLAY") {
		t.Fatal("status must not contain payload contents")
	}
}

func TestNoMatchingTemplateDataAndWrongSubscriptionAreDropped(t *testing.T) {
	c := testConfig()
	c.Rules = []config.HomeAssistantRule{{ID: "notice", EventType: "ottplay_notification", Title: "{{title}}", Message: "{{message}}"}}
	calls := 0
	b := New(c, func(Notification) DeliveryResult { calls++; return DeliveryResult{} })
	now := time.Now()
	b.now = func() time.Time { return now }
	b.processEvent(noticeEvent(now, "", "message"), "ottplay_notification")
	b.processEvent(noticeEvent(now, "title", "message"), "state_changed")
	if calls != 0 || b.Snapshot().EventsDropped != 2 {
		t.Fatal("empty template/wrong subscription should be suppressed", b.Snapshot())
	}
}
