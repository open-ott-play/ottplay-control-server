package control

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const playerStateStoreMax = 8 * 1024 * 1024

var playerStateStoreError = errors.New("player state storage unavailable")

type playerStateDisk struct {
	Version int                    `json:"version"`
	Devices map[string]stateDevice `json:"devices"`
}
type playerStateStore struct {
	writer  sync.Mutex
	path    string
	lock    *os.File
	devices map[string]stateDevice
	failed  bool
	// The seam is also used to test failures before any acknowledgement.
	persist func([]byte) error
}

func (s *playerStateStore) storage() string {
	if s.path != "" {
		return "persistent"
	}
	return "memory"
}
func openPlayerState(c *config.PlayerState) (*playerStateStore, error) {
	s := &playerStateStore{devices: map[string]stateDevice{}}
	if c == nil {
		return s, nil
	}
	s.path = c.Path
	if registryPrivatePath(s.path, true) != nil {
		return nil, playerStateStoreError
	}
	lock, err := lockJournal(s.path + ".lock")
	if err != nil {
		return nil, playerStateStoreError
	}
	s.lock = lock
	fail := func() (*playerStateStore, error) { _ = lock.Close(); return nil, playerStateStoreError }
	f, err := os.Open(s.path)
	if err != nil {
		if !os.IsNotExist(err) {
			return fail()
		}
	} else {
		raw, e := io.ReadAll(io.LimitReader(f, playerStateStoreMax+1))
		closeErr := f.Close()
		if e != nil || closeErr != nil || len(raw) > playerStateStoreMax {
			return fail()
		}
		disk, ok := decodePlayerStateDisk(raw)
		if !ok {
			return fail()
		}
		s.devices = disk.Devices
	}
	s.persist = s.write
	return s, nil
}

// Comparing the typed round trip rejects absent/unknown fields and null in
// non-nullable fields without maintaining a second copy of the disk schema.
func decodePlayerStateDisk(raw []byte) (playerStateDisk, bool) {
	var disk playerStateDisk
	if !diagStrictJSON(raw) || json.Unmarshal(raw, &disk) != nil || disk.Version != 1 || disk.Devices == nil || len(disk.Devices) > config.MaxDevices {
		return disk, false
	}
	canonical, err := json.Marshal(disk)
	if err != nil {
		return disk, false
	}
	decode := func(b []byte) any {
		var value any
		d := json.NewDecoder(bytes.NewReader(b))
		d.UseNumber()
		_ = d.Decode(&value)
		return value
	}
	if !reflect.DeepEqual(decode(raw), decode(canonical)) {
		return disk, false
	}
	for id, d := range disk.Devices {
		if !registryID.MatchString(id) || !registryHash.MatchString(d.Authority) || d.Runtimes == nil || d.History == nil || d.Fences == nil || len(d.Runtimes) > playerStateRuntimeMax || len(d.History) > playerStateHistoryMax || len(d.Fences) > playerStateFenceMax {
			return disk, false
		}
		for runtime, f := range d.Fences {
			if !notificationRuntime.MatchString(runtime) || !playerStateEpochPattern.MatchString(f.Epoch) || !registryHash.MatchString(f.Digest) || f.Sequence < 1 || f.Sequence > 9007199254740991 || f.Seen.IsZero() {
				return disk, false
			}
		}
		seen := map[string]bool{}
		for i, r := range d.Runtimes {
			f, ok := d.Fences[r.Runtime]
			if seen[r.Runtime] || !ok || f.Retired || r.Sequence != f.Sequence || !r.ReceivedAt.Equal(f.Seen) || !validStateContent(r.Player, r.Playback) {
				return disk, false
			}
			// Persisted rows always need a new report in the new controller epoch.
			d.Runtimes[i].Restored = true
			seen[r.Runtime] = true
		}
		for _, r := range d.History {
			if !notificationRuntime.MatchString(r.Runtime) || r.Sequence < 1 || r.Sequence > 9007199254740991 || r.ReceivedAt.IsZero() || !validStateContent(r.Player, r.Playback) {
				return disk, false
			}
			if r.Event != "connected" && r.Event != "reconnected" && r.Event != "changed" {
				return disk, false
			}
		}
		disk.Devices[id] = d
	}
	return disk, true
}

// Oldest history is dispensable; current snapshots and sequence fences are not.
// The caller supplies a private copy, so failed persistence cannot publish it.
func playerStateBytes(rows map[string]stateDevice, limit int) ([]byte, error) {
	raw, err := json.Marshal(playerStateDisk{Version: 1, Devices: rows})
	if err != nil {
		return nil, playerStateStoreError
	}
	over := len(raw) - limit
	if over <= 0 {
		return raw, nil
	}
	// Removing an array item reduces its exact JSON length plus one comma
	// (unless it was the final item). Marshal the full store only twice.
	for over > 0 {
		victim := ""
		index := -1
		var oldest time.Time
		for id, d := range rows {
			for i, e := range d.History {
				if index < 0 || e.ReceivedAt.Before(oldest) || (e.ReceivedAt.Equal(oldest) && id < victim) {
					victim = id
					index = i
					oldest = e.ReceivedAt
				}
			}
		}
		if index < 0 {
			return nil, playerStateStoreError
		}
		d := rows[victim]
		item, e := json.Marshal(d.History[index])
		if e != nil {
			return nil, playerStateStoreError
		}
		over -= len(item)
		if len(d.History) > 1 {
			over--
		}
		d.History = append(d.History[:index], d.History[index+1:]...)
		rows[victim] = d
	}
	raw, err = json.Marshal(playerStateDisk{Version: 1, Devices: rows})
	if err != nil || len(raw) > limit {
		return nil, playerStateStoreError
	}
	return raw, nil
}
func (s *playerStateStore) save(rows map[string]stateDevice) error {
	raw, err := playerStateBytes(rows, playerStateStoreMax)
	if err != nil {
		return err
	}
	if s.persist != nil {
		return s.persist(raw)
	}
	return nil
}
func (s *playerStateStore) write(raw []byte) error {
	if registryPrivatePath(s.path, false) != nil {
		return playerStateStoreError
	}
	f, err := os.CreateTemp(filepath.Dir(s.path), ".player-state-*")
	if err != nil {
		return playerStateStoreError
	}
	name := f.Name()
	defer os.Remove(name)
	if _, err = f.Write(raw); err != nil {
		_ = f.Close()
		return playerStateStoreError
	}
	if f.Sync() != nil {
		_ = f.Close()
		return playerStateStoreError
	}
	if f.Close() != nil || os.Rename(name, s.path) != nil || syncRegistryParent(s.path) != nil {
		return playerStateStoreError
	}
	return nil
}
func (s *Server) restorePlayerState() error {
	rows := map[string]stateDevice{}
	now := s.now().UTC()
	for _, d := range s.currentDevices() {
		old, ok := s.playerState.devices[d.id]
		if !ok || old.Authority != s.stateAuthority(d) {
			continue
		}
		next := cloneStateDevice(old, now)
		// Old-epoch fences are needed only for retained snapshots. This bounds
		// restart recovery while allowing the first current-epoch sequence to reset.
		next.Fences = map[string]stateFence{}
		for i, r := range next.Runtimes {
			next.Runtimes[i].Restored = true
			next.Fences[r.Runtime] = old.Fences[r.Runtime]
		}
		rows[d.id] = next
	}
	if s.playerState.path != "" {
		if err := s.playerState.save(rows); err != nil {
			return err
		}
	}
	s.playerState.devices = rows
	return nil
}

// Called under s.mu and the registry admission gate. No disk I/O here: the
// new credential/generation can never restore the old authority's disk rows.
// The next semantic write (or startup cleanup) removes those obsolete rows.
func (s *Server) forgetPlayerState(id string) {
	if s.playerState != nil {
		delete(s.playerState.devices, id)
	}
}
