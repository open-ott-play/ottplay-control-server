package control

import (
	"testing"
	"time"
)

func TestIdempotencyRetentionCoversPreciseQueueDeadline(t *testing.T) {
	for _, nanos := range []time.Duration{0, time.Millisecond, 900 * time.Millisecond, time.Second - time.Nanosecond} {
		c := journalConfig(t)
		c.CommandTTLSeconds = 60
		c.Journal.RetentionSeconds = 60
		s, err := New(c)
		if err != nil {
			t.Fatal(err)
		}
		now := time.Now().Truncate(time.Second).Add(nanos)
		s.now = func() time.Time { return now }
		body := `{"action":"playback","params":{"operation":"next_channel"},"idempotency_key":"boundary-retain-0001"}`
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
		original := s.devices[0].queue[0]
		now = original.expires.Add(-time.Nanosecond)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
		if len(s.devices[0].queue) != 1 || s.devices[0].queue[0].id != original.id {
			t.Fatal("key expired before original command deadline")
		}
		s.Close()
	}
}

func TestCanonicalResponseRoutesNeverGrantDeviceSubmissionAuthority(t *testing.T) {
	for _, path := range []string{"/api/responses", "/api/responses/", "/api/responses//", "/api/responses///", "/api/responses%2f%2f"} {
		t.Run(path, func(t *testing.T) {
			s := newTestServer(t)
			w := request(s, "POST", path, firstToken, `{"action":"playback","params":{"operation":"next_channel"}}`, nil)
			expect(t, w, 400)
			if len(s.devices[0].queue) != 0 {
				t.Fatal("device submitted an RPC")
			}
			id := rpcID(t, s, `{"action":"status","params":{}}`)
			expect(t, request(s, "POST", path, firstToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 200)
		})
	}
}
