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

// URL parsing stays native; generated wire policy owns when it is required.
func wireHTTPURL(value string) bool {
	u, err := url.Parse(value)
	return err == nil && (u.Scheme == "http" || u.Scheme == "https") && u.Hostname() != ""
}
