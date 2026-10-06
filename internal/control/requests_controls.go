package control

import "encoding/json"

// These modern RPC operations deliberately do not expand the legacy command
// envelope. The player owns platform capabilities, parental/kiosk admission and
// acknowledgement-before-effect ordering; the server rejects ambiguous payloads.
func validControlRequest(action string, params map[string]json.RawMessage) bool {
	var value string
	field := "operation"
	if action == "input" {
		field = "key"
	}
	if json.Unmarshal(params[field], &value) != nil {
		return false
	}
	switch action {
	case "lifecycle":
		if len(params) != 1 {
			return false
		}
		switch value {
		case "restart_stream", "reload_player", "restart_app", "standby", "wake", "exit_app", "reboot_device":
			return true
		}
	case "input":
		if len(params) != 1 {
			return false
		}
		switch value {
		case "up", "down", "left", "right", "ok", "back", "menu", "settings", "channels", "guide", "info",
			"channel_up", "channel_down", "volume_up", "volume_down", "mute", "play_pause", "audio", "aspect", "zoom", "pip", "fullscreen":
			return true
		}
	case "playback":
		switch value {
		case "pause", "resume", "previous_channel", "next_channel":
			return len(params) == 1
		case "seek":
			return len(params) == 2 && numberValue(params["position"], 0, 9007199254740991, false)
		}
	}
	return false
}
