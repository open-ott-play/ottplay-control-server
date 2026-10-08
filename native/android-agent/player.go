package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"time"
)

//go:embed player.js
var playerJS string

func (a *Agent) player(ctx context.Context, action string, params any) (map[string]any, error) {
	budget := 25000
	if deadline, ok := ctx.Deadline(); ok {
		n := int(time.Until(deadline).Milliseconds()) - 500
		if n < budget {
			budget = n
		}
	}
	if budget < 1 {
		return nil, errors.New("player deadline exceeded")
	}
	id := randomID()
	request, _ := json.Marshal(map[string]any{"action": action, "params": params, "id": id, "timeoutMs": budget})
	key, _ := json.Marshal(id)
	expression := `(function(){var id=` + string(key) + `;window.__ottNativeCall={id:id};(` + playerJS + `)(` + string(request) + `,function(r){if(window.__ottNativeCall&&window.__ottNativeCall.id===id)window.__ottNativeCall.result=r;});return true;})()`
	if _, e := a.cdp.evaluate(ctx, expression); e != nil {
		return nil, e
	}
	for {
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		raw, e := a.cdp.evaluate(ctx, `(function(){var r=window.__ottNativeCall;return r&&r.id===`+string(key)+`?r.result:null;})()`)
		if e != nil {
			return nil, e
		}
		if string(raw) != "null" {
			var reply struct {
				OK    bool           `json:"ok"`
				Data  map[string]any `json:"data"`
				Error string         `json:"error"`
			}
			if json.Unmarshal(raw, &reply) != nil {
				return nil, errors.New("invalid player response")
			}
			if !reply.OK {
				return nil, errors.New(reply.Error)
			}
			return reply.Data, nil
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(200 * time.Millisecond):
		}
	}
}
