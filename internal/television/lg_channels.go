package television

// The public regional lineup is metadata, not evidence that a channel is
// installed or available on a particular TV. Playback stays owned by webOS.
import (
	"bytes"
	"compress/zlib"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const lgLineupURL = "https://api.lgchannels.com/api/v1.0/channellist?deviceType=All"
const lgLineupLimit = 2 << 20
const lgFASTPrefix = "lgfast:US:"

type lgLineupCache struct {
	mu             sync.Mutex
	rows           []Channel
	expires, retry time.Time
	gate           chan struct{}
	client         *http.Client
}

func newLGLineupCache() *lgLineupCache {
	return &lgLineupCache{gate: make(chan struct{}, 1), client: &http.Client{Timeout: 4 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
}
func decodeLGLineup(body []byte) ([]Channel, error) {
	body = bytes.TrimSpace(body)
	if len(body) == 0 || len(body) > lgLineupLimit {
		return nil, failure("lg_channels_unavailable")
	}
	if body[0] != '{' {
		if body[0] == '"' {
			var text string
			if json.Unmarshal(body, &text) != nil {
				return nil, failure("lg_channels_unavailable")
			}
			body = []byte(text)
		}
		compressed, err := base64.StdEncoding.DecodeString(string(body))
		if err != nil {
			return nil, failure("lg_channels_unavailable")
		}
		r, err := zlib.NewReader(bytes.NewReader(compressed))
		if err != nil {
			return nil, failure("lg_channels_unavailable")
		}
		body, err = io.ReadAll(io.LimitReader(r, lgLineupLimit+1))
		_ = r.Close()
		if err != nil || len(body) > lgLineupLimit {
			return nil, failure("lg_channels_unavailable")
		}
	}
	var data struct {
		Genres []struct {
			Genre    string `json:"genre"`
			Channels []struct {
				Number string `json:"channelNumber"`
				Name   string `json:"channelName"`
			} `json:"channels"`
		} `json:"genres"`
	}
	if json.Unmarshal(body, &data) != nil || len(data.Genres) == 0 || len(data.Genres) > 128 {
		return nil, failure("lg_channels_unavailable")
	}
	var rows []Channel
	seen := map[string]bool{}
	for _, genre := range data.Genres {
		for _, c := range genre.Channels {
			if len(rows) >= 2000 || !lgFASTNumber(c.Number) || !boundedText(c.Name, 512) || strings.TrimSpace(c.Name) == "" || seen[c.Number] {
				return nil, failure("lg_channels_unavailable")
			}
			seen[c.Number] = true
			rows = append(rows, Channel{ID: lgFASTPrefix + c.Number, Name: c.Name, Number: c.Number, Mode: "LG Channels (US catalogue)", Source: "lg_channels"})
		}
	}
	if len(rows) == 0 {
		return nil, failure("lg_channels_unavailable")
	}
	return rows, nil
}
func lgFASTNumber(s string) bool {
	if len(s) == 0 || len(s) > 8 {
		return false
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}
func (c *lgLineupCache) get(ctx context.Context) ([]Channel, string) {
	ctx, cancel := context.WithTimeout(ctx, 4*time.Second)
	defer cancel()
	select {
	case c.gate <- struct{}{}:
		defer func() { <-c.gate }()
	case <-ctx.Done():
		return nil, "LG Channels regional catalogue unavailable; tuner channels remain available"
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	now := time.Now()
	if now.Before(c.expires) {
		return append([]Channel(nil), c.rows...), ""
	}
	warning := "LG Channels regional catalogue unavailable; tuner channels remain available"
	if now.After(c.retry) {
		c.retry = now.Add(time.Minute)
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, lgLineupURL, nil)
		req.Header.Set("X-Device-Country", "US")
		req.Header.Set("X-Device-Language", "en")
		response, err := c.client.Do(req)
		if err == nil {
			body, readErr := io.ReadAll(io.LimitReader(response.Body, lgLineupLimit+1))
			_ = response.Body.Close()
			if response.StatusCode == 200 && readErr == nil {
				if rows, decodeErr := decodeLGLineup(body); decodeErr == nil {
					c.rows = rows
					c.expires = now.Add(6 * time.Hour)
					return append([]Channel(nil), rows...), ""
				}
			}
		}
	}
	if len(c.rows) > 0 {
		warning = "LG Channels regional catalogue is stale metadata; availability on this TV is unverified"
	}
	rows := append([]Channel(nil), c.rows...)
	for i := range rows {
		rows[i].Mode = "LG Channels (US stale catalogue)"
	}
	return rows, warning
}
func (m *Manager) executeRegionalChannels(ctx context.Context, s *session, c config.Television, cmd Command, r *Result) error {
	logical := cmd.ChannelID
	if strings.HasPrefix(logical, "lgfast:") {
		if c.LGChannelsCountry != "US" || !strings.HasPrefix(logical, lgFASTPrefix) || !lgFASTNumber(strings.TrimPrefix(logical, lgFASTPrefix)) {
			return failure("channel_not_found")
		}
		rows, warning := m.lgLineup.get(ctx)
		if warning != "" {
			r.Warnings = append(r.Warnings, warning)
		}
		var match *Channel
		for i := range rows {
			if rows[i].ID == logical {
				match = &rows[i]
				break
			}
		}
		if match == nil {
			return failure("channel_not_found")
		}
		switch cmd.Action {
		case "channel_select":
			cmd.ChannelID = ""
			cmd.ChannelNumber = match.Number
			cmd.ChannelMode = "Terrestrial"
		case "channel_program":
			current, err := s.currentChannel(ctx)
			if err != nil {
				return err
			}
			if current.Number != match.Number || current.Mode != "Terrestrial" {
				return failure("channel_not_current")
			}
			cmd.ChannelID = current.ID
		default:
			return failure("invalid_command")
		}
		if err := s.executeChannels(ctx, cmd, r); err != nil {
			return err
		}
		if r.Channel != nil && r.Channel.Number == match.Number && r.Channel.Mode == "Terrestrial" {
			r.Channel.PhysicalID = r.Channel.ID
			r.Channel.ID = logical
			r.Channel.Source = "lg_channels"
			if r.Program != nil {
				r.Program.ChannelID = logical
			}
		}
		return nil
	}
	if err := s.executeChannels(ctx, cmd, r); err != nil {
		return err
	}
	if cmd.Action == "channels" && c.LGChannelsCountry == "US" {
		rows, warning := m.lgLineup.get(ctx)
		if warning != "" {
			r.Warnings = append(r.Warnings, warning)
		}
		seen := make(map[string]bool, len(r.Channels))
		for _, row := range r.Channels {
			seen[row.ID] = true
		}
		for _, row := range rows {
			if seen[row.ID] || len(r.Channels) >= 20000 {
				r.Warnings = append(r.Warnings, "LG Channels regional catalogue could not be appended completely")
				break
			}
			seen[row.ID] = true
			r.Channels = append(r.Channels, row)
		}
	}
	return nil
}
