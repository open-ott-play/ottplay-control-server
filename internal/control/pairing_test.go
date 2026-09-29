package control

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const publicController = "https://controller.example.test/ott-control"

var homeServer = discoveredServer{ID: "home._ottplay-ctrl._tcp.example.test.", Domain: "example.test", Address: publicController}

type claim struct {
	ID      string `json:"id"`
	Secret  string `json:"secret"`
	Code    string `json:"code"`
	Expires int    `json:"expires_in"`
}

func pairingServer(t *testing.T) *Server {
	t.Helper()
	c := testConfig()
	c.Discovery = &config.Discovery{Domain: "example.test", Nameserver: "127.0.0.1:53", PublicURL: publicController}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
		return []discoveredServer{homeServer}, 30 * time.Second, nil
	}
	return s
}
func startPairing(t *testing.T, s *Server) claim {
	t.Helper()
	r := request(s, "POST", "/api/pairings", "", `{"device_id":"first"}`, nil)
	expect(t, r, 201)
	var c claim
	if err := json.Unmarshal(r.Body.Bytes(), &c); err != nil {
		t.Fatal(err)
	}
	if len(c.ID) != 32 || len(c.Secret) != 43 || len(c.Code) != 8 || c.Expires != 600 {
		t.Fatal("invalid pairing identifiers or expiry")
	}
	return c
}
func approve(t *testing.T, s *Server, c claim) *httptest.ResponseRecorder {
	t.Helper()
	return request(s, "POST", "/api/pairings/approve", adminToken, fmt.Sprintf(`{"id":%q,"code":%q}`, c.ID, c.Code), nil)
}
func redeem(s *Server, c claim) *httptest.ResponseRecorder {
	return request(s, "GET", "/api/pairings?id="+c.ID, c.Secret, "", nil)
}

func TestPairingApprovalAndPrivateClaim(t *testing.T) {
	s := pairingServer(t)
	c := startPairing(t, s)
	pending := redeem(s, c)
	expect(t, pending, 202)
	if strings.Contains(pending.Body.String(), firstToken) {
		t.Fatal("unapproved token leaked")
	}
	list := request(s, "GET", "/api/pairings", adminToken, "", nil)
	expect(t, list, 200)
	if !strings.Contains(list.Body.String(), c.Code) || !strings.Contains(list.Body.String(), publicController) || strings.Contains(list.Body.String(), c.Secret) || strings.Contains(list.Body.String(), firstToken) || strings.Contains(list.Body.String(), adminToken) {
		t.Fatal("unsafe approval listing")
	}
	expect(t, approve(t, s, c), 200)
	expect(t, approve(t, s, c), 409)
	for i := 0; i < 2; i++ {
		result := redeem(s, c)
		expect(t, result, 200)
		var data map[string]string
		_ = json.Unmarshal(result.Body.Bytes(), &data)
		if data["token"] != firstToken || data["device_id"] != "first" || data["address"] != publicController || data["status"] != "approved" || strings.Contains(result.Body.String(), adminToken) || strings.Contains(result.Body.String(), secondToken) {
			t.Fatal("wrong approved device claim")
		}
	}
	for _, wrong := range []string{"", adminToken, firstToken, secondToken, strings.Repeat("x", 43)} {
		expect(t, request(s, "GET", "/api/pairings?id="+c.ID, wrong, "", nil), 401)
	}
	expect(t, request(s, "GET", "/api/pairings", firstToken, "", nil), 403)
	expect(t, request(s, "POST", "/api/pairings/approve", c.Secret, fmt.Sprintf(`{"id":%q,"code":%q}`, c.ID, c.Code), nil), 403)
}

func TestBootstrapDefaultsCORSAndStrictInputs(t *testing.T) {
	off := newTestServer(t)
	for _, path := range []string{"/api/discovery", "/api/pairings", "/api/pairings/approve"} {
		expect(t, request(off, "GET", path, "", "", nil), 404)
	}
	s := pairingServer(t)
	result := request(s, "GET", "/api/discovery", "", "", map[string]string{"Origin": "https://player.example"})
	expect(t, result, 200)
	if result.Header().Get("Cache-Control") != "no-store" || result.Header().Get("Access-Control-Allow-Origin") != "https://player.example" || !strings.Contains(result.Body.String(), publicController+"/api/pairings") || strings.Contains(result.Body.String(), firstToken) {
		t.Fatal("incorrect public discovery response")
	}
	for _, path := range []string{"/api/discovery", "/api/pairings"} {
		expect(t, request(s, "GET", path, "", "", map[string]string{"Origin": "https://evil.example"}), 403)
	}
	expect(t, request(s, "OPTIONS", "/api/pairings", "", "", map[string]string{"Origin": "https://player.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type"}), 204)
	expect(t, request(s, "GET", "/api/discovery?domain=evil.example", "", "", nil), 400)
	expect(t, request(s, "GET", "/api/discovery?id=whatever", "", "", nil), 400)
	expect(t, request(s, "POST", "/api/pairings", "", `{"device_id":"unregistered"}`, nil), 400)
	expect(t, request(s, "POST", "/api/pairings", "", `{"device_id":"first","token":"supplied"}`, nil), 400)
	expect(t, request(s, "POST", "/api/pairings?domain=evil.example", "", `{"device_id":"first"}`, nil), 400)
	expect(t, request(s, "POST", "/api/pairings", "", `{"device_id":"first","server_id":"untrusted"}`, nil), 409)
}

func TestHostedDiscoveryOnlyNominatesConfiguredController(t *testing.T) {
	foreign := discoveredServer{ID: "foreign._ottplay-ctrl._tcp.example.test.", Domain: "example.test", Address: "https://foreign.example.test/ott-control"}
	otherPath := discoveredServer{ID: "path._ottplay-ctrl._tcp.example.test.", Domain: "example.test", Address: publicController + "-other"}
	for _, tc := range []struct {
		name    string
		servers []discoveredServer
		want    []discoveredServer
	}{
		{name: "empty", servers: []discoveredServer{}, want: []discoveredServer{}},
		{name: "foreign only", servers: []discoveredServer{foreign, otherPath}, want: []discoveredServer{}},
		{name: "mixed", servers: []discoveredServer{foreign, homeServer, otherPath}, want: []discoveredServer{homeServer}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := pairingServer(t)
			// The configured URL and DNS descriptors use the same canonical base.
			s.discovery = newDiscovery(config.Discovery{PublicURL: "https://CONTROLLER.example.test:443/ott-control/"})
			s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
				return tc.servers, 30 * time.Second, nil
			}
			result := request(s, "GET", "/api/discovery", "", "", nil)
			expect(t, result, 200)
			var body struct {
				Version    int                `json:"version"`
				Servers    []discoveredServer `json:"servers"`
				PairingURL string             `json:"pairing_url"`
			}
			if err := json.Unmarshal(result.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if body.Version != 1 || body.PairingURL != publicController+"/api/pairings" || body.Servers == nil || len(body.Servers) != len(tc.want) {
				t.Fatalf("incorrect pinned discovery response: %s", result.Body.String())
			}
			for i, server := range body.Servers {
				if server != tc.want[i] {
					t.Fatalf("unexpected nominated controller: %+v", server)
				}
			}
			// The public filter must not mutate the DNS cache used by pairing.
			all, err := s.discovery.resolve(context.Background(), false)
			if err != nil || len(all) != len(tc.servers) {
				t.Fatalf("internal discovery changed: %v, %v", all, err)
			}
			for i, server := range all {
				if server != tc.servers[i] {
					t.Fatalf("internal descriptor changed: %+v", server)
				}
			}
		})
	}
}

func TestPairingRejectsChangedDescriptorAndAnotherController(t *testing.T) {
	for _, stage := range []string{"approval", "redemption"} {
		for _, change := range []string{"different", "absent"} {
			t.Run(stage+"/"+change, func(t *testing.T) {
				s := pairingServer(t)
				c := startPairing(t, s)
				if stage == "redemption" {
					expect(t, approve(t, s, c), 200)
				}
				s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
					if change == "absent" {
						return []discoveredServer{}, 30 * time.Second, nil
					}
					v := homeServer
					v.Address = "https://other.example.test"
					return []discoveredServer{v}, 30 * time.Second, nil
				}
				if stage == "approval" {
					expect(t, approve(t, s, c), 409)
				} else {
					expect(t, redeem(s, c), 409)
				}
				expect(t, redeem(s, c), 401)
			})
		}
	}
	s := pairingServer(t)
	other := homeServer
	other.ID = "other._ottplay-ctrl._tcp.example.test."
	other.Address = "https://other.example.test"
	s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
		return []discoveredServer{homeServer, other}, 30 * time.Second, nil
	}
	expect(t, request(s, "POST", "/api/pairings", "", `{"device_id":"first"}`, nil), 409)
	expect(t, request(s, "POST", "/api/pairings", "", fmt.Sprintf(`{"device_id":"first","server_id":%q}`, other.ID), nil), 409)
	expect(t, request(s, "POST", "/api/pairings", "", fmt.Sprintf(`{"device_id":"first","server_id":%q}`, homeServer.ID), nil), 201)
}

func TestPairingLookupErrorPreservesApprovalAndRedemption(t *testing.T) {
	for _, stage := range []string{"approval", "redemption"} {
		for _, lookupError := range []error{errors.New("temporary DNS failure"), context.Canceled, context.DeadlineExceeded} {
			t.Run(stage+"/"+lookupError.Error(), func(t *testing.T) {
				s := pairingServer(t)
				c := startPairing(t, s)
				if stage == "redemption" {
					expect(t, approve(t, s, c), 200)
				}
				original := *s.pairings[c.ID]
				s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
					return nil, 0, lookupError
				}
				var response *httptest.ResponseRecorder
				if stage == "approval" {
					response = approve(t, s, c)
				} else {
					response = redeem(s, c)
				}
				expect(t, response, 503)
				if strings.Contains(response.Body.String(), firstToken) || strings.Contains(response.Body.String(), c.Secret) {
					t.Fatal("unavailable lookup disclosed a credential")
				}
				if p := s.pairings[c.ID]; p == nil || *p != original {
					t.Fatal("temporary lookup error changed or removed the pairing")
				}
				s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
					return []discoveredServer{homeServer}, 30 * time.Second, nil
				}
				if stage == "approval" {
					expect(t, redeem(s, c), 202)
					expect(t, approve(t, s, c), 200)
				}
				expect(t, redeem(s, c), 200)
			})
		}
	}
}

func TestPairingStillExpiresOrCancelsDuringLookup(t *testing.T) {
	for _, stage := range []string{"approval", "redemption"} {
		for _, invalidation := range []string{"expiry", "cancel"} {
			t.Run(stage+"/"+invalidation, func(t *testing.T) {
				s := pairingServer(t)
				var now atomic.Int64
				now.Store(time.Now().UnixNano())
				s.now = func() time.Time { return time.Unix(0, now.Load()) }
				c := startPairing(t, s)
				if stage == "redemption" {
					expect(t, approve(t, s, c), 200)
				}
				started, release := make(chan struct{}), make(chan struct{})
				var releaseOnce sync.Once
				t.Cleanup(func() { releaseOnce.Do(func() { close(release) }) })
				s.discovery.lookup = func(ctx context.Context) ([]discoveredServer, time.Duration, error) {
					close(started)
					select {
					case <-release:
						return []discoveredServer{homeServer}, 30 * time.Second, nil
					case <-ctx.Done():
						return nil, 0, ctx.Err()
					}
				}
				var result *httptest.ResponseRecorder
				done := make(chan struct{})
				go func() {
					defer close(done)
					if stage == "approval" {
						result = approve(t, s, c)
					} else {
						result = redeem(s, c)
					}
				}()
				waitForDiscovery(t, started)
				if invalidation == "expiry" {
					now.Add(int64(pairingTTL))
				} else {
					expect(t, request(s, "DELETE", "/api/pairings?id="+c.ID, c.Secret, "", nil), 200)
				}
				releaseOnce.Do(func() { close(release) })
				waitForDiscovery(t, done)
				want := 401
				if stage == "approval" {
					want = 404
				}
				expect(t, result, want)
				if strings.Contains(result.Body.String(), firstToken) || s.pairings[c.ID] != nil {
					t.Fatal("lookup completion revived an invalidated pairing")
				}
			})
		}
	}
}

func TestPairingCancelledWaitPreservesClaim(t *testing.T) {
	for _, stage := range []string{"approval", "redemption"} {
		t.Run(stage, func(t *testing.T) {
			s := pairingServer(t)
			c := startPairing(t, s)
			if stage == "redemption" {
				expect(t, approve(t, s, c), 200)
			}
			original := *s.pairings[c.ID]
			started, release := make(chan struct{}), make(chan struct{})
			var startOnce, releaseOnce sync.Once
			t.Cleanup(func() { releaseOnce.Do(func() { close(release) }) })
			s.discovery.lookup = func(ctx context.Context) ([]discoveredServer, time.Duration, error) {
				startOnce.Do(func() { close(started) })
				select {
				case <-release:
					return []discoveredServer{homeServer}, 30 * time.Second, nil
				case <-ctx.Done():
					return nil, 0, ctx.Err()
				}
			}
			r := httptest.NewRequest("GET", "/api/pairings?id="+c.ID, nil)
			r.Header.Set("Authorization", "Bearer "+c.Secret)
			if stage == "approval" {
				r = httptest.NewRequest("POST", "/api/pairings/approve", strings.NewReader(fmt.Sprintf(`{"id":%q,"code":%q}`, c.ID, c.Code)))
				r.Header.Set("Content-Type", "application/json")
				r.Header.Set("Authorization", "Bearer "+adminToken)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result, done := httptest.NewRecorder(), make(chan struct{})
			go func() { defer close(done); s.ServeHTTP(result, r.WithContext(ctx)) }()
			waitForDiscovery(t, started)
			cancel()
			select {
			case <-done:
			case <-time.After(time.Second):
				t.Fatal("cancelled pairing request kept waiting for DNS")
			}
			expect(t, result, 503)
			if p := s.pairings[c.ID]; p == nil || *p != original || strings.Contains(result.Body.String(), firstToken) {
				t.Fatal("cancelled wait changed the claim or disclosed a token")
			}
			releaseOnce.Do(func() { close(release) })
			if stage == "approval" {
				expect(t, approve(t, s, c), 200)
			}
			expect(t, redeem(s, c), 200)
		})
	}
}

func TestPairingExpiryRestartCodeAndCapacity(t *testing.T) {
	s := pairingServer(t)
	now := time.Now()
	s.now = func() time.Time { return now }
	c := startPairing(t, s)
	now = now.Add(pairingTTL)
	expect(t, approve(t, s, c), 404)
	expect(t, redeem(s, c), 401)
	fresh := pairingServer(t)
	expect(t, redeem(fresh, c), 401)
	s = pairingServer(t)
	c = startPairing(t, s)
	c.Code = "00000000"
	for i := 0; i < 5; i++ {
		expect(t, approve(t, s, c), 403)
	}
	expect(t, approve(t, s, c), 404)
	s = pairingServer(t)
	startPairing(t, s)
	startPairing(t, s)
	expect(t, request(s, "POST", "/api/pairings", "", `{"device_id":"first"}`, nil), 429)
}

func TestPairingConcurrentApprovalIsOneTime(t *testing.T) {
	s := pairingServer(t)
	c := startPairing(t, s)
	statuses := make(chan int, 12)
	var wait sync.WaitGroup
	for i := 0; i < 12; i++ {
		wait.Add(1)
		go func() { defer wait.Done(); statuses <- approve(t, s, c).Code }()
	}
	wait.Wait()
	close(statuses)
	approved := 0
	for status := range statuses {
		if status == 200 {
			approved++
		} else if status != 409 {
			t.Fatalf("unexpected status %d", status)
		}
	}
	if approved != 1 {
		t.Fatalf("approved %d times", approved)
	}
	expect(t, redeem(s, c), 200)
}

func TestPairingCancellationNeedsOwnSecretAndReleasesCapacity(t *testing.T) {
	s := pairingServer(t)
	first := startPairing(t, s)
	second := startPairing(t, s)
	for _, secret := range []string{"", adminToken, firstToken, second.Secret} {
		expect(t, request(s, "DELETE", "/api/pairings?id="+first.ID, secret, "", nil), 401)
	}
	expect(t, request(s, "DELETE", "/api/pairings", adminToken, "", nil), 400)
	expect(t, request(s, "OPTIONS", "/api/pairings?id="+first.ID, "", "", map[string]string{"Origin": "https://player.example", "Access-Control-Request-Method": "DELETE", "Access-Control-Request-Headers": "Authorization"}), 204)
	s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
		t.Fatal("cancel queried DNS")
		return nil, 0, nil
	}
	expect(t, request(s, "DELETE", "/api/pairings?id="+first.ID, first.Secret, "", nil), 200)
	expect(t, redeem(s, first), 401)
	expect(t, redeem(s, second), 202)
	s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
		return []discoveredServer{homeServer}, 30 * time.Second, nil
	}
	startPairing(t, s)
}

func TestNullOriginBootstrapNeverGrantsAdministratorRoutes(t *testing.T) {
	s := pairingServer(t)
	s.allowNull = true
	headers := map[string]string{"Origin": "null"}
	expect(t, request(s, "GET", "/api/discovery", "", "", headers), 200)
	response := request(s, "POST", "/api/pairings", "", `{"device_id":"first"}`, headers)
	expect(t, response, 201)
	var c claim
	_ = json.Unmarshal(response.Body.Bytes(), &c)
	expect(t, request(s, "GET", "/api/pairings?id="+c.ID, c.Secret, "", headers), 202)
	expect(t, request(s, "GET", "/api/pairings", adminToken, "", headers), 403)
	expect(t, request(s, "POST", "/api/pairings/approve", adminToken, fmt.Sprintf(`{"id":%q,"code":%q}`, c.ID, c.Code), headers), 403)
	expect(t, request(s, "OPTIONS", "/api/pairings/approve", "", "", map[string]string{"Origin": "null", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization, Content-Type"}), 403)
	expect(t, request(s, "DELETE", "/api/pairings?id="+c.ID, c.Secret, "", headers), 200)
}

func TestPairingLimitsAndClaimQueryDoNotLeakCredentials(t *testing.T) {
	s := pairingServer(t)
	now := time.Now()
	s.now = func() time.Time { return now }
	// Cancelled attempts still consume the creation allowance, preventing churn.
	for i := 0; i < 3; i++ {
		c := startPairing(t, s)
		expect(t, request(s, "DELETE", "/api/pairings?id="+c.ID, c.Secret, "", nil), 200)
	}
	expect(t, request(s, "POST", "/api/pairings", "", `{"device_id":"first"}`, nil), 429)
	now = now.Add(time.Minute)
	c := startPairing(t, s)
	expect(t, request(s, "GET", "/api/pairings?id="+c.ID+"&secret="+c.Secret, "", "", nil), 400)
	expect(t, request(s, "GET", "/api/pairings?id="+c.ID+"&id="+c.ID, c.Secret, "", nil), 400)
	expect(t, request(s, "GET", "/api/pairings?id="+c.ID, c.Secret, "", nil), 202)
}
