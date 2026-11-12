package television

import (
	"context"
	"encoding/json"
	"regexp"
	"strings"
)

var channelPermissions = []string{"READ_TV_CHANNEL_LIST", "CONTROL_INPUT_TV", "READ_CURRENT_CHANNEL"}
var channelNumberPattern = regexp.MustCompile(`^[0-9]{1,8}([.-][0-9]{1,8})?$`)

// Channel identifies a system-owned source. It deliberately has no playable URL.
type Channel struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Number string `json:"number"`
	Mode   string `json:"mode,omitempty"`
}

type Program struct {
	ID          string `json:"id,omitempty"`
	Title       string `json:"title,omitempty"`
	Description string `json:"description,omitempty"`
	StartTime   string `json:"start_time,omitempty"`
	EndTime     string `json:"end_time,omitempty"`
	ChannelID   string `json:"channel_id,omitempty"`
}

func isChannelAction(action string) bool {
	switch action {
	case "channels", "channel_current", "channel_program", "channel_select":
		return true
	}
	return false
}

func boundedText(value string, limit int) bool {
	if len(value) > limit {
		return false
	}
	for _, ch := range value {
		if ch < 32 || ch == 127 {
			return false
		}
	}
	return true
}

func (c Command) validateChannels() error {
	if !boundedText(c.ChannelID, 512) {
		return failure("invalid_command")
	}
	switch c.Action {
	case "channel_select":
		if (c.ChannelID == "") == (c.ChannelNumber == "") || (c.ChannelID != "" && strings.TrimSpace(c.ChannelID) == "") {
			return failure("invalid_command")
		}
		if c.ChannelNumber != "" {
			if !channelNumberPattern.MatchString(c.ChannelNumber) || c.ChannelMode != "Terrestrial" {
				return failure("invalid_command")
			}
		} else if c.ChannelMode != "" {
			return failure("invalid_command")
		}
	case "channel_program":
		if c.ChannelNumber != "" || c.ChannelMode != "" {
			return failure("invalid_command")
		}
	default:
		if c.ChannelID != "" || c.ChannelNumber != "" || c.ChannelMode != "" {
			return failure("invalid_command")
		}
	}
	return nil
}

func parseChannel(p map[string]json.RawMessage) (Channel, error) {
	c := Channel{ID: stringField(p, "channelId", 512), Name: stringField(p, "channelName", 512), Number: stringField(p, "channelNumber", 64), Mode: stringField(p, "channelMode", 64)}
	if strings.TrimSpace(c.ID) == "" {
		return c, failure("protocol_error")
	}
	return c, nil
}
func (s *session) currentChannel(ctx context.Context) (*Channel, error) {
	p, err := s.rpc(ctx, "tv/getCurrentChannel", nil, false)
	if err != nil {
		return nil, err
	}
	c, err := parseChannel(p)
	return &c, err
}

func (s *session) executeChannels(ctx context.Context, cmd Command, r *Result) error {
	switch cmd.Action {
	case "channels":
		p, err := s.rpc(ctx, "tv/getChannelList", nil, false)
		if err != nil {
			return err
		}
		var list []map[string]json.RawMessage
		if json.Unmarshal(p["channelList"], &list) != nil || list == nil || len(list) > 20000 {
			return failure("protocol_error")
		}
		r.Channels = make([]Channel, 0, len(list))
		seen := make(map[string]bool, len(list))
		for _, item := range list {
			c, err := parseChannel(item)
			if err != nil {
				return err
			}
			if seen[c.ID] {
				return failure("protocol_error")
			}
			seen[c.ID] = true
			r.Channels = append(r.Channels, c)
		}
		r.Outcome = "confirmed"
		return nil
	case "channel_current":
		c, err := s.currentChannel(ctx)
		if err != nil {
			return err
		}
		r.Channel = c
		r.Outcome = "confirmed"
		return nil
	case "channel_program":
		c, err := s.currentChannel(ctx)
		if err != nil {
			return err
		}
		if cmd.ChannelID != "" && cmd.ChannelID != c.ID {
			return failure("channel_not_current")
		}
		p, err := s.rpc(ctx, "tv/getChannelCurrentProgramInfo", nil, false)
		if err != nil {
			return err
		}
		observed, err := s.currentChannel(ctx)
		if err != nil {
			return err
		}
		if observed.ID != c.ID {
			return failure("channel_changed")
		}
		if id := stringField(p, "channelId", 512); id != "" && id != c.ID {
			return failure("channel_changed")
		}
		r.Channel = c
		r.Program = &Program{ID: stringField(p, "programId", 512), Title: stringField(p, "programName", 2048), Description: stringField(p, "description", 16384), StartTime: stringField(p, "startTime", 128), EndTime: stringField(p, "endTime", 128), ChannelID: c.ID}
		r.Outcome = "confirmed"
		return nil
	case "channel_select":
		payload := map[string]any{"channelId": cmd.ChannelID}
		if cmd.ChannelNumber != "" {
			payload = map[string]any{"channelNumber": cmd.ChannelNumber, "channelMode": cmd.ChannelMode}
		}
		if _, err := s.rpc(ctx, "tv/openChannel", payload, true); err != nil {
			return err
		}
		r.Outcome = "accepted"
		// RPC acceptance is not proof of tuning. Observe once, never replay a tune.
		if current, err := s.currentChannel(ctx); err == nil {
			r.Channel = current
			if (cmd.ChannelID != "" && current.ID == cmd.ChannelID) || (cmd.ChannelNumber != "" && current.Number == cmd.ChannelNumber && current.Mode == cmd.ChannelMode) {
				r.Outcome = "confirmed"
			}
		}
		return nil
	}
	return failure("invalid_command")
}
