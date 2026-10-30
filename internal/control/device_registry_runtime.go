package control

import (
	"crypto/sha256"
	"encoding/json"
	"sort"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type registrySnapshot struct {
	devices     []*device
	identities  map[string]registryIdentity
	unavailable bool
}

func (s *Server) currentDevices() []*device {
	if snapshot := s.registryView.Load(); snapshot != nil {
		return snapshot.devices
	}
	return s.devices
}

func registryActiveConfig(c config.Config, rows map[string]registryIdentity) config.Config {
	c.Devices = []config.Device{}
	for _, row := range rows {
		if row.State == "active" {
			c.Devices = append(c.Devices, config.Device{ID: row.DeviceID, Token: row.Token, Diagnostics: &config.DeviceDiagnostics{Enabled: row.Diagnostics}})
		}
	}
	sort.Slice(c.Devices, func(i, j int) bool { return c.Devices[i].ID < c.Devices[j].ID })
	return c
}

// Called with registry.mu held, after durable persistence. Admission waits for
// already-running logical requests, so revoke confirmation cannot precede an
// old request's final delivery. No disk or DNS I/O runs under the admission gate.
func (s *Server) publishRegistry(rows map[string]registryIdentity) {
	s.registryGate.Lock()
	defer s.registryGate.Unlock()
	prior := s.registryView.Load()
	next := &registrySnapshot{identities: rows, devices: []*device{}, unavailable: s.registry.failed}
	old := map[string]*device{}
	for _, d := range s.currentDevices() {
		old[d.id] = d
	}
	removed := []string{}
	for id, d := range old {
		row, found := rows[id]
		unchanged := found && row.State == "active" && d.token == sha256.Sum256([]byte(row.Token)) && prior != nil && prior.identities[id].Generation == row.Generation
		if !unchanged {
			removed = append(removed, id)
			delete(old, id)
		}
	}
	s.mu.Lock()
	for _, id := range removed {
		for _, d := range s.currentDevices() {
			if d.id != id {
				continue
			}
			for _, e := range d.queue {
				s.bytes -= len(e.data)
			}
			for _, result := range d.results {
				s.resultBytes -= len(result.data)
			}
			clear(d.queue)
			d.queue = nil
			clear(d.results)
			d.results = nil
			s.notificationStats.Dropped += uint64(len(d.notifications.queue))
			d.notifications = notificationPresence{}
		}
		s.forgetPlayerState(id)
		s.notificationMedia.revokeDevice(id)
		for key, p := range s.pairings {
			if p.device == id {
				delete(s.pairings, key)
			}
		}
	}
	for id, row := range rows {
		if row.State != "active" {
			continue
		}
		d := old[id]
		if d == nil {
			d = &device{id: id, token: sha256.Sum256([]byte(row.Token))}
		}
		next.devices = append(next.devices, d)
	}
	sort.Slice(next.devices, func(i, j int) bool { return next.devices[i].id < next.devices[j].id })
	s.registryView.Store(next)
	s.mu.Unlock()
	d := s.diagnostics
	d.mu.Lock()
	for _, id := range removed {
		d.enabled[id] = false
	}
	d.expire(s.now())
	for _, id := range removed {
		for _, session := range d.sessions {
			if session.device == id {
				d.eventBytes -= session.eventBytes
				clear(session.events)
				session.events = nil
				session.eventBytes = 0
			}
		}
	}
	for id, row := range rows {
		d.enabled[id] = row.State == "active" && row.Diagnostics && d.configured
		if d.registrations[id] == nil && row.State == "active" {
			d.registrations[id] = &diagBudget{}
		}
	}
	d.mu.Unlock()
}

func (s *Server) commitRegistry(disk registryDisk) error {
	r := s.registry
	if validateRegistryDisk(disk) != nil {
		return registryCapacityError
	}
	encoded, err := json.Marshal(disk)
	if err != nil || len(encoded) > maxRegistryBytes {
		return registryCapacityError
	}
	rows, err := registryIdentities(r.config, disk)
	if err != nil {
		return err
	}
	if registryRevocationReserve(r.config, disk, rows, s.now()) != nil {
		return registryCapacityError
	}
	if err = r.persist(disk); err != nil {
		// A rename/fsync failure may have committed bytes despite a lost proof.
		// Never continue authenticating an uncertain previous credential set.
		r.failed = true
		s.publishRegistry(map[string]registryIdentity{})
		return registryError
	}
	r.disk = disk
	s.publishRegistry(rows)
	return nil
}

func registryCommitFailure(err error, uncertain string) (int, any) {
	if err == registryCapacityError {
		return registryFailure(409, "device registry capacity reached")
	}
	return registryFailure(503, uncertain)
}
