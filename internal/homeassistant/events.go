package homeassistant

import (
	"crypto/sha256"
	"encoding/json"
	"regexp"
	"strings"
	"time"
	"unicode"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type event struct {
	EventType string          `json:"event_type"`
	Data      json.RawMessage `json:"data"`
	TimeFired time.Time       `json:"time_fired"`
}

type entityState struct {
	State      string `json:"state"`
	Attributes struct {
		FriendlyName string `json:"friendly_name"`
	} `json:"attributes"`
}

func (b *Bridge) processEvent(raw json.RawMessage, subscribedType string) {
	now := b.now().UTC()
	b.mu.Lock()
	b.status.EventsReceived++
	b.status.LastEventAt = &now
	b.mu.Unlock()
	var ev event
	if json.Unmarshal(raw, &ev) != nil || ev.EventType != subscribedType || ev.TimeFired.IsZero() || now.Sub(ev.TimeFired) > time.Duration(b.config.EventMaxAgeSeconds)*time.Second || ev.TimeFired.Sub(now) > 5*time.Second {
		b.dropped()
		return
	}
	for k, expiry := range b.seen {
		if !now.Before(expiry) {
			delete(b.seen, k)
		}
	}
	key := sha256.Sum256(raw)
	if _, exists := b.seen[key]; exists {
		b.dropped()
		return
	}
	// A bounded replay window stores hashes only, never raw HA event contents.
	if len(b.seen) >= 1024 {
		var oldest [32]byte
		var expiry time.Time
		for k, v := range b.seen {
			if expiry.IsZero() || v.Before(expiry) {
				oldest, expiry = k, v
			}
		}
		delete(b.seen, oldest)
	}
	b.seen[key] = now.Add(time.Duration(b.config.EventMaxAgeSeconds) * time.Second)
	fields, entity, old, state, ok := eventFields(ev)
	if !ok {
		b.dropped()
		return
	}
	matched, attempted := false, false
	for _, rule := range b.config.Rules {
		if rule.EventType != ev.EventType || (ev.EventType == "state_changed" && (!contains(rule.EntityIDs, entity) || (len(rule.From) > 0 && !contains(rule.From, old)) || (len(rule.To) > 0 && !contains(rule.To, state)))) {
			continue
		}
		matched = true
		cooldownKey := rule.ID + "\x00" + entity
		if now.Before(b.cooldown[cooldownKey]) {
			continue
		}
		if b.rateStart.IsZero() || now.Sub(b.rateStart) >= time.Minute {
			b.rateStart, b.rateUsed = now, 0
		}
		if b.rateUsed >= b.config.MaxPerMinute {
			continue
		}
		n := render(rule, fields)
		if n.Message == "" || n.Title == "" {
			continue
		}
		b.cooldown[cooldownKey] = now.Add(time.Duration(rule.CooldownSeconds) * time.Second)
		b.rateUsed++
		attempted = true
		result := DeliveryResult{}
		if b.deliver != nil {
			result = b.deliver(n)
		}
		b.mu.Lock()
		b.status.NotificationsQueued += uint64(max(result.Queued, 0))
		b.status.NotificationsSkipped += uint64(max(result.Skipped, 0))
		b.mu.Unlock()
	}
	b.mu.Lock()
	if matched {
		b.status.EventsMatched++
	}
	if !attempted {
		b.status.EventsDropped++
	}
	b.mu.Unlock()
}

func (b *Bridge) dropped() {
	b.mu.Lock()
	b.status.EventsDropped++
	b.mu.Unlock()
}

func eventFields(ev event) (map[string]string, string, string, string, bool) {
	fields := map[string]string{"event_type": ev.EventType}
	if ev.EventType == "state_changed" {
		var data struct {
			EntityID string       `json:"entity_id"`
			OldState *entityState `json:"old_state"`
			NewState *entityState `json:"new_state"`
		}
		if json.Unmarshal(ev.Data, &data) != nil || data.OldState == nil || data.NewState == nil || data.OldState.State == data.NewState.State {
			return nil, "", "", "", false
		}
		fields["entity_id"] = data.EntityID
		fields["name"] = data.NewState.Attributes.FriendlyName
		if fields["name"] == "" {
			fields["name"] = data.EntityID
		}
		fields["old_state"], fields["state"] = data.OldState.State, data.NewState.State
		return fields, data.EntityID, data.OldState.State, data.NewState.State, true
	}
	// Deliberately extract only two plain-string custom fields. Attributes, URLs,
	// credentials, object dumps and arbitrary template expressions are not read.
	var data struct {
		Title   string `json:"title"`
		Message string `json:"message"`
	}
	if json.Unmarshal(ev.Data, &data) != nil {
		return nil, "", "", "", false
	}
	fields["title"], fields["message"] = data.Title, data.Message
	return fields, "", "", "", true
}

func contains(values []string, value string) bool {
	for _, v := range values {
		if v == value {
			return true
		}
	}
	return false
}

var placeholder = regexp.MustCompile(`\{\{\s*([a-z_]+)\s*\}\}`)

func render(rule config.HomeAssistantRule, fields map[string]string) Notification {
	format := func(template string, limit int) string {
		text := placeholder.ReplaceAllStringFunc(template, func(p string) string {
			return cleanText(fields[placeholder.FindStringSubmatch(p)[1]], limit)
		})
		return cleanText(text, limit)
	}
	return Notification{Title: format(rule.Title, 120), Message: format(rule.Message, 512), Severity: rule.Severity, DurationMS: rule.DurationMS, TTLSeconds: rule.TTLSeconds, Targets: append([]string(nil), rule.Targets...)}
}

// Bounds are UTF-16 code units, matching JavaScript length checks in every player.
// Bidi/format and control characters are removed; HTML stays inert plain text.
func cleanText(s string, limit int) string {
	var out strings.Builder
	units := 0
	for _, r := range s {
		if unicode.IsControl(r) || unicode.Is(unicode.Cf, r) {
			if r == '\n' || r == '\t' {
				r = ' '
			} else {
				continue
			}
		}
		n := 1
		if r > 0xffff {
			n = 2
		}
		if units+n > limit {
			break
		}
		out.WriteRune(r)
		units += n
	}
	return strings.TrimSpace(out.String())
}
