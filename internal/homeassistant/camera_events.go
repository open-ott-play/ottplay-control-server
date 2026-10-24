package homeassistant

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"math"
	"regexp"
	"strconv"
	"sync"
	"time"
)

type cameraJob struct {
	key          string
	notification Notification
}

var cameraEventID = regexp.MustCompile(`^[A-Za-z0-9_.:-]{8,128}$`)
var cameraEventTimeString = regexp.MustCompile(`^[0-9]{1,13}(\.[0-9]{1,9})?$`)

func cameraEventTime(raw json.RawMessage) (float64, bool) {
	var value float64
	if len(raw) > 0 && raw[0] == '"' {
		var text string
		if len(raw) > 25 || json.Unmarshal(raw, &text) != nil || !cameraEventTimeString.MatchString(text) {
			return 0, false
		}
		var err error
		value, err = strconv.ParseFloat(text, 64)
		if err != nil {
			return 0, false
		}
	} else if json.Unmarshal(raw, &value) != nil {
		return 0, false
	}
	return value, !math.IsNaN(value) && !math.IsInf(value, 0) && value > 0
}

// SetCameraTargets must be called before Run. Callbacks return fresh snapshots;
// the control server rechecks them again before enqueue and before media read.
func (b *Bridge) SetCameraTargets(all, media func(Notification) map[string]string) {
	b.cameraTargets, b.cameraMediaTargets = all, media
}

func (b *Bridge) startCameraWorkers(ctx context.Context) func() {
	ctx, cancel := context.WithCancel(ctx)
	var workers sync.WaitGroup
	for range 2 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for {
				select {
				case <-ctx.Done():
					return
				case job := <-b.cameraQueue:
					if ctx.Err() != nil || !b.now().Before(job.notification.ExpiresAt) {
						continue
					}
					eligible := false
					if b.cameraMediaTargets != nil {
						for id, runtime := range b.cameraMediaTargets(job.notification) {
							if job.notification.MediaTargetRuntimes[id] == runtime {
								eligible = true
								break
							}
						}
					}
					if !eligible {
						b.mu.Lock()
						b.status.CameraSkipped++
						b.mu.Unlock()
						b.deliverCamera(job.notification)
						continue
					}
					captureCtx, cancel := context.WithDeadline(ctx, job.notification.ExpiresAt)
					frame, err := b.cameraFetch(captureCtx, b.config, job.key)
					cancel()
					if ctx.Err() != nil {
						return
					}
					b.mu.Lock()
					if err != nil {
						b.status.CameraFailed++
					} else {
						b.status.CameraAcquired++
					}
					b.mu.Unlock()
					if !b.now().Before(job.notification.ExpiresAt) {
						continue
					}
					job.notification.Media = frame
					b.deliverCamera(job.notification)
				}
			}
		}()
	}
	return func() { cancel(); workers.Wait() }
}

func (b *Bridge) deliverCamera(n Notification) {
	result := DeliveryResult{}
	if b.deliver != nil {
		result = b.deliver(n)
	}
	b.mu.Lock()
	b.status.NotificationsQueued += uint64(max(0, result.Queued))
	b.status.NotificationsSkipped += uint64(max(0, result.Skipped))
	b.mu.Unlock()
}

func (b *Bridge) processCameraEvent(ev event, now time.Time) {
	var data struct {
		CameraKey string          `json:"camera_key"`
		EventID   string          `json:"event_id"`
		EventAt   json.RawMessage `json:"event_at"`
	}
	if json.Unmarshal(ev.Data, &data) != nil || !cameraEventID.MatchString(data.EventID) {
		b.dropped()
		return
	}
	eventAt, validTime := cameraEventTime(data.EventAt)
	if !validTime {
		b.dropped()
		return
	}
	camera, ok := b.config.Cameras[data.CameraKey]
	age := float64(now.UnixNano())/1e9 - eventAt
	if !ok || age > 90 || age < -5 {
		b.dropped()
		return
	}
	// ID is the HA context+camera suffix, not a Frigate event ID. Hash only;
	// re-emitting the same event with a changed timestamp must not replay it.
	key := sha256.Sum256([]byte("camera\x00" + data.CameraKey + "\x00" + data.EventID))
	for k, expiry := range b.cameraSeen {
		if !now.Before(expiry) {
			delete(b.cameraSeen, k)
		}
	}
	if _, ok := b.cameraSeen[key]; ok {
		b.dropped()
		return
	}
	if len(b.cameraSeen) >= 1024 {
		b.dropped()
		return
	}
	b.cameraSeen[key] = now.Add(90 * time.Second)
	matched, attempted := false, false
	for _, rule := range b.config.Rules {
		if rule.EventType != ev.EventType || rule.CameraKey != data.CameraKey {
			continue
		}
		matched = true
		cooldownKey := rule.ID + "\x00" + data.CameraKey
		if now.Before(b.cooldown[cooldownKey]) {
			continue
		}
		if b.rateStart.IsZero() || now.Sub(b.rateStart) >= time.Minute {
			b.rateStart, b.rateUsed = now, 0
		}
		if b.rateUsed >= b.config.MaxPerMinute {
			continue
		}
		n := render(rule, map[string]string{"name": camera.Label, "entity_id": camera.EntityID, "event_type": ev.EventType, "state": "on"})
		if n.Title == "" || n.Message == "" {
			continue
		}
		b.cooldown[cooldownKey] = now.Add(time.Duration(rule.CooldownSeconds) * time.Second)
		b.rateUsed++
		attempted = true
		n.TargetRuntimes = map[string]string{}
		n.MediaTargetRuntimes = map[string]string{}
		if b.cameraTargets != nil {
			n.TargetRuntimes = b.cameraTargets(n)
		}
		if b.cameraMediaTargets != nil {
			n.MediaTargetRuntimes = b.cameraMediaTargets(n)
		}
		// Normalize nil callback responses to explicit empty sets, never broadcast.
		if n.TargetRuntimes == nil {
			n.TargetRuntimes = map[string]string{}
		}
		if n.MediaTargetRuntimes == nil {
			n.MediaTargetRuntimes = map[string]string{}
		}
		for id, runtime := range n.MediaTargetRuntimes {
			if n.TargetRuntimes[id] != runtime {
				delete(n.MediaTargetRuntimes, id)
			}
		}
		n.ExpiresAt = now.Add(time.Duration(n.TTLSeconds) * time.Second)
		originalDeadline := time.Unix(0, int64(eventAt*1e9)).Add(90 * time.Second)
		if originalDeadline.Before(n.ExpiresAt) {
			n.ExpiresAt = originalDeadline
		}
		if len(n.TargetRuntimes) == 0 {
			b.mu.Lock()
			b.status.CameraSkipped++
			b.mu.Unlock()
			continue
		}
		if len(n.MediaTargetRuntimes) == 0 {
			b.deliverCamera(n)
			continue
		}
		select {
		case b.cameraQueue <- cameraJob{data.CameraKey, n}:
		default:
			b.mu.Lock()
			b.status.CameraSkipped++
			b.mu.Unlock()
			b.deliverCamera(n)
		}
	}
	b.mu.Lock()
	if matched {
		b.status.EventsMatched++
	}
	if !attempted {
		b.status.EventsDropped++
	}
	b.mu.Unlock()
}
