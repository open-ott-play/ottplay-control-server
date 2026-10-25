package control

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func registryTestConfig(t *testing.T) config.Config {
	t.Helper()
	c := testConfig()
	c.DeviceRegistry = &config.DeviceRegistry{Path: filepath.Join(t.TempDir(), "private", "devices.json")}
	if err := InitDeviceRegistry(c); err != nil {
		t.Fatal(err)
	}
	return c
}

func registryTestServer(t *testing.T, c config.Config) *Server {
	t.Helper()
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func registryCall(t *testing.T, s *Server, kind string, body map[string]any, want int) registryReceipt {
	t.Helper()
	b, _ := json.Marshal(body)
	w := request(s, "POST", "/api/device-registry/"+kind, adminToken, string(b), nil)
	expect(t, w, want)
	var result registryReceipt
	if want == 200 && json.Unmarshal(w.Body.Bytes(), &result) != nil {
		t.Fatal("invalid registry receipt")
	}
	return result
}

func registryAddBody(id string, op int) map[string]any {
	return map[string]any{"version": 1, "operation_id": fmt.Sprintf("%032x", op), "device_id": id, "diagnostics_enabled": false}
}

func registryLifeBody(id, generation string, op int) map[string]any {
	return map[string]any{"version": 1, "operation_id": fmt.Sprintf("%032x", op), "device_id": id, "expected_generation": generation}
}

func registryCredential(t *testing.T, s *Server, id, generation string) string {
	t.Helper()
	w := request(s, "GET", "/api/device-registry/credential?device_id="+id+"&generation="+generation, adminToken, "", nil)
	expect(t, w, 200)
	if w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("credential response cached")
	}
	var result map[string]any
	if json.Unmarshal(w.Body.Bytes(), &result) != nil {
		t.Fatal("invalid credential")
	}
	return result["token"].(string)
}

func TestDeviceRegistryTransportDoesNotHoldOuterReadGate(t *testing.T) {
	s := registryTestServer(t, registryTestConfig(t))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	body := transportBody("GET", "/api/webhook/commands", firstToken, "")
	r := httptest.NewRequest("POST", deviceTransportPath, nil).WithContext(ctx)
	r.Header.Set("Content-Type", "application/json")
	r.ContentLength = int64(len(body))
	reading, releaseBody := make(chan struct{}), make(chan struct{})
	writerStarted, writerAcquired, releaseWriter := make(chan struct{}), make(chan struct{}), make(chan struct{})
	requestDone, writerDone := make(chan struct{}), make(chan struct{})
	bodyReleased, writerReleased, held := false, false, true
	defer func() {
		cancel()
		if held {
			s.registryGate.RUnlock()
		}
		if !bodyReleased {
			close(releaseBody)
		}
		if !writerReleased {
			close(releaseWriter)
		}
	}()
	r.Body = &diagnosticBodyCallback{before: func() { close(reading); <-releaseBody }, body: strings.NewReader(body)}
	w := httptest.NewRecorder()
	s.registryGate.RLock()
	go func() { s.ServeHTTP(w, r); close(requestDone) }()
	select {
	case <-reading:
	case <-time.After(2 * time.Second):
		t.Fatal("transport did not reach its bounded body read")
	}
	go func() {
		close(writerStarted)
		s.registryGate.Lock()
		close(writerAcquired)
		<-releaseWriter
		s.registryGate.Unlock()
		close(writerDone)
	}()
	<-writerStarted
	s.registryGate.RUnlock()
	held = false
	select {
	case <-writerAcquired:
	case <-time.After(2 * time.Second):
		t.Fatal("transport body read held a registry read lock and blocked the queued writer")
	}
	close(releaseBody)
	bodyReleased = true
	select {
	case <-requestDone:
		t.Fatal("logical device route bypassed the active registry writer")
	default:
	}
	close(releaseWriter)
	writerReleased = true
	select {
	case <-requestDone:
	case <-time.After(2 * time.Second):
		t.Fatal("transport and queued registry writer deadlocked")
	}
	<-writerDone
	expect(t, w, 200)
}

func TestDeviceRegistryAddRevokeRotateAndRestart(t *testing.T) {
	c := registryTestConfig(t)
	s := registryTestServer(t, c)
	body := registryAddBody("new-tv", 1)
	added := registryCall(t, s, "add", body, 200)
	token := registryCredential(t, s, "new-tv", added.Generation)
	expect(t, request(s, "GET", "/api/webhook/commands", token, "", nil), 200)
	again := registryCall(t, s, "add", body, 200)
	if again.Generation != added.Generation {
		t.Fatal("replay rotated credential")
	}
	body["device_id"] = "different"
	registryCall(t, s, "add", body, 409)
	revoked := registryCall(t, s, "revoke", registryLifeBody("new-tv", added.Generation, 2), 200)
	if revoked.DeviceStopConfirmed == nil || *revoked.DeviceStopConfirmed {
		t.Fatal("revoke claims remote stop")
	}
	expect(t, request(s, "GET", "/api/webhook/commands", token, "", nil), 401)
	registryCall(t, s, "add", registryAddBody("new-tv", 3), 409)
	s.Close()
	s = registryTestServer(t, c)
	expect(t, request(s, "GET", "/api/webhook/commands", token, "", nil), 401)
	rotated := registryCall(t, s, "rotate", registryLifeBody("new-tv", revoked.Generation, 4), 200)
	newToken := registryCredential(t, s, "new-tv", rotated.Generation)
	if newToken == token || rotated.Generation == revoked.Generation {
		t.Fatal("rotation reused revoked identity")
	}
	expect(t, request(s, "GET", "/api/webhook/commands", newToken, "", nil), 200)
	expect(t, request(s, "GET", "/api/webhook/commands", token, "", nil), 401)
	registryCall(t, s, "revoke", registryLifeBody("new-tv", added.Generation, 5), 409)
	w := request(s, "GET", "/api/device-registry/operations?id="+fmt.Sprintf("%032x", 1), adminToken, "", nil)
	expect(t, w, 200)
	if strings.Contains(w.Body.String(), token) || strings.Contains(w.Body.String(), newToken) {
		t.Fatal("receipt leaked credential")
	}
	for _, cred := range []string{"", firstToken, token} {
		expect(t, request(s, "GET", "/api/device-registry", cred, "", nil), 403)
	}
}

func TestDeviceRegistryStaticTombstoneConflictAndExplicitConversion(t *testing.T) {
	c := registryTestConfig(t)
	s := registryTestServer(t, c)
	initial := s.registryView.Load().identities["first"]
	revoked := registryCall(t, s, "revoke", registryLifeBody("first", initial.Generation, 1), 200)
	s.Close()
	s = registryTestServer(t, c)
	expect(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), 401)
	expect(t, request(s, "GET", "/api/webhook/commands", secondToken, "", nil), 200)
	s.Close()
	c.Devices[0].Token = strings.Repeat("x", 32)
	s = registryTestServer(t, c)
	conflict := s.registryView.Load().identities["first"]
	if conflict.State != "conflict" || conflict.Generation != revoked.Generation {
		t.Fatal("changed static token silently revived device")
	}
	expect(t, request(s, "GET", "/api/webhook/commands", c.Devices[0].Token, "", nil), 401)
	body := registryLifeBody("first", conflict.Generation, 2)
	body["observed_static_fingerprint"] = conflict.StaticFingerprint
	reconciled := registryCall(t, s, "reconcile", body, 200)
	token := registryCredential(t, s, "first", reconciled.Generation)
	if token == c.Devices[0].Token || token == firstToken {
		t.Fatal("reconciliation adopted static token")
	}
	s.Close()
	c.Devices = c.Devices[1:]
	s = registryTestServer(t, c)
	missing := s.registryView.Load().identities["first"]
	if missing.State != "conflict" {
		t.Fatal("missing static source converted implicitly")
	}
	registryCall(t, s, "rotate", registryLifeBody("first", missing.Generation, 3), 409)
	convert := registryAddBody("first", 4)
	convert["convert_to_dynamic"], convert["expected_generation"] = true, missing.Generation
	converted := registryCall(t, s, "add", convert, 200)
	if s.registryView.Load().identities["first"].Origin != "dynamic" || converted.Generation == missing.Generation {
		t.Fatal("explicit conversion failed")
	}
}

func TestDeviceRegistryStorageIsPrivateStrictAndSingleWriter(t *testing.T) {
	c := registryTestConfig(t)
	if InitDeviceRegistry(c) == nil {
		t.Fatal("init overwrote registry")
	}
	s := registryTestServer(t, c)
	if _, err := New(c); err == nil {
		t.Fatal("second writer accepted")
	}
	s.Close()
	original, err := os.ReadFile(c.DeviceRegistry.Path)
	if err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{`{}`, `null`, strings.Replace(string(original), `"version":1`, `"version":1,"version":1`, 1), strings.Replace(string(original), `"records":[]`, `"records":null`, 1), string(original) + `{}`} {
		if os.WriteFile(c.DeviceRegistry.Path, []byte(bad), 0600) != nil {
			t.Fatal("fixture write failed")
		}
		if _, err := New(c); err == nil {
			t.Fatal("invalid registry accepted")
		}
	}
	if os.WriteFile(c.DeviceRegistry.Path, original, 0600) != nil {
		t.Fatal("fixture restore failed")
	}
	if runtime.GOOS != "windows" {
		os.Chmod(c.DeviceRegistry.Path, 0644)
		if _, err := New(c); err == nil {
			t.Fatal("public registry accepted")
		}
		os.Chmod(c.DeviceRegistry.Path, 0600)
	}
	if os.Remove(c.DeviceRegistry.Path) != nil {
		t.Fatal("remove failed")
	}
	if _, err := New(c); err == nil {
		t.Fatal("missing registry silently recreated")
	}
}

func TestDeviceRegistryPersistenceUncertaintyNeverActivatesUnprovenCredential(t *testing.T) {
	for _, afterWrite := range []bool{false, true} {
		t.Run(fmt.Sprint(afterWrite), func(t *testing.T) {
			c := registryTestConfig(t)
			s := registryTestServer(t, c)
			s.registry.persist = func(d registryDisk) error {
				if afterWrite {
					if err := s.registry.save(d); err != nil {
						return err
					}
				}
				return errors.New("test private failure")
			}
			registryCall(t, s, "add", registryAddBody("uncertain", 1), 503)
			if len(s.currentDevices()) != 0 || !s.registry.failed {
				t.Fatal("uncertain persistence retained credential admission")
			}
			expect(t, request(s, "GET", "/readyz", "", "", nil), 503)
			expect(t, request(s, "GET", "/healthz", "", "", nil), 200)
			s.Close()
			s = registryTestServer(t, c)
			_, found := s.registryView.Load().identities["uncertain"]
			if found != afterWrite {
				t.Fatal("restart did not reflect durable file")
			}
			if afterWrite {
				expect(t, request(s, "GET", "/api/device-registry/operations?id="+fmt.Sprintf("%032x", 1), adminToken, "", nil), 200)
			}
		})
	}
}

func registryPairingServer(t *testing.T, c config.Config) *Server {
	t.Helper()
	s := registryTestServer(t, c)
	s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
		return []discoveredServer{homeServer}, time.Second, nil
	}
	return s
}

func TestDeviceRegistryUnknownPairingPersistsApprovedClaimAndSelectedAddress(t *testing.T) {
	c := registryTestConfig(t)
	alias := "https://player.example/ott-control"
	c.Discovery = &config.Discovery{Domain: "example.test", Nameserver: "127.0.0.1:53", PublicURL: publicController, PublicAliases: []string{alias}}
	s := registryPairingServer(t, c)
	w := request(s, "POST", "/api/pairings", "", `{"device_id":"unknown-tv","server_address":"`+alias+`"}`, nil)
	expect(t, w, 201)
	var claim claim
	if json.Unmarshal(w.Body.Bytes(), &claim) != nil {
		t.Fatal("invalid pairing")
	}
	if _, found := s.registryView.Load().identities["unknown-tv"]; found {
		t.Fatal("pending device became registered")
	}
	expect(t, redeem(s, claim), 202)
	approved := fmt.Sprintf(`{"id":%q,"code":%q}`, claim.ID, claim.Code)
	expect(t, request(s, "POST", "/api/pairings/approve", adminToken, approved, nil), 200)
	expect(t, request(s, "POST", "/api/pairings/approve", adminToken, approved, nil), 200)
	w = redeem(s, claim)
	expect(t, w, 200)
	var result map[string]string
	json.Unmarshal(w.Body.Bytes(), &result)
	if result["address"] != alias || result["device_id"] != "unknown-tv" {
		t.Fatal("selected address changed")
	}
	token := result["token"]
	expect(t, request(s, "GET", "/api/webhook/commands", token, "", nil), 200)
	s.Close()
	s = registryPairingServer(t, c)
	w = redeem(s, claim)
	expect(t, w, 200)
	if !strings.Contains(w.Body.String(), token) || !strings.Contains(w.Body.String(), alias) {
		t.Fatal("approved claim did not survive restart")
	}
	expect(t, request(s, "POST", "/api/pairings/approve", adminToken, approved, nil), 200)
	expect(t, request(s, "DELETE", "/api/pairings?id="+claim.ID, claim.Secret, "", nil), 200)
	expect(t, request(s, "GET", "/api/webhook/commands", token, "", nil), 200)
	expect(t, redeem(s, claim), 401)
}

func TestDeviceRegistryAliasPolicyAndRemoval(t *testing.T) {
	c := registryTestConfig(t)
	alias := "https://player.example/ott-control"
	c.Discovery = &config.Discovery{Domain: "example.test", Nameserver: "127.0.0.1:53", PublicURL: publicController, PublicAliases: []string{alias}}
	s := registryPairingServer(t, c)
	for _, address := range []string{"http://player.example/ott-control", "https://other.example", "https://player.example", alias + "?token=x"} {
		body, _ := json.Marshal(map[string]string{"device_id": "first", "server_address": address})
		expect(t, request(s, "POST", "/api/pairings", "", string(body), nil), 400)
	}
	w := request(s, "POST", "/api/pairings", "", `{"device_id":"aliased-tv","server_address":"`+alias+`"}`, nil)
	expect(t, w, 201)
	var claim claim
	json.Unmarshal(w.Body.Bytes(), &claim)
	expect(t, approve(t, s, claim), 200)
	identity := s.registryView.Load().identities["aliased-tv"]
	token := registryCredential(t, s, identity.DeviceID, identity.Generation)
	s.Close()
	c.Discovery.PublicAliases = nil
	s = registryPairingServer(t, c)
	expect(t, redeem(s, claim), 401)
	if len(s.registry.disk.Claims) != 0 {
		t.Fatal("removed address retained an approved claim")
	}
	if s.discovery.publicURL != publicController {
		t.Fatal("native canonical address changed")
	}
	s.Close()
	c.Discovery.PublicAliases = []string{alias}
	s = registryPairingServer(t, c)
	expect(t, redeem(s, claim), 401)
	expect(t, request(s, "GET", "/api/webhook/commands", token, "", nil), 200)
}

func TestDeviceRegistryPendingBoundsAndZeroStaticBootstrap(t *testing.T) {
	c := registryTestConfig(t)
	c.Devices = nil
	c.Discovery = &config.Discovery{Domain: "example.test", Nameserver: "127.0.0.1:53", PublicURL: publicController}
	s := registryPairingServer(t, c)
	for i := 0; i < 300; i++ {
		w := request(s, "POST", "/api/pairings", "", fmt.Sprintf(`{"device_id":"pending-%d"}`, i), nil)
		if w.Code != 201 && w.Code != 429 {
			t.Fatal("unexpected pending result", w.Code)
		}
	}
	if len(s.pairRates) > 256 || len(s.pairings) > maxPairings || len(s.currentDevices()) != 0 || len(s.registry.disk.Records) != 0 {
		t.Fatal("unapproved registration allocated unbounded rights or state")
	}
	s.globalRate = bucket{}
	s.adminRate = bucket{}
	added := registryCall(t, s, "add", registryAddBody("first-dynamic", 1), 200)
	if added.State != "active" || len(s.currentDevices()) != 1 {
		t.Fatal("zero-static bootstrap failed")
	}
}

func TestDeviceRegistrySerializedCapacityRejectsBeforePersistence(t *testing.T) {
	c := registryTestConfig(t)
	s := registryTestServer(t, c)
	before, _ := os.ReadFile(c.DeviceRegistry.Path)
	disk := cloneRegistry(s.registry.disk, time.Now())
	for i := 0; i < maxRegistryRecords; i++ {
		row := registryRecord{DeviceID: fmt.Sprintf("retired-%d", i), Generation: fmt.Sprintf("%032x", i), State: "revoked", Origin: "dynamic", RevokedHashes: []string{}}
		for j := 0; j < maxRegistryHashes; j++ {
			row.RevokedHashes = append(row.RevokedHashes, fmt.Sprintf("%064x", j))
		}
		disk.Records = append(disk.Records, row)
	}
	encoded, _ := json.Marshal(disk)
	if validateRegistryDisk(disk) != nil || len(encoded) <= maxRegistryBytes {
		t.Fatal("fixture must exceed serialized capacity without violating record bounds")
	}
	persisted := false
	s.registry.persist = func(registryDisk) error { persisted = true; return nil }
	s.registry.mu.Lock()
	err := s.commitRegistry(disk)
	s.registry.mu.Unlock()
	after, _ := os.ReadFile(c.DeviceRegistry.Path)
	if err != registryCapacityError || persisted || s.registry.failed || string(after) != string(before) || len(s.currentDevices()) != len(c.Devices) {
		t.Fatal("capacity rejection attempted persistence or disabled valid identities")
	}
	expect(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), 200)
}

func TestDeviceRegistryPairingDiagnosticPolicyAndReplayAreExplicit(t *testing.T) {
	c := registryTestConfig(t)
	c.Discovery = &config.Discovery{Domain: "example.test", Nameserver: "127.0.0.1:53", PublicURL: publicController}
	s := registryPairingServer(t, c)
	known := startPairing(t, s)
	withDiagnostics := fmt.Sprintf(`{"id":%q,"code":%q,"diagnostics_enabled":true}`, known.ID, known.Code)
	expect(t, request(s, "POST", "/api/pairings/approve", adminToken, withDiagnostics, nil), 409)
	expect(t, approve(t, s, known), 200)
	expect(t, request(s, "POST", "/api/pairings/approve", adminToken, withDiagnostics, nil), 409)
	if s.registryView.Load().identities["first"].Diagnostics {
		t.Fatal("pairing changed an existing diagnostic policy")
	}
	w := request(s, "POST", "/api/pairings", "", `{"device_id":"unknown-diagnostics"}`, nil)
	expect(t, w, 201)
	var unknown claim
	json.Unmarshal(w.Body.Bytes(), &unknown)
	withDiagnostics = fmt.Sprintf(`{"id":%q,"code":%q,"diagnostics_enabled":true}`, unknown.ID, unknown.Code)
	expect(t, request(s, "POST", "/api/pairings/approve", adminToken, withDiagnostics, nil), 400)
	if _, exists := s.registryView.Load().identities["unknown-diagnostics"]; exists {
		t.Fatal("approval without global diagnostics registered a device")
	}
	expect(t, approve(t, s, unknown), 200)
	if s.registryView.Load().identities["unknown-diagnostics"].Diagnostics {
		t.Fatal("unknown approval did not default to diagnostics disabled")
	}
}

func TestDeviceRegistryDelayedCameraCannotCrossCredentialGeneration(t *testing.T) {
	c := registryTestConfig(t)
	s := registryTestServer(t, c)
	mediaPresence(t, s, firstToken, "same-runtime")
	n := sampleNotification("first")
	n.Media = mediaTestFrame(t)
	n.TargetRuntimes = s.NotificationTargets(n)
	n.MediaTargetRuntimes = s.NotificationMediaTargets(n)
	release := make(chan struct{})
	queued := make(chan int, 1)
	go func() {
		<-release
		queued <- s.DeliverNotification(n).Queued
	}()
	generation := s.registryView.Load().identities["first"].Generation
	revoked := registryCall(t, s, "revoke", registryLifeBody("first", generation, 1), 200)
	rotated := registryCall(t, s, "rotate", registryLifeBody("first", revoked.Generation, 2), 200)
	token := registryCredential(t, s, "first", rotated.Generation)
	mediaPresence(t, s, token, "same-runtime")
	close(release)
	if <-queued != 0 || s.notificationMedia.bytes != 0 {
		t.Fatal("old camera acquisition reached a re-registered identity using the same runtime")
	}
	n.TargetRuntimes = s.NotificationTargets(n)
	n.MediaTargetRuntimes = s.NotificationMediaTargets(n)
	if s.DeliverNotification(n).Queued != 1 || s.notificationMedia.bytes == 0 {
		t.Fatal("current generation could not receive a fresh camera acquisition")
	}
}

func TestDeviceRegistryRevokeWaitsForAdmittedRequestAndRejectsOldCredential(t *testing.T) {
	c := registryTestConfig(t)
	s := registryTestServer(t, c)
	id := rpcID(t, s, `{"action":"status","params":{}}`)
	body := `{"id":"` + id + `","status":"ok","data":{}}`
	r := httptest.NewRequest("POST", "/api/responses", strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer "+firstToken)
	r.Header.Set("Content-Type", "application/json")
	w := &blockedResultWriter{httptest.NewRecorder(), make(chan struct{}), make(chan struct{})}
	done := make(chan struct{})
	go func() { s.ServeHTTP(w, r); close(done) }()
	select {
	case <-w.entered:
	case <-time.After(5 * time.Second):
		t.Fatal("request did not start response")
	}
	generation := s.registryView.Load().identities["first"].Generation
	mutation, _ := json.Marshal(registryLifeBody("first", generation, 1))
	revoked := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		revoked <- request(s, "POST", "/api/device-registry/revoke", adminToken, string(mutation), nil)
	}()
	close(w.release)
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("old request did not finish")
	}
	select {
	case result := <-revoked:
		expect(t, result, 200)
	case <-time.After(5 * time.Second):
		t.Fatal("revoke did not complete")
	}
	expect(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), 401)
	if len(s.currentDevices()) != 1 || s.resultBytes != 0 {
		t.Fatal("revoked queue/results survived")
	}
}

func TestDeviceRegistryRevocationClearsRuntimeAndMediaWithoutWideningScopes(t *testing.T) {
	c := registryTestConfig(t)
	diag := diagnosticConfig()
	c.Diagnostics = diag.Diagnostics
	c.Devices = diag.Devices
	s := registryTestServer(t, c)
	rt, runtimeToken := diagnosticRegister(t, s)
	started := diagnosticStart(t, s, rt, "start")
	diagnosticAck(t, s, rt, runtimeToken, started, 200)
	diagnosticCall(t, s, "POST", "/events", runtimeToken, diagnosticBatch(rt, started["session_id"].(string), 1), 200)
	mediaPresence(t, s, firstToken, "media-runtime")
	frame := mediaTestFrame(t)
	if s.grantNotificationMedia(strings.Repeat("a", 32), "first", "media-runtime", frame, time.Now().Add(time.Minute), time.Now()) == nil {
		t.Fatal("media grant failed")
	}
	gen := s.registryView.Load().identities["first"].Generation
	registryCall(t, s, "revoke", registryLifeBody("first", gen, 1), 200)
	diagnosticCall(t, s, "POST", "/poll", runtimeToken, diagnosticPollBody(rt, 1, true), 401)
	if len(s.diagnostics.runtimes) != 0 || s.diagnostics.eventBytes != 0 || s.notificationMedia.bytes != 0 {
		t.Fatal("revoked runtime retained captures or grants")
	}
	for _, session := range s.diagnostics.sessions {
		if session.state != "revoked" || len(session.events) != 0 {
			t.Fatal("session did not terminate privately")
		}
	}
	add := registryAddBody("new-diagnostic", 2)
	add["diagnostics_enabled"] = true
	added := registryCall(t, s, "add", add, 200)
	token := registryCredential(t, s, added.DeviceID, added.Generation)
	diagnosticCall(t, s, "POST", "/runtimes", token, map[string]any{"instance_id": "new", "boot_id": "boot", "capabilities": []string{"playback"}, "consent": map[string]any{"granted": true, "epoch": "grant"}}, 201)
	diagnosticCall(t, s, "GET", "/runtimes?device_id=new-diagnostic", diagnosticOperatorToken, nil, 404)
}

func TestDeviceRegistryMergedScopesAndCapacity(t *testing.T) {
	c := registryTestConfig(t)
	s := registryTestServer(t, c)
	added := registryCall(t, s, "add", registryAddBody("scoped", 1), 200)
	registryCall(t, s, "revoke", registryLifeBody("scoped", added.Generation, 2), 200)
	s.Close()
	hash := sha256.Sum256([]byte(strings.Repeat("q", 32)))
	c.Operators = []config.CommandOperator{{ID: "scoped-operator", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"scoped"}, Actions: []string{"status"}}}
	s = registryTestServer(t, c)
	s.Close()
	c.Operators[0].DeviceIDs = []string{"absent"}
	if _, err := New(c); err == nil {
		t.Fatal("unknown merged scope accepted")
	}
	c.Operators = nil
	c.Devices = nil
	for i := 0; i < config.MaxDevices; i++ {
		c.Devices = append(c.Devices, config.Device{ID: fmt.Sprintf("static-%d", i), Token: fmt.Sprintf("%032x", i)})
	}
	s = registryTestServer(t, c)
	before := s.registry.disk.Revision
	registryCall(t, s, "add", registryAddBody("overflow", 3), 409)
	if s.registry.disk.Revision != before || len(s.currentDevices()) != config.MaxDevices {
		t.Fatal("capacity failure changed registry")
	}
}

func TestDeviceRegistryStrictRequestsAndDiskRecords(t *testing.T) {
	c := registryTestConfig(t)
	s := registryTestServer(t, c)
	for _, body := range []string{`{}`, `{"version":1,"version":1}`, `{"version":1,"operation_id":"00000000000000000000000000000001","device_id":"new","diagnostics_enabled":null}`, `{"version":1,"operation_id":"00000000000000000000000000000001","device_id":"new","diagnostics_enabled":false,"token":"private"}`} {
		expect(t, request(s, "POST", "/api/device-registry/add", adminToken, body, nil), 400)
	}
	registryCall(t, s, "add", registryAddBody("record", 1), 200)
	s.Close()
	original, _ := os.ReadFile(c.DeviceRegistry.Path)
	for _, changed := range []string{
		strings.Replace(string(original), `"diagnostics_enabled":false,`, "", 1),
		strings.Replace(string(original), `"diagnostics_enabled":false`, `"diagnostics_enabled":null`, 1),
		strings.Replace(string(original), `"persisted":true`, `"persisted":null`, 1),
		strings.Replace(string(original), `"revoked_token_sha256":[]`, `"revoked_token_sha256":null`, 1),
	} {
		if changed == string(original) {
			t.Fatal("negative fixture did not alter file")
		}
		os.WriteFile(c.DeviceRegistry.Path, []byte(changed), 0600)
		if _, err := New(c); err == nil {
			t.Fatal("malformed durable record accepted")
		}
	}
	os.WriteFile(c.DeviceRegistry.Path, original, 0600)
}

func TestDeviceRegistrySharedContract(t *testing.T) {
	encoded, err := os.ReadFile("../../contracts/device-registry-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Version  int                        `json:"version"`
		Requests map[string]json.RawMessage `json:"requests"`
		Invalid  []json.RawMessage          `json:"invalid_add"`
	}
	if json.Unmarshal(encoded, &fixture) != nil || fixture.Version != 1 || len(fixture.Invalid) == 0 {
		t.Fatal("invalid shared registry fixture")
	}
	s := registryTestServer(t, registryTestConfig(t))
	for _, invalid := range fixture.Invalid {
		expect(t, request(s, "POST", "/api/device-registry/add", adminToken, string(invalid), nil), 400)
	}
	expect(t, request(s, "POST", "/api/device-registry/add", adminToken, string(fixture.Requests["convert_absent_static"]), nil), 409)
	w := request(s, "POST", "/api/device-registry/add", adminToken, string(fixture.Requests["add"]), nil)
	expect(t, w, 200)
	var added registryReceipt
	if json.Unmarshal(w.Body.Bytes(), &added) != nil || !added.Persisted || added.State != "active" {
		t.Fatal("shared add example did not produce a durable receipt")
	}
	var revoked map[string]any
	if json.Unmarshal(fixture.Requests["revoke"], &revoked) != nil {
		t.Fatal("invalid shared revoke example")
	}
	revoked["expected_generation"] = added.Generation
	registryCall(t, s, "revoke", revoked, 200)
}

func TestDeviceRegistryOrdinarySaturationReservesEveryActiveRevocation(t *testing.T) {
	c := registryTestConfig(t)
	c.Devices = nil
	for i := 0; i < config.MaxDevices; i++ {
		c.Devices = append(c.Devices, config.Device{ID: fmt.Sprintf("static-%d", i), Token: fmt.Sprintf("%032x", 1000+i)})
	}
	s := registryTestServer(t, c)
	disk := s.registry.disk
	now := time.Now().Unix()
	for i := 0; i < maxRegistryOrdinaryOperations; i++ {
		id := fmt.Sprintf("%032x", 1000+i)
		disk.Operations = append(disk.Operations, registryOperation{ID: id, RequestHash: registryDigest(id), Kind: "rotate", CreatedAt: now, ExpiresAt: now + 86400, Result: registryReceipt{Version: 1, OperationID: id, DeviceID: "static-0", Generation: s.registryView.Load().identities["static-0"].Generation, State: "active", Persisted: true}})
	}
	if err := s.registry.save(disk); err != nil {
		t.Fatal(err)
	}
	s.Close()
	s = registryTestServer(t, c)
	registryCall(t, s, "rotate", registryLifeBody("static-0", s.registryView.Load().identities["static-0"].Generation, 999), 409)
	for i, device := range c.Devices {
		generation := s.registryView.Load().identities[device.ID].Generation
		registryCall(t, s, "revoke", registryLifeBody(device.ID, generation, 2000+i), 200)
	}
	if len(s.registry.disk.Operations) != maxRegistryOperations || len(s.currentDevices()) != 0 {
		t.Fatal("ordinary saturation prevented full revocation")
	}
	for _, device := range c.Devices {
		expect(t, request(s, "GET", "/api/webhook/commands", device.Token, "", nil), 401)
	}
}

func registryRetiredFixture(count, hashes int) []registryRecord {
	rows := make([]registryRecord, 0, count)
	for i := 0; i < count; i++ {
		row := registryRecord{DeviceID: fmt.Sprintf("retired-%d", i), Generation: fmt.Sprintf("%032x", i+1), Origin: "dynamic", State: "revoked", RevokedHashes: []string{}}
		for j := 0; j < hashes; j++ {
			row.RevokedHashes = append(row.RevokedHashes, fmt.Sprintf("%064x", j+1))
		}
		rows = append(rows, row)
	}
	return rows
}

func TestDeviceRegistryReservesRecordHistoryAndByteHeadroom(t *testing.T) {
	for _, kind := range []string{"records", "history", "bytes"} {
		t.Run(kind, func(t *testing.T) {
			c := registryTestConfig(t)
			s := registryTestServer(t, c)
			disk := cloneRegistry(s.registry.disk, time.Now())
			switch kind {
			case "records":
				disk.Records = registryRetiredFixture(maxRegistryRecords-1, 0)
			case "history":
				disk.Records = registryRetiredFixture(1, maxRegistryHashes)
				disk.Records[0].State = "active"
				disk.Records[0].Token = strings.Repeat("x", 43)
			case "bytes":
				disk.Records = registryRetiredFixture(960, maxRegistryHashes)
				encoded, _ := json.Marshal(disk)
				remove := (len(encoded) - (maxRegistryBytes - 256) + 66) / 67
				for i := len(disk.Records) - 1; i >= 0 && remove > 0; i-- {
					count := min(remove, len(disk.Records[i].RevokedHashes))
					disk.Records[i].RevokedHashes = disk.Records[i].RevokedHashes[:len(disk.Records[i].RevokedHashes)-count]
					remove -= count
				}
			}
			encoded, _ := json.Marshal(disk)
			if validateRegistryDisk(disk) != nil || len(encoded) > maxRegistryBytes {
				t.Fatal("candidate itself must fit the hard limits")
			}
			persisted := false
			s.registry.persist = func(registryDisk) error { persisted = true; return nil }
			s.registry.mu.Lock()
			err := s.commitRegistry(disk)
			s.registry.mu.Unlock()
			if err != registryCapacityError || persisted || s.registry.failed {
				t.Fatal("ordinary state consumed required revocation headroom")
			}
		})
	}
}

func TestDeviceRegistryRejectsActiveHistoricalCredentialReuse(t *testing.T) {
	c := registryTestConfig(t)
	s := registryTestServer(t, c)
	added := registryCall(t, s, "add", registryAddBody("retired", 1), 200)
	token := registryCredential(t, s, added.DeviceID, added.Generation)
	registryCall(t, s, "revoke", registryLifeBody(added.DeviceID, added.Generation, 2), 200)
	s.Close()
	c.Devices = append(c.Devices, config.Device{ID: "new-static-identity", Token: token})
	if _, err := New(c); err != registryRevokedCredentialError {
		t.Fatal("active static identity reused a revoked credential", err)
	}
	if err := ValidateDeviceRegistry(c); err != registryRevokedCredentialError {
		t.Fatal("offline validation did not identify historical credential reuse", err)
	}
}
