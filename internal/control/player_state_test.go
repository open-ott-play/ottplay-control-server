package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func stateTestReport(s *Server, runtime string, seq int64) stateReport {
	title, provider, id := "Example", "plex", "12345"
	position, duration, volume := 100.5, 500.0, 50.0
	return stateReport{Version: 1, Epoch: s.playerStateEpoch, Runtime: runtime, Sequence: seq,
		Player:   statePlayer{Version: "1.1.62", Platform: "browser"},
		Playback: statePlayback{State: "playing", Kind: "vod", Title: &title, Provider: &provider, MediaID: &id, Position: &position, Duration: &duration, Volume: &volume}}
}
func stateTestJSON(t *testing.T, v any) string {
	t.Helper()
	b, e := json.Marshal(v)
	if e != nil {
		t.Fatal(e)
	}
	return string(b)
}
func stateTestSend(t *testing.T, s *Server, v stateReport, code int) *httptest.ResponseRecorder {
	t.Helper()
	w := request(s, "POST", playerStatePath, firstToken, stateTestJSON(t, v), nil)
	expect(t, w, code)
	return w
}

type stateTestView struct {
	Epoch           string `json:"controller_epoch"`
	Storage         string `json:"storage"`
	Current, Latest *stateRow
	Runtimes        []stateRow
	History         []stateEvent
	Ambiguous       bool
	Truncated       bool `json:"history_truncated"`
	Connection      struct {
		State    string
		LastSeen *time.Time `json:"last_seen_at"`
	}
}

func stateTestRead(t *testing.T, s *Server, limit int) stateTestView {
	t.Helper()
	w := request(s, "GET", fmt.Sprintf("%s?device_id=first&limit=%d", playerStatePath, limit), adminToken, "", nil)
	expect(t, w, 200)
	var v stateTestView
	if e := json.Unmarshal(w.Body.Bytes(), &v); e != nil {
		t.Fatal(e)
	}
	return v
}
func stateTestClock(s *Server) *time.Time {
	now := time.Now().UTC().Truncate(time.Millisecond)
	s.now = func() time.Time { return now }
	return &now
}

func TestPlayerStateReportFreshnessIdempotencyAndHistory(t *testing.T) {
	s := newTestServer(t)
	now := stateTestClock(s)
	v := stateTestReport(s, "page-a", 1)
	if got := stateTestRead(t, s, 64); got.Latest != nil || got.Connection.State != "unknown" {
		t.Fatal(got)
	}
	stateTestSend(t, s, v, 200)
	first := stateTestRead(t, s, 64)
	if first.Current == nil || len(first.History) != 1 || first.History[0].Event != "connected" || first.Connection.State != "online" {
		t.Fatal(first)
	}
	*now = now.Add(44 * time.Second)
	duplicate := stateTestSend(t, s, v, 200)
	if !strings.Contains(duplicate.Body.String(), `"duplicate":true`) {
		t.Fatal(duplicate.Body.String())
	}
	got := stateTestRead(t, s, 64)
	if got.Current.AgeMS != 44000 || !got.Connection.LastSeen.Equal(*first.Connection.LastSeen) || len(got.History) != 1 {
		t.Fatal(got)
	}
	*now = now.Add(time.Second)
	got = stateTestRead(t, s, 64)
	if got.Current != nil || !got.Latest.Stale || got.Connection.State != "offline" {
		t.Fatal(got)
	}
	v.Sequence = 2
	stateTestSend(t, s, v, 200)
	got = stateTestRead(t, s, 64)
	if got.Current == nil || len(got.History) != 2 || got.History[0].Event != "reconnected" {
		t.Fatal(got)
	}
	*now = now.Add(time.Second)
	v.Sequence = 3
	pos := 101.0
	v.Playback.Position = &pos
	stateTestSend(t, s, v, 200)
	got = stateTestRead(t, s, 64)
	if len(got.History) != 2 || got.Current.Sequence != 3 {
		t.Fatal(got)
	}
	conflict := v
	conflict.Playback.State = "paused"
	stateTestSend(t, s, conflict, 409)
	older := v
	older.Sequence = 2
	stateTestSend(t, s, older, 409)
	v.Sequence = 4
	v.Playback.State = "paused"
	stateTestSend(t, s, v, 200)
	got = stateTestRead(t, s, 1)
	if len(got.History) != 1 || !got.Truncated || got.History[0].Event != "changed" {
		t.Fatal(got)
	}
	wrong := v
	wrong.Epoch = strings.Repeat("f", 32)
	stateTestSend(t, s, wrong, 409)
}
func TestPlayerStateAmbiguityCapacityAndRetiredFence(t *testing.T) {
	s := newTestServer(t)
	now := stateTestClock(s)
	for _, runtime := range []string{"z", "a", "c", "b"} {
		stateTestSend(t, s, stateTestReport(s, runtime, 1), 200)
	}
	got := stateTestRead(t, s, 64)
	if !got.Ambiguous || got.Current != nil || got.Latest.Runtime != "a" || len(got.Runtimes) != 4 {
		t.Fatal(got)
	}
	stateTestSend(t, s, stateTestReport(s, "e", 1), 429)
	*now = now.Add(45 * time.Second)
	stateTestSend(t, s, stateTestReport(s, "e", 1), 200)
	got = stateTestRead(t, s, 0)
	if got.Ambiguous || got.Current == nil || got.Current.Runtime != "e" || len(got.Runtimes) != 4 {
		t.Fatal(got)
	}
	// The retired row cannot steal capacity again, even with a higher sequence.
	stateTestSend(t, s, stateTestReport(s, "z", 2), 409)
	*now = now.Add(playerStateRetention)
	stateTestSend(t, s, stateTestReport(s, "z", 2), 200)
	got = stateTestRead(t, s, 64)
	if len(got.Runtimes) != 1 || len(got.History) != 1 {
		t.Fatal(got)
	}
}
func TestPlayerStateHistoryPruningDoesNotResetHighWater(t *testing.T) {
	s := newTestServer(t)
	now := stateTestClock(s)
	v := stateTestReport(s, "page", 1)
	stateTestSend(t, s, v, 200)
	for i := int64(2); i <= 70; i++ {
		*now = now.Add(2 * time.Second)
		v.Sequence = i
		if i%2 == 0 {
			v.Playback.State = "paused"
		} else {
			v.Playback.State = "playing"
		}
		stateTestSend(t, s, v, 200)
	}
	got := stateTestRead(t, s, 64)
	if len(got.History) != 64 || got.Current.Sequence != 70 {
		t.Fatal(got)
	}
	v.Sequence = 1
	stateTestSend(t, s, v, 409)
}
func TestPlayerStateFenceCapacityRecoversAfterRetention(t *testing.T) {
	s := newTestServer(t)
	now := stateTestClock(s)
	for i := 0; i < playerStateFenceMax; i++ {
		stateTestSend(t, s, stateTestReport(s, fmt.Sprintf("r%d", i), 1), 200)
		*now = now.Add(46 * time.Second)
	}
	stateTestSend(t, s, stateTestReport(s, "extra", 1), 429)
	*now = now.Add(playerStateRetention)
	stateTestSend(t, s, stateTestReport(s, "extra", 1), 200)
	if len(s.playerState.devices["first"].Fences) != 1 {
		t.Fatal("expired fences did not clear")
	}
}
func TestPlayerStateBackwardClockAndHistoryOrder(t *testing.T) {
	s := newTestServer(t)
	now := stateTestClock(s)
	original := *now
	stateTestSend(t, s, stateTestReport(s, "a", 1), 200)
	*now = now.Add(-time.Second)
	got := stateTestRead(t, s, 64)
	if got.Current != nil || got.Latest.AgeMS != 0 || !got.Latest.Stale || got.Connection.State != "offline" {
		t.Fatal(got)
	}
	stateTestSend(t, s, stateTestReport(s, "b", 1), 200)
	got = stateTestRead(t, s, 64)
	if got.History[0].Runtime != "a" || !got.Connection.LastSeen.Equal(original) {
		t.Fatal(got)
	}
}
func TestPlayerStateStrictPayloadAndPrivacy(t *testing.T) {
	s := newTestServer(t)
	good := stateTestJSON(t, stateTestReport(s, "page", 1))
	bad := []string{
		strings.Replace(good, `"version":1,`, `"version":1.0,`, 1),
		strings.Replace(good, `"sequence":1`, `"sequence":null`, 1),
		strings.Replace(good, `"sequence":1`, `"sequence":true`, 1),
		strings.Replace(good, `"sequence":1`, `"sequence":1.0`, 1),
		strings.Replace(good, `"standby":false`, `"standby": null `, 1),
		strings.Replace(good, `"standby":false`, `"standby":true`, 1),
		strings.Replace(good, `"position":100.5`, `"position":"100.5"`, 1),
		strings.Replace(good, `"channel_number":null`, `"channel_number":1.0`, 1),
		strings.Replace(good, `"title":"Example"`, `"title":"x","title":"Example"`, 1),
		strings.Replace(good, `"title":"Example",`, "", 1),
		strings.TrimSuffix(good, "}") + `,"secret":"hidden"}`,
		good + `{}`, strings.Repeat(" ", playerStateBodyMax) + good,
	}
	for i, raw := range bad {
		if _, ok := decodeStateReport([]byte(raw)); ok {
			t.Fatalf("accepted malformed case %d", i)
		}
	}
	for _, label := range []string{"https://secret.example/a", "/private/a", `\private\a`, `C:\private\a`, "c:/private/a", "~/private/a", "\u200b/private/a", "https:\u200b//secret.example/a", "\ue000/private/a", "\u0378/private/a"} {
		for _, field := range []string{"title", "provider", "version", "platform"} {
			v := stateTestReport(s, "page", 1)
			switch field {
			case "title":
				v.Playback.Title = &label
			case "provider":
				v.Playback.Provider = &label
			case "version":
				v.Player.Version = label
			case "platform":
				v.Player.Platform = label
			}
			if _, ok := decodeStateReport([]byte(stateTestJSON(t, v))); ok {
				t.Fatalf("accepted private %s", field)
			}
		}
	}
	for _, label := range []string{"AC/DC", "d/maxtv", "Песня — 1", "A\u200bB"} {
		v := stateTestReport(s, "page", 1)
		v.Playback.Title = &label
		if _, ok := decodeStateReport([]byte(stateTestJSON(t, v))); !ok {
			t.Fatal("rejected ordinary display text")
		}
	}
	expect(t, request(s, "POST", playerStatePath, firstToken, strings.Repeat(" ", playerStateBodyMax+1), nil), 413)
	for _, inactive := range []string{"idle", "ended", "standby"} {
		v := stateTestReport(s, "page", 1)
		v.Playback.State = inactive
		v.Player.Standby = inactive == "standby"
		if validStateContent(v.Player, v.Playback) {
			t.Fatal("accepted stale active title")
		}
		v.Playback.Title = nil
		v.Playback.MediaID = nil
		if !validStateContent(v.Player, v.Playback) {
			t.Fatal("rejected clear inactive report")
		}
	}
}
func TestPlayerStateRolesTransportAndLegacyPoll(t *testing.T) {
	token := strings.Repeat("o", 32)
	sum := sha256.Sum256([]byte(token))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "reader", CredentialSHA256: hex.EncodeToString(sum[:]), DeviceIDs: []string{"first"}, Actions: []string{"status"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	body := stateTestJSON(t, stateTestReport(s, "page", 1))
	expect(t, request(s, "POST", playerStatePath, adminToken, body, nil), 403)
	expect(t, request(s, "POST", playerStatePath, token, body, nil), 403)
	expect(t, request(s, "GET", playerStatePath+"?device_id=first", firstToken, "", nil), 403)
	expect(t, request(s, "GET", playerStatePath+"?device_id=first", token, "", nil), 200)
	expect(t, request(s, "GET", playerStatePath+"?device_id=second", token, "", nil), 403)
	expect(t, request(s, "GET", playerStatePath+"?device_id=first&extra=1", adminToken, "", nil), 400)
	expect(t, transportCall(s, "POST", playerStatePath, firstToken, body), 200)
	if stateTestRead(t, s, 64).Current == nil {
		t.Fatal("device envelope did not report state")
	}
	expect(t, transportCall(s, "GET", playerStatePath+"?device_id=first", firstToken, ""), 400)
	ack := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
	expect(t, ack, 200)
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(ack.Body.Bytes(), &fields)
	var ad map[string]any
	_ = json.Unmarshal(fields["player_state"], &ad)
	if !reflect.DeepEqual(ad, map[string]any{"version": float64(1), "controller_epoch": s.playerStateEpoch, "report_path": playerStatePath, "heartbeat_ms": float64(15000), "stale_after_ms": float64(45000), "max_body_bytes": float64(4096)}) {
		t.Fatal(ad)
	}
	legacy := request(s, "GET", "/api/webhook/commands", firstToken, "", nil)
	expect(t, legacy, 200)
	if strings.TrimSpace(legacy.Body.String()) != "[]" {
		t.Fatal(legacy.Body.String())
	}
	s.operators[0].actions = map[string]bool{}
	expect(t, request(s, "GET", playerStatePath+"?device_id=first", token, "", nil), 403)
}
func TestPlayerStateReportsHaveIndependentRateBudget(t *testing.T) {
	s := newTestServer(t)
	stateTestClock(s)
	v := stateTestReport(s, "page", 1)
	for i := 0; i < 60; i++ {
		stateTestSend(t, s, v, 200)
	}
	stateTestSend(t, s, v, 429)
	for i := 0; i < 120; i++ {
		expect(t, request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil), 200)
	}
	expect(t, request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil), 429)
	if len(s.playerState.devices["first"].History) != 1 {
		t.Fatal("duplicate heartbeat produced history")
	}
}

func TestPlayerStateBackwardClockRPCResponseDoesNotRefreshConnection(t *testing.T) {
	s := newTestServer(t)
	now := stateTestClock(s)
	original := *now
	stateTestSend(t, s, stateTestReport(s, "page", 1), 200)
	id := rpcID(t, s, `{"action":"status","params":{}}`)
	*now = now.Add(-time.Second)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 200)
	got := stateTestRead(t, s, 64)
	if got.Connection.State != "offline" || !got.Connection.LastSeen.Equal(original) {
		t.Fatal(got)
	}
}
