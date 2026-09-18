package control

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/url"
	"strings"
)

// decodeObject rejects duplicate keys as well as trailing values. This avoids
// different clients interpreting the same command or acknowledgement differently.
func decodeObject(body []byte) (map[string]json.RawMessage, error) {
	d := json.NewDecoder(bytes.NewReader(body))
	t, err := d.Token()
	if err != nil || t != json.Delim('{') {
		return nil, errors.New("expected JSON object")
	}
	m := make(map[string]json.RawMessage)
	for d.More() {
		t, err = d.Token()
		if err != nil {
			return nil, errors.New("invalid JSON")
		}
		key, ok := t.(string)
		if !ok {
			return nil, errors.New("invalid JSON key")
		}
		if _, exists := m[key]; exists {
			return nil, errors.New("duplicate JSON key")
		}
		var v json.RawMessage
		if d.Decode(&v) != nil {
			return nil, errors.New("invalid JSON value")
		}
		m[key] = v
	}
	if _, err = d.Token(); err != nil {
		return nil, errors.New("invalid JSON object")
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return nil, errors.New("expected one JSON object")
	}
	return m, nil
}

func textValue(v json.RawMessage, max int) (string, bool) {
	var s string
	if json.Unmarshal(v, &s) != nil || len(s) > max || strings.TrimSpace(s) == "" {
		return "", false
	}
	return s, true
}

func numberValue(v json.RawMessage, min, max float64, integer bool) bool {
	var n float64
	return len(v) > 0 && string(v) != "null" && json.Unmarshal(v, &n) == nil && !math.IsNaN(n) && !math.IsInf(n, 0) && n >= min && n <= max && (!integer || math.Trunc(n) == n)
}

func validateCommand(body []byte) (map[string]json.RawMessage, error) {
	m, err := decodeObject(body)
	if err != nil {
		return nil, err
	}
	cmd, ok := textValue(m["command"], 64)
	if !ok {
		return nil, errors.New("command is required")
	}
	allowed := map[string]bool{"command": true}
	valid := false
	const maxInteger = 9007199254740991
	switch cmd {
	case "popup_message":
		allowed["message"], allowed["popup_duration"] = true, true
		_, valid = textValue(m["message"], 8192)
		if v, exists := m["popup_duration"]; exists {
			valid = valid && numberValue(v, 0.001, 3600, false)
		}
	case "channel_by_number":
		allowed["channel_number"] = true
		valid = numberValue(m["channel_number"], 1, maxInteger, true)
	case "channel_by_name":
		allowed["channel_name"] = true
		_, valid = textValue(m["channel_name"], 1024)
	case "random_channel":
		allowed["random_range"] = true
		valid = true
		if v, exists := m["random_range"]; exists {
			var a []json.RawMessage
			valid = json.Unmarshal(v, &a) == nil && len(a) == 2 && numberValue(a[0], 1, maxInteger, true) && numberValue(a[1], 1, maxInteger, true)
			if valid {
				var lo, hi float64
				_ = json.Unmarshal(a[0], &lo)
				_ = json.Unmarshal(a[1], &hi)
				valid = lo <= hi
			}
		}
	case "change_provider":
		allowed["provider"] = true
		valid = numberValue(m["provider"], 0, maxInteger, true)
	case "change_provider_settings":
		allowed["provider_settings"] = true
		s, stringOK := textValue(m["provider_settings"], 8192)
		valid = stringOK && json.Valid([]byte(s))
	case "change_playlist":
		allowed["playlist"] = true
		s, stringOK := textValue(m["playlist"], 8192)
		u, parseErr := url.Parse(s)
		valid = stringOK && parseErr == nil && (u.Scheme == "http" || u.Scheme == "https") && u.Hostname() != ""
	case "set_volume":
		allowed["volume"], allowed["volume_step"] = true, true
		v, hasVolume := m["volume"]
		s, hasStep := m["volume_step"]
		valid = hasVolume != hasStep && ((hasVolume && numberValue(v, 0, 100, false)) || (hasStep && numberValue(s, -math.MaxFloat64, math.MaxFloat64, false)))
	case "exit_player":
		valid = true
	default:
		return nil, errors.New("unsupported command")
	}
	for key := range m {
		if !allowed[key] {
			return nil, errors.New("unexpected command field")
		}
	}
	if !valid {
		return nil, errors.New("invalid command value")
	}
	return m, nil
}
