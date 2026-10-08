package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"image"
	"image/png"
	"io"
	"math"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Accept exact keys and reject duplicate keys before executing any operation.
func params(raw []byte) (map[string]json.RawMessage, error) {
	d := json.NewDecoder(bytes.NewReader(raw))
	t, e := d.Token()
	if e != nil || t != json.Delim('{') {
		return nil, errors.New("invalid parameters")
	}
	m := map[string]json.RawMessage{}
	for d.More() {
		t, e = d.Token()
		if e != nil {
			return nil, e
		}
		k, ok := t.(string)
		if !ok {
			return nil, errors.New("invalid key")
		}
		if _, exists := m[k]; exists {
			return nil, errors.New("duplicate key")
		}
		var v json.RawMessage
		if e = d.Decode(&v); e != nil {
			return nil, e
		}
		m[k] = v
	}
	if _, e = d.Token(); e != nil {
		return nil, e
	}
	if d.Decode(new(any)) != io.EOF {
		return nil, errors.New("trailing data")
	}
	return m, nil
}
func stringParam(p map[string]json.RawMessage, key string) string {
	var s string
	_ = json.Unmarshal(p[key], &s)
	return s
}
func (a *Agent) capabilities() map[string]any {
	return map[string]any{"version": 1, "player": map[string]any{"version": version, "platform": "android-agent", "runtime": a.runtime}, "lifecycle": []string{"restart_app", "reload_player", "reboot_device", "wake", "standby"}, "input": []string{}, "playback": []string{"pause", "resume", "seek"}, "screenshot": map[string]any{"state": "ready", "source": "display"}}
}
func (a *Agent) execute(ctx context.Context, r Request) (Result, func() error) {
	reject := func() (Result, func() error) { return rejected(r.ID, "Invalid or unsupported native operation"), nil }
	p, e := params(r.Params)
	if e != nil {
		return reject()
	}
	op := stringParam(p, "operation")
	switch r.Action {
	case "capabilities":
		if len(p) == 0 {
			return ok(r.ID, a.capabilities()), nil
		}
	case "screenshot":
		if len(p) != 1 || stringParam(p, "runtime") != a.runtime {
			return reject()
		}
		data, e := a.screenshot(ctx)
		if e != nil {
			return rejected(r.ID, "Display capture unavailable"), nil
		}
		return ok(r.ID, data), nil
	case "maintenance":
		if op == "update" {
			if len(p) != 3 {
				return reject()
			}
			effect, e := a.prepareUpdate(ctx, r.ID, stringParam(p, "manifest"), stringParam(p, "sha256"))
			if e != nil {
				return rejected(r.ID, "Signed update verification or download failed"), nil
			}
			return ok(r.ID, map[string]any{"operation": op, "accepted": true, "completion": "inspect_status"}), effect
		}
		if len(p) != 1 {
			return reject()
		}
		switch op {
		case "health":
			return ok(r.ID, a.health(ctx)), nil
		case "logs":
			events := a.logs
			if events == nil {
				events = []map[string]any{}
			}
			return ok(r.ID, map[string]any{"version": 1, "runtime": a.runtime, "boot_id": a.bootID, "events": events}), nil
		case "recover_video":
			a.watchdog.LastAction = time.Now()
			data, e := a.player(ctx, "recover", nil)
			if e != nil {
				return rejected(r.ID, "Video recovery unavailable; use android restart"), nil
			}
			data["operation"] = op
			return ok(r.ID, data), nil
		}
	case "lifecycle":
		if len(p) != 1 {
			return reject()
		}
		switch op {
		case "restart_app", "reload_player", "reboot_device", "wake", "standby":
			return ok(r.ID, map[string]any{"operation": op, "accepted": true, "completion": "inspect_status"}), func() error { return a.lifecycle(op) }
		}
	case "playback":
		if op != "pause" && op != "resume" && op != "seek" {
			return reject()
		}
		if op == "seek" {
			var n *float64
			if len(p) != 2 || json.Unmarshal(p["position"], &n) != nil || n == nil || math.IsNaN(*n) || math.IsInf(*n, 0) || *n < 0 || *n > 9007199254740991 {
				return reject()
			}
		} else if len(p) != 1 {
			return reject()
		}
		// Persist explicit pause intent before the player operation, so its watchdog
		// cannot undo a user pause after an agent restart.
		old := a.suspended
		if op == "pause" || op == "resume" {
			if a.suspend(op == "pause") != nil {
				return rejected(r.ID, "Cannot save playback intent"), nil
			}
		}
		data, e := a.player(ctx, "playback", p)
		if e != nil {
			_ = a.suspend(old)
			return rejected(r.ID, "Playback operation unconfirmed; inspect status before retrying"), nil
		}
		return ok(r.ID, data), nil
	case "vportal_queue":
		if op == "play" {
			var ids []int64
			var loop *bool
			if len(p) != 3 || json.Unmarshal(p["ids"], &ids) != nil || len(ids) < 1 || len(ids) > 100 || json.Unmarshal(p["loop"], &loop) != nil || loop == nil || !*loop {
				return reject()
			}
			seen := map[int64]bool{}
			for _, id := range ids {
				if id < 1 || id > 9007199254740991 || seen[id] {
					return reject()
				}
				seen[id] = true
			}
		} else {
			if len(p) != 1 {
				return reject()
			}
			switch op {
			case "status", "next", "previous", "restart", "stop":
			default:
				return reject()
			}
		}
		data, e := a.player(ctx, "vportal_queue", p)
		if e != nil {
			return rejected(r.ID, "Queue operation unconfirmed; inspect Android status before retrying"), nil
		}
		if op != "status" {
			if a.suspend(op == "stop") != nil {
				return rejected(r.ID, "Queue changed but watchdog intent was not saved; inspect status"), nil
			}
			a.watchdog.Progress = time.Now()
		}
		data["operation"] = op
		return ok(r.ID, data), nil
	}
	return reject()
}
func (a *Agent) suspend(value bool) error {
	if e := atomicJSON(dataDir+"/suspended.json", value); e != nil {
		return e
	}
	a.suspended = value
	return nil
}
func (a *Agent) lifecycle(op string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	a.watchdog.LastAction = time.Now()
	a.watchdog.Progress = time.Now()
	switch op {
	case "restart_app":
		if _, e := runCommand(ctx, "/system/bin/am", "force-stop", packageName); e != nil {
			return e
		}
		_, e := runCommand(ctx, "/system/bin/am", "start", "-n", packageName+"/.MainActivity")
		return e
	case "reload_player":
		_, e := a.cdp.evaluate(ctx, "(function(){location.reload();return true;})()")
		return e
	case "reboot_device":
		_, e := runCommand(ctx, "/system/bin/reboot")
		return e
	case "wake", "standby":
		if e := a.suspend(op == "standby"); e != nil {
			return e
		}
		key := "224"
		if op == "standby" {
			key = "223"
		}
		_, e := runCommand(ctx, "/system/bin/input", "keyevent", key)
		return e
	}
	return errors.New("unsupported lifecycle")
}
func numericFile(path string) float64 {
	b, e := os.ReadFile(path)
	if e != nil {
		return 0
	}
	fields := strings.Fields(string(b))
	if len(fields) == 0 {
		return 0
	}
	n, _ := strconv.ParseFloat(fields[0], 64)
	return n
}
func readBootID() string {
	b, _ := os.ReadFile("/proc/sys/kernel/random/boot_id")
	s := strings.TrimSpace(string(b))
	if !regexp.MustCompile(`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`).MatchString(s) {
		return ""
	}
	return s
}
func (a *Agent) health(ctx context.Context) map[string]any {
	pid, _ := appPID()
	h := map[string]any{"version": 1, "agent_version": version, "runtime": a.runtime, "boot_id": a.bootID, "app_pid": pid, "uptime_seconds": numericFile("/proc/uptime"), "battery_percent": numericFile("/sys/class/power_supply/battery/capacity"), "watchdog_suspended": a.suspended, "watchdog_attempts": a.watchdog.Attempts, "last_operation": a.lastOperation}
	history := a.operationHistory()
	h["operations"] = history
	if len(history) > 0 {
		h["last_operation"] = history[len(history)-1]
	}
	child, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()
	p, e := a.player(child, "health", nil)
	h["webview_responsive"] = e == nil
	if e == nil {
		h["player"] = p
	}
	h["system_evidence"] = systemEvidence(ctx, pid)
	if after, _ := appPID(); after != pid {
		h["system_evidence"] = map[string]any{"surface": unavailableEvidence("process_changed"), "audio": unavailableEvidence("process_changed")}
	}
	return h
}
func (a *Agent) screenshot(ctx context.Context) (map[string]any, error) {
	cmd := exec.CommandContext(ctx, "/system/bin/screencap", "-p")
	pipe, e := cmd.StdoutPipe()
	if e != nil {
		return nil, e
	}
	if e = cmd.Start(); e != nil {
		return nil, e
	}
	raw, e := io.ReadAll(io.LimitReader(pipe, 8*1024*1024+1))
	if e != nil || len(raw) > 8*1024*1024 {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		return nil, errors.New("capture too large")
	}
	if e = cmd.Wait(); e != nil {
		return nil, e
	}
	img, e := boundedImage(raw)
	if e != nil {
		return nil, e
	}
	return map[string]any{"version": 1, "runtime": a.runtime, "mime": "image/png", "encoding": "base64", "image": base64.StdEncoding.EncodeToString(img.data), "width": img.width, "height": img.height, "captured_at": time.Now().UnixMilli(), "source": "display", "video": "unknown"}, nil
}

type scaledImage struct {
	data          []byte
	width, height int
}

func boundedImage(raw []byte) (scaledImage, error) {
	var result scaledImage
	c, e := png.DecodeConfig(bytes.NewReader(raw))
	if e != nil || c.Width < 1 || c.Height < 1 || c.Width > 4096 || c.Height > 4096 {
		return result, errors.New("invalid capture dimensions")
	}
	src, e := png.Decode(bytes.NewReader(raw))
	if e != nil {
		return result, e
	}
	w, h := c.Width, c.Height
	if h > 720 {
		w = w * 720 / h
		h = 720
	}
	if w > 1280 {
		h = h * 1280 / w
		w = 1280
	}
	if w < 1 || h < 1 {
		return result, errors.New("invalid scaled dimensions")
	}
	for {
		dst := image.NewRGBA(image.Rect(0, 0, w, h))
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				dst.Set(x, y, src.At(x*c.Width/w, y*c.Height/h))
			}
		}
		var b bytes.Buffer
		if e = png.Encode(&b, dst); e != nil {
			return result, e
		}
		if b.Len() <= 1024*1024 {
			return scaledImage{b.Bytes(), w, h}, nil
		}
		w = w * 3 / 4
		h = h * 3 / 4
		if w < 160 || h < 90 {
			return result, errors.New("capture exceeds upload limit")
		}
	}
}
