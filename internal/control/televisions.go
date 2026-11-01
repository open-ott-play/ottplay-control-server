package control

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/television"
)

type televisionControl interface {
	Binding(string) television.Binding
	Execute(context.Context, string, television.Command) (television.Result, error)
}

type televisionRequest struct {
	DeviceID  string `json:"device_id"`
	Action    string `json:"action"`
	App       string `json:"app,omitempty"`
	Volume    *int   `json:"volume,omitempty"`
	Delta     *int   `json:"delta,omitempty"`
	Muted     *bool  `json:"muted,omitempty"`
	TimeoutMS *int   `json:"timeout_ms,omitempty"`
}

// Physical TV control is an administrator-only lane. It does not depend on a
// polling player and does not expand that player's token or advertised RPCs.
// The normal registry admission gate covers this bounded request, so a revoke
// cannot confirm before an already-admitted physical command finishes.
func (s *Server) serveTelevisions(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/api/televisions" || r.URL.RawPath != "" {
		failure(w, 400, "invalid television path")
		return
	}
	q, ok := query(r, "device_id")
	if !ok {
		failure(w, 400, "invalid television query")
		return
	}
	id := q.Get("device_id")
	var body televisionRequest
	if r.Method == http.MethodPost {
		if len(q) != 0 {
			failure(w, 400, "POST television parameters belong in the JSON body")
			return
		}
		data, ok := readLimitedBody(w, r, 2048)
		if !ok {
			return
		}
		if !diagStrictJSON(data) || !diagDecode(data, &body, []string{"device_id", "action"}, "app", "volume", "delta", "muted", "timeout_ms") {
			failure(w, 400, "invalid television command")
			return
		}
		id = body.DeviceID
	} else if r.ContentLength != 0 || len(r.TransferEncoding) != 0 {
		failure(w, 400, "GET television metadata must not contain a body")
		return
	}
	known := false
	for _, d := range s.currentDevices() {
		if d.id == id {
			known = true
			break
		}
	}
	if !known {
		failure(w, 404, "an active player device_id is required")
		return
	}
	if r.Method == http.MethodGet {
		reply(w, 200, s.televisions.Binding(id))
		return
	}
	command := television.Command{Action: body.Action, App: body.App, Volume: body.Volume, Delta: body.Delta, Muted: body.Muted}
	if command.Validate() != nil || (body.TimeoutMS != nil && (*body.TimeoutMS < 1 || *body.TimeoutMS > 120000)) {
		failure(w, 400, "invalid television command parameters")
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
	budget := 10 * time.Second
	if body.TimeoutMS != nil {
		budget = time.Duration(*body.TimeoutMS) * time.Millisecond
	}
	// Only this authenticated, capacity-limited lane may exceed the server's
	// ordinary ten-second write deadline (pairing and cold boot take longer).
	_ = http.NewResponseController(w).SetWriteDeadline(time.Now().Add(budget + time.Second))
	ctx, cancel := context.WithTimeout(r.Context(), budget)
	defer cancel()
	result, err := s.televisions.Execute(ctx, id, command)
	if err != nil {
		readOnly := command.Action == "status" || command.Action == "apps" || (command.Action == "volume" && command.Volume == nil && command.Delta == nil)
		code, uncertain := "operation_failed", !readOnly
		var tvError *television.Error
		if errors.As(err, &tvError) {
			code, uncertain = tvError.Code, tvError.Uncertain
		}
		reply(w, 200, map[string]any{"version": 1, "device_id": id, "action": body.Action, "code": code, "uncertain": uncertain})
		return
	}
	reply(w, 200, result)
}
