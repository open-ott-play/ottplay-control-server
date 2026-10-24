package config

import (
	"errors"
	"net/url"
	"path/filepath"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

// HomeAssistant is an opt-in, outbound-only event bridge. Credentials are never
// included in its status: TokenFile is read by the worker on each connection.
type HomeAssistant struct {
	Enabled            bool                `json:"enabled"`
	URL                string              `json:"url"`
	TokenFile          string              `json:"token_file"`
	Rules              []HomeAssistantRule `json:"rules"`
	MaxPerMinute       int                 `json:"max_per_minute,omitempty"`
	EventMaxAgeSeconds int                 `json:"event_max_age_seconds,omitempty"`
}

type HomeAssistantRule struct {
	ID              string   `json:"id"`
	EventType       string   `json:"event_type"`
	EntityIDs       []string `json:"entity_ids,omitempty"`
	From            []string `json:"from,omitempty"`
	To              []string `json:"to,omitempty"`
	Title           string   `json:"title"`
	Message         string   `json:"message"`
	Severity        string   `json:"severity,omitempty"`
	DurationMS      int      `json:"duration_ms,omitempty"`
	TTLSeconds      int      `json:"ttl_seconds,omitempty"`
	CooldownSeconds int      `json:"cooldown_seconds,omitempty"`
	Targets         []string `json:"targets,omitempty"`
}

var haIdentifier = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,128}$`)
var haEntity = regexp.MustCompile(`^[a-z0-9_]+\.[a-z0-9_]+$`)
var haTemplate = regexp.MustCompile(`\{\{\s*([a-z_]+)\s*\}\}`)

func (c *HomeAssistant) Defaults() {
	if c.MaxPerMinute == 0 {
		c.MaxPerMinute = 30
	}
	if c.EventMaxAgeSeconds == 0 {
		c.EventMaxAgeSeconds = 30
	}
	for i := range c.Rules {
		r := &c.Rules[i]
		if r.Severity == "" {
			r.Severity = "info"
		}
		if r.DurationMS == 0 {
			r.DurationMS = 5000
		}
		if r.TTLSeconds == 0 {
			r.TTLSeconds = 15
		}
		if r.CooldownSeconds == 0 {
			r.CooldownSeconds = 60
		}
	}
}

func (c HomeAssistant) Validate() error {
	if !c.Enabled {
		return nil
	}
	u, err := url.Parse(c.URL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Opaque != "" || u.RawPath != "" || strings.ContainsAny(c.URL, "\\\r\n\t ") {
		return errors.New("home_assistant.url must be an HTTP(S) base URL without credentials, query or fragment")
	}
	for _, part := range strings.Split(u.Path, "/") {
		if part == "." || part == ".." {
			return errors.New("home_assistant.url path must not contain dot segments")
		}
	}
	if !filepath.IsAbs(c.TokenFile) || strings.ContainsAny(c.TokenFile, "\x00\r\n") {
		return errors.New("home_assistant.token_file must be an absolute file path")
	}
	if c.MaxPerMinute < 1 || c.MaxPerMinute > 120 || c.EventMaxAgeSeconds < 1 || c.EventMaxAgeSeconds > 300 {
		return errors.New("home_assistant limits must be max_per_minute 1..120 and event_max_age_seconds 1..300")
	}
	if len(c.Rules) == 0 || len(c.Rules) > 64 {
		return errors.New("home_assistant.rules must contain 1..64 entries")
	}
	ids := map[string]bool{}
	for _, r := range c.Rules {
		if !haIdentifier.MatchString(r.ID) || ids[r.ID] || !haIdentifier.MatchString(r.EventType) {
			return errors.New("home_assistant rules require unique ids and explicit event_type identifiers")
		}
		ids[r.ID] = true
		if !validHATemplate(r.Title, 120) || !validHATemplate(r.Message, 512) {
			return errors.New("home_assistant rule title/message must be bounded text with only supported placeholders")
		}
		if r.Severity != "info" && r.Severity != "warning" && r.Severity != "critical" {
			return errors.New("home_assistant rule severity must be info, warning or critical")
		}
		if r.DurationMS < 1000 || r.DurationMS > 15000 || r.TTLSeconds < 1 || r.TTLSeconds > 60 || r.CooldownSeconds < 1 || r.CooldownSeconds > 86400 {
			return errors.New("home_assistant rule limits must be duration_ms 1000..15000, ttl_seconds 1..60 and cooldown_seconds 1..86400")
		}
		if len(r.Targets) > MaxDevices || !uniqueHAStrings(r.Targets, deviceID.MatchString) {
			return errors.New("home_assistant rule targets must be unique device ids")
		}
		if r.EventType == "state_changed" {
			if len(r.EntityIDs) == 0 || len(r.EntityIDs) > 128 || !uniqueHAStrings(r.EntityIDs, func(v string) bool { return len(v) <= 255 && haEntity.MatchString(v) }) {
				return errors.New("home_assistant state_changed rules require 1..128 explicit unique entity_ids")
			}
			if len(r.From) > 16 || len(r.To) > 16 || !uniqueHAStrings(r.From, validHAState) || !uniqueHAStrings(r.To, validHAState) {
				return errors.New("home_assistant rule from/to filters must contain at most 16 unique bounded states")
			}
		} else if len(r.EntityIDs) != 0 || len(r.From) != 0 || len(r.To) != 0 {
			return errors.New("home_assistant entity_ids/from/to filters require state_changed")
		}
	}
	return nil
}

func validHATemplate(v string, limit int) bool {
	units := 0
	for _, r := range v {
		units++
		if r > 0xffff {
			units++
		}
	}
	if strings.TrimSpace(v) == "" || !utf8.ValidString(v) || units > limit || strings.IndexFunc(v, func(r rune) bool { return unicode.IsControl(r) && r != '\n' }) >= 0 {
		return false
	}
	valid := true
	rest := haTemplate.ReplaceAllStringFunc(v, func(p string) string {
		switch haTemplate.FindStringSubmatch(p)[1] {
		case "entity_id", "name", "old_state", "state", "event_type", "title", "message":
		default:
			valid = false
		}
		return ""
	})
	return valid && !strings.Contains(rest, "{{") && !strings.Contains(rest, "}}")
}

func validHAState(v string) bool {
	return v != "" && utf8.ValidString(v) && utf8.RuneCountInString(v) <= 256 && strings.IndexFunc(v, unicode.IsControl) < 0
}

func uniqueHAStrings(values []string, valid func(string) bool) bool {
	seen := map[string]bool{}
	for _, v := range values {
		if !valid(v) || seen[v] {
			return false
		}
		seen[v] = true
	}
	return true
}
