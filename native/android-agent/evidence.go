package main

import (
	"context"
	"errors"
	"io"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// Only fixed, read-only services are queried. Raw dumps may contain other apps'
// names and are never retained, returned, or logged.
func boundedDump(ctx context.Context, args ...string) ([]byte, error) {
	child, cancel := context.WithTimeout(ctx, 1200*time.Millisecond)
	defer cancel()
	cmd := exec.CommandContext(child, "/system/bin/dumpsys", args...)
	pipe, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err = cmd.Start(); err != nil {
		return nil, err
	}
	b, err := io.ReadAll(io.LimitReader(pipe, 256*1024+1))
	if err != nil || len(b) > 256*1024 {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		return nil, errors.New("bounded dump failed")
	}
	if err = cmd.Wait(); err != nil {
		return nil, err
	}
	return b, nil
}
func unavailableEvidence(reason string) map[string]any {
	return map[string]any{"state": "unavailable", "reason": reason}
}
func ownSurface(raw []byte) string {
	name := ""
	// KitKat names the Activity layer with its full component. A SurfaceView or
	// vendor video plane without that identity is not guessed to belong to us.
	want := packageName + "/" + packageName + ".MainActivity"
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == want {
			if name != "" {
				return ""
			}
			name = line
		}
	}
	return name
}
func surfaceEvidence(raw []byte) map[string]any {
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) < 2 {
		return unavailableEvidence("no_frame_timestamps")
	}
	period, err := strconv.ParseInt(strings.TrimSpace(lines[0]), 10, 64)
	if err != nil || period <= 0 || period > 1000000000 {
		return unavailableEvidence("unsupported_dump")
	}
	var latest int64
	count := 0
	for _, line := range lines[1:] {
		f := strings.Fields(line)
		if len(f) == 0 {
			continue
		}
		if len(f) != 3 {
			return unavailableEvidence("unsupported_dump")
		}
		// Column two is actualPresentTime in AOSP 4.4 FrameTracker. INT64_MAX is
		// a pending fence. Zero and invalid fences provide no presentation evidence.
		values := [3]int64{}
		for i := range f {
			n, e := strconv.ParseInt(f[i], 10, 64)
			if e != nil || n < 0 {
				return unavailableEvidence("unsupported_dump")
			}
			values[i] = n
		}
		n := values[1]
		if n > 0 && n < 9007199254740991 {
			count++
			if n > latest {
				latest = n
			}
		}
	}
	if count == 0 {
		return unavailableEvidence("no_completed_fences")
	}
	return map[string]any{"state": "observed", "scope": "app_surface", "period_ns": period, "latest_present_ns": latest, "completed_frames": count, "video_verified": false}
}
func audioEvidence(raw []byte, pid int) map[string]any {
	active, header, recognized := false, false, false
	tracks := []map[string]any{}
	for _, line := range strings.Split(string(raw), "\n") {
		s := strings.TrimSpace(line)
		if strings.HasPrefix(s, "Output thread ") {
			active = strings.HasSuffix(s, " active tracks")
			header = false
			continue
		}
		if strings.HasPrefix(s, "Name Client Type") {
			header = active && strings.Contains(s, "Session fCount S F SRate") && strings.Contains(s, "UndFrmCnt")
			recognized = recognized || header
			continue
		}
		if !active || !header {
			continue
		}
		f := strings.Fields(s)
		if len(f) > 0 && f[0] == "F" {
			f = f[1:]
		}
		if len(f) != 17 {
			continue
		}
		client, err := strconv.Atoi(f[1])
		if err != nil || client != pid {
			continue
		}
		session, e1 := strconv.ParseUint(f[5], 10, 32)
		rate, e2 := strconv.ParseUint(f[9], 10, 32)
		frames, e3 := strconv.ParseUint(f[12], 16, 32)
		underruns, e4 := strconv.ParseUint(strings.TrimRight(f[16], "<*?"), 10, 32)
		if e1 != nil || e2 != nil || e3 != nil || e4 != nil || len(f[7]) != 1 {
			continue
		}
		if len(tracks) >= 16 {
			return unavailableEvidence("too_many_tracks")
		}
		tracks = append(tracks, map[string]any{"session_id": session, "sample_rate": rate, "server_frames": frames, "underrun_frames": underruns, "active": f[7] == "A" || f[7] == "R"})
	}
	if !recognized {
		return unavailableEvidence("unsupported_dump")
	}
	if len(tracks) == 0 {
		return unavailableEvidence("no_matching_audio_track")
	}
	return map[string]any{"state": "observed", "scope": "app_process", "tracks": tracks, "audible_verified": false}
}
func systemEvidence(ctx context.Context, pid int) map[string]any {
	// A blocked service must not consume the response deadline or starve native
	// status when the WebView already spent its own responsiveness budget.
	budget := 1800 * time.Millisecond
	if deadline, ok := ctx.Deadline(); ok {
		if left := time.Until(deadline) - 500*time.Millisecond; left < budget {
			budget = left
		}
	}
	ctx, cancel := context.WithTimeout(ctx, budget)
	defer cancel()
	surface := unavailableEvidence("app_surface_unavailable")
	audio := unavailableEvidence("audio_service_unavailable")
	if pid > 0 {
		if b, e := boundedDump(ctx, "SurfaceFlinger", "--list"); e == nil {
			if name := ownSurface(b); name != "" {
				if b, e = boundedDump(ctx, "SurfaceFlinger", "--latency", name); e == nil {
					surface = surfaceEvidence(b)
				}
			}
		}
		if b, e := boundedDump(ctx, "media.audio_flinger"); e == nil {
			audio = audioEvidence(b, pid)
		}
	}
	return map[string]any{"surface": surface, "audio": audio, "app_pid": pid, "captured_uptime_seconds": numericFile("/proc/uptime"), "physical_display_verified": false, "physical_audio_verified": false}
}
