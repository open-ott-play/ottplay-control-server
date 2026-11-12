package control

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/television"
)

const televisionChannelsPath = "/api/television-channels"

// This lane delegates channel operations only, to the authenticated player's own
// explicitly enabled physical-TV binding. It never accepts a target identity or
// SSAP URI, and cannot pair, launch applications, wake or power off a television.
func (s *Server) serveTelevisionChannels(w http.ResponseWriter, r *http.Request, d *device) {
	if d == nil || r.URL.Path != televisionChannelsPath || r.URL.RawPath != "" || r.URL.RawQuery != "" || r.URL.ForceQuery {
		failure(w, 400, "invalid player television path")
		return
	}
	binding := s.televisions.Binding(d.id)
	if !binding.Configured || !binding.PlayerChannelControl {
		failure(w, 403, "player channel control is not enabled for this device")
		return
	}
	var body struct {
		Action        string `json:"action"`
		ChannelID     string `json:"channel_id,omitempty"`
		ChannelNumber string `json:"channel_number,omitempty"`
		ChannelMode   string `json:"channel_mode,omitempty"`
		TimeoutMS     *int   `json:"timeout_ms,omitempty"`
	}
	data, ok := readLimitedBody(w, r, 2048)
	if !ok {
		return
	}
	if !diagStrictJSON(data) || !diagDecode(data, &body, []string{"action"}, "channel_id", "channel_number", "channel_mode", "timeout_ms") {
		failure(w, 400, "invalid channel command")
		return
	}
	switch body.Action {
	case "channels", "channel_current", "channel_program", "channel_select":
	default:
		failure(w, 400, "channel operation is not allowed")
		return
	}
	cmd := television.Command{Action: body.Action, ChannelID: body.ChannelID, ChannelNumber: body.ChannelNumber, ChannelMode: body.ChannelMode}
	if cmd.Validate() != nil || (body.TimeoutMS != nil && (*body.TimeoutMS < 1 || *body.TimeoutMS > 10000)) {
		failure(w, 400, "invalid channel command parameters")
		return
	}
	select {
	case s.televisionSlots <- struct{}{}:
		defer func() { <-s.televisionSlots }()
	default:
		w.Header().Set("Retry-After", "1")
		failure(w, 503, "television control is busy")
		return
	}
	budget := 8 * time.Second
	if body.TimeoutMS != nil {
		budget = time.Duration(*body.TimeoutMS) * time.Millisecond
	}
	_ = http.NewResponseController(w).SetWriteDeadline(time.Now().Add(budget + time.Second))
	ctx, cancel := context.WithTimeout(r.Context(), budget)
	defer cancel()
	result, err := s.televisions.Execute(ctx, d.id, cmd)
	if err != nil {
		code, uncertain := "operation_failed", cmd.Action == "channel_select"
		var tvError *television.Error
		if errors.As(err, &tvError) {
			code, uncertain = tvError.Code, tvError.Uncertain
		}
		reply(w, 200, map[string]any{"version": 1, "device_id": d.id, "action": cmd.Action, "code": code, "uncertain": uncertain})
		return
	}
	reply(w, 200, result)
}
