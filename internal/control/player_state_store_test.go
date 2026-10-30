package control

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func statePersistentConfig(t *testing.T) config.Config {
	t.Helper()
	c := testConfig()
	c.PlayerState = &config.PlayerState{Path: filepath.Join(t.TempDir(), "private", "player-state.json")}
	return c
}
func TestPlayerStatePersistentRestartAndStaticAuthority(t *testing.T) {
	c := statePersistentConfig(t)
	s, e := New(c)
	if e != nil {
		t.Fatal(e)
	}
	report := stateTestReport(s, "page", 99)
	stateTestSend(t, s, report, 200)
	oldEpoch := s.playerStateEpoch
	if e = s.Close(); e != nil {
		t.Fatal(e)
	}
	raw, e := os.ReadFile(c.PlayerState.Path)
	if e != nil {
		t.Fatal(e)
	}
	if strings.Contains(string(raw), firstToken) {
		t.Fatal("persisted credential")
	}
	info, _ := os.Stat(c.PlayerState.Path)
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0600 {
		t.Fatal("state file not private")
	}
	s, e = New(c)
	if e != nil {
		t.Fatal(e)
	}
	got := stateTestRead(t, s, 64)
	if got.Storage != "persistent" || got.Current != nil || got.Latest == nil || !got.Latest.Stale || !got.Latest.Restored || got.Epoch == oldEpoch {
		t.Fatal(got)
	}
	stateTestSend(t, s, report, 409)
	report.Epoch = s.playerStateEpoch
	report.Sequence = 1
	stateTestSend(t, s, report, 200)
	got = stateTestRead(t, s, 64)
	if got.Current == nil || got.Current.Restored || got.Current.Sequence != 1 || got.History[0].Event != "reconnected" {
		t.Fatal(got)
	}
	if e = s.Close(); e != nil {
		t.Fatal(e)
	}
	c.Devices[0].Token = strings.Repeat("d", 32)
	s, e = New(c)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	got = stateTestRead(t, s, 64)
	if got.Latest != nil || len(got.History) != 0 {
		t.Fatal("old identity restored", got)
	}
	raw, _ = os.ReadFile(c.PlayerState.Path)
	if strings.Contains(string(raw), "Example") {
		t.Fatal("startup did not purge stale authority")
	}
}
func TestPlayerStateRegistryRotationEvictsWithoutAdmissionIO(t *testing.T) {
	c := registryTestConfig(t)
	c.PlayerState = &config.PlayerState{Path: filepath.Join(t.TempDir(), "private", "state.json")}
	s := registryTestServer(t, c)
	stateTestSend(t, s, stateTestReport(s, "page", 1), 200)
	old := s.registryView.Load().identities["first"]
	saves := 0
	s.playerState.persist = func([]byte) error { saves++; return errors.New("must not write under admission gate") }
	registryCall(t, s, "rotate", registryLifeBody("first", old.Generation, 910), 200)
	if saves != 0 || s.playerState.failed {
		t.Fatal("rotation wrote state under registry gate")
	}
	if _, ok := s.playerState.devices["first"]; ok {
		t.Fatal("retained rotated identity")
	}
	if got := stateTestRead(t, s, 64); got.Latest != nil || len(got.History) != 0 {
		t.Fatal(got)
	}
}
func TestPlayerStatePersistenceFailureDoesNotAcknowledgeOrPublish(t *testing.T) {
	s := newTestServer(t)
	stateTestClock(s)
	v := stateTestReport(s, "page", 1)
	stateTestSend(t, s, v, 200)
	before := s.playerState.devices["first"]
	s.playerState.persist = func([]byte) error { return errors.New("private path or secret must never be echoed") }
	v.Sequence = 2
	v.Playback.State = "paused"
	w := stateTestSend(t, s, v, 503)
	if strings.Contains(w.Body.String(), "secret") || !reflect.DeepEqual(before, s.playerState.devices["first"]) {
		t.Fatal("failed write leaked or published")
	}
	expect(t, request(s, "GET", playerStatePath+"?device_id=first", adminToken, "", nil), 503)
}
func TestPlayerStateAtomicStoreRejectsInvalidAndSymlink(t *testing.T) {
	c := statePersistentConfig(t)
	s, e := New(c)
	if e != nil {
		t.Fatal(e)
	}
	stateTestSend(t, s, stateTestReport(s, "page", 1), 200)
	raw, e := os.ReadFile(c.PlayerState.Path)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = New(c); e == nil {
		t.Fatal("accepted second store writer")
	}
	if e = s.Close(); e != nil {
		t.Fatal(e)
	}
	for _, bad := range [][]byte{[]byte(`{"version":1,"devices":null}`), append(raw, []byte(`{}`)...), []byte(strings.Replace(string(raw), `"retired":false`, `"retired":null`, 1)), []byte(strings.Replace(string(raw), `"standby":false`, `"standby":null`, 1))} {
		if e = os.WriteFile(c.PlayerState.Path, bad, 0600); e != nil {
			t.Fatal(e)
		}
		if _, e = New(c); e == nil {
			t.Fatal("accepted malformed persisted state")
		}
	}
	if e = os.WriteFile(c.PlayerState.Path, raw, 0600); e != nil {
		t.Fatal(e)
	}
	link := filepath.Join(filepath.Dir(c.PlayerState.Path), "linked.json")
	if e = os.Symlink(c.PlayerState.Path, link); e == nil {
		c.PlayerState.Path = link
		if _, e = New(c); e == nil {
			t.Fatal("accepted symlink")
		}
	}
}
func TestPlayerStateStorePrunesHistoryBeforeEightMiB(t *testing.T) {
	s := newTestServer(t)
	stateTestClock(s)
	v := stateTestReport(s, "page", 1)
	title, provider, version, platform := strings.Repeat("&", 256), strings.Repeat("&", 64), strings.Repeat("&", 64), strings.Repeat("&", 32)
	v.Playback.Title = &title
	v.Playback.Provider = &provider
	v.Player.Version = version
	v.Player.Platform = platform
	stateTestSend(t, s, v, 200)
	template := s.playerState.devices["first"]
	rows := map[string]stateDevice{}
	now := s.now()
	for i := 0; i < config.MaxDevices; i++ {
		d := cloneStateDevice(template, now)
		event := d.History[0]
		d.History = []stateEvent{}
		for j := 0; j < 64; j++ {
			event.Sequence = int64(j + 1)
			event.ReceivedAt = now.Add(time.Duration(j) * time.Second)
			d.History = append(d.History, event)
		}
		rows[fmt.Sprintf("device-%d", i)] = d
	}
	before, _ := json.Marshal(playerStateDisk{Version: 1, Devices: rows})
	if len(before) <= playerStateStoreMax {
		t.Fatal("fixture did not exceed disk bound")
	}
	out, e := playerStateBytes(rows, playerStateStoreMax)
	if e != nil || len(out) > playerStateStoreMax {
		t.Fatal(e, len(out))
	}
	events := 0
	for _, d := range rows {
		events += len(d.History)
		if !reflect.DeepEqual(d.Runtimes, template.Runtimes) || !reflect.DeepEqual(d.Fences, template.Fences) {
			t.Fatal("pruned latest or sequence fence")
		}
	}
	if events >= config.MaxDevices*64 {
		t.Fatal("history not pruned")
	}
	if _, e = playerStateBytes(rows, 1); e == nil {
		t.Fatal("accepted impossible bound")
	}
}
func TestPlayerStateDiskValidationRejectsPrivateMetadataAndMissingFence(t *testing.T) {
	s := newTestServer(t)
	stateTestSend(t, s, stateTestReport(s, "page", 1), 200)
	raw, _ := json.Marshal(playerStateDisk{Version: 1, Devices: s.playerState.devices})
	if _, ok := decodePlayerStateDisk(raw); !ok {
		t.Fatal("rejected valid own encoding")
	}
	for _, bad := range []string{
		strings.Replace(string(raw), `"version":"1.1.62"`, `"version":"https://private.example"`, 1),
		strings.Replace(string(raw), `"sequence":1`, `"sequence":1.0`, 1),
		strings.Replace(string(raw), `"fences":{`, `"unknown":{},"fences":{`, 1),
	} {
		if _, ok := decodePlayerStateDisk([]byte(bad)); ok {
			t.Fatal("accepted malformed private state")
		}
	}
	d := s.playerState.devices["first"]
	d.Fences = map[string]stateFence{}
	s.playerState.devices["first"] = d
	raw, _ = json.Marshal(playerStateDisk{Version: 1, Devices: s.playerState.devices})
	if _, ok := decodePlayerStateDisk(raw); ok {
		t.Fatal("accepted row without sequence fence")
	}
}

func TestPlayerStateSlowPersistenceDoesNotBlockPollOrRotation(t *testing.T) {
	for _, rotateID := range []string{"first", "second"} {
		t.Run(rotateID, func(t *testing.T) {
			c := registryTestConfig(t)
			s := registryTestServer(t, c)
			initial := stateTestReport(s, "page", 1)
			stateTestSend(t, s, initial, 200)
			second := stateTestReport(s, "other-page", 1)
			expect(t, request(s, "POST", playerStatePath, secondToken, stateTestJSON(t, second), nil), 200)
			started, release := make(chan struct{}), make(chan struct{})
			released := false
			defer func() {
				if !released {
					close(release)
				}
			}()
			s.playerState.persist = func([]byte) error { close(started); <-release; return nil }
			initial.Sequence = 2
			initial.Playback.State = "paused"
			body := stateTestJSON(t, initial)
			reportDone := make(chan *httptest.ResponseRecorder, 1)
			go func() { reportDone <- request(s, "POST", playerStatePath, firstToken, body, nil) }()
			select {
			case <-started:
			case <-time.After(2 * time.Second):
				t.Fatal("report did not reach persistence")
			}
			// Both ordinary command delivery and a credential writer must progress
			// while the state store is blocked, without waiting for release.
			pollDone := make(chan *httptest.ResponseRecorder, 1)
			go func() { pollDone <- request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil) }()
			select {
			case w := <-pollDone:
				expect(t, w, 200)
			case <-time.After(2 * time.Second):
				t.Fatal("state fsync blocked command polling")
			}
			generation := s.registryView.Load().identities[rotateID].Generation
			rotateBody := stateTestJSON(t, registryLifeBody(rotateID, generation, 920))
			rotated := make(chan *httptest.ResponseRecorder, 1)
			go func() { rotated <- request(s, "POST", "/api/device-registry/rotate", adminToken, rotateBody, nil) }()
			select {
			case w := <-rotated:
				expect(t, w, 200)
			case <-time.After(2 * time.Second):
				t.Fatal("state fsync blocked credential rotation")
			}
			close(release)
			released = true
			select {
			case w := <-reportDone:
				if rotateID == "first" {
					expect(t, w, 409)
				} else {
					expect(t, w, 200)
				}
			case <-time.After(2 * time.Second):
				t.Fatal("report failed to finish")
			}
			if _, ok := s.playerState.devices[rotateID]; ok {
				t.Fatal("in-flight write resurrected revoked state")
			}
		})
	}
}
