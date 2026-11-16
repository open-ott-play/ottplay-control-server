package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const activityObserverA = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
const activityObserverB = "yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"

func activityScopedServer(t *testing.T) *Server {
	t.Helper()
	c := testConfig()
	for _, row := range []struct{ id, token string }{{"observer-a", activityObserverA}, {"observer-b", activityObserverB}} {
		hash := sha256.Sum256([]byte(row.token))
		c.Operators = append(c.Operators, config.CommandOperator{ID: row.id, CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"command_activity_diagnostics", "play", "playback.pause", "plex_library.list"}})
	}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}
func activitySubmit(t *testing.T, s *Server, token string) string {
	t.Helper()
	w := request(s, "POST", "/api/requests?device_id=first", token, activityRequest, nil)
	expect(t, w, 202)
	var reply map[string]string
	_ = json.Unmarshal(w.Body.Bytes(), &reply)
	return reply["id"]
}
func activityRow(id any, action string, sequence int) map[string]any {
	return map[string]any{"sequence": sequence, "action": action, "age_ms": 0, "result": "ok", "request_id": id}
}
func activityReport(rows ...any) map[string]any {
	return map[string]any{"version": 2, "runtime": "native_123", "scope": "remote_ingress", "entries": rows}
}
func activityReceiptRows(t *testing.T, s *Server, id, token string) []json.RawMessage {
	t.Helper()
	w := request(s, "GET", "/api/requests?device_id=first&id="+id, token, "", nil)
	expect(t, w, 200)
	var outer struct {
		Data struct {
			Entries []json.RawMessage `json:"entries"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &outer); err != nil {
		t.Fatal(err)
	}
	return outer.Data.Entries
}
func seedActivityAudit(s *Server, id, owner, device, action string, at time.Time) {
	s.appendRequestAudit(entry{id: id, owner: owner, permission: action, expires: at.Add(time.Minute)}, device, at)
}
func TestCommandActivityOperatorFiltersForeignAndUnprovenRowsBeforeStorage(t *testing.T) {
	for name, setup := range map[string]func(*Server, time.Time) map[string]any{
		"admin": func(s *Server, now time.Time) map[string]any {
			seedActivityAudit(s, strings.Repeat("2", 32), "admin", "first", "play", now)
			return activityRow(strings.Repeat("2", 32), "play", 2)
		},
		"other operator": func(s *Server, now time.Time) map[string]any {
			seedActivityAudit(s, strings.Repeat("2", 32), "operator:observer-b", "first", "play", now)
			return activityRow(strings.Repeat("2", 32), "play", 2)
		},
		"unknown":     func(s *Server, _ time.Time) map[string]any { return activityRow(strings.Repeat("2", 32), "play", 2) },
		"null legacy": func(s *Server, _ time.Time) map[string]any { return activityRow(nil, "play", 2) },
		"stale": func(s *Server, now time.Time) map[string]any {
			seedActivityAudit(s, strings.Repeat("2", 32), "operator:observer-a", "first", "play", now.Add(-10*time.Minute-time.Nanosecond))
			return activityRow(strings.Repeat("2", 32), "play", 2)
		},
		"other device": func(s *Server, now time.Time) map[string]any {
			seedActivityAudit(s, strings.Repeat("2", 32), "operator:observer-a", "second", "play", now)
			return activityRow(strings.Repeat("2", 32), "play", 2)
		},
		"family mismatch": func(s *Server, now time.Time) map[string]any {
			seedActivityAudit(s, strings.Repeat("2", 32), "operator:observer-a", "first", "playback.pause", now)
			return activityRow(strings.Repeat("2", 32), "play", 2)
		},
		"ungranted operation": func(s *Server, now time.Time) map[string]any {
			seedActivityAudit(s, strings.Repeat("2", 32), "operator:observer-a", "first", "playback.resume", now)
			return activityRow(strings.Repeat("2", 32), "playback", 2)
		},
	} {
		t.Run(name, func(t *testing.T) {
			s := activityScopedServer(t)
			now := time.Now()
			s.now = func() time.Time { return now }
			foreign := setup(s, now)
			own := strings.Repeat("1", 32)
			seedActivityAudit(s, own, "operator:observer-a", "first", "play", now)
			id := activitySubmit(t, s, activityObserverA)
			bound := s.devices[0].queue[0].commandActivity
			if bound == nil || bound.Owner != "operator:observer-a" || bound.Device != "first" {
				t.Fatal("trusted origin not bound")
			}
			full := screenshotEnvelope(id, "ok", activityReport(activityRow(own, "play", 1), foreign))
			expect(t, request(s, "POST", "/api/responses", firstToken, full, nil), 200)
			stored := string(s.devices[0].results[id].data)
			if strings.Contains(stored, strings.Repeat("2", 32)) {
				t.Fatal("foreign identifier retained")
			}
			rows := activityReceiptRows(t, s, id, activityObserverA)
			if len(rows) != 1 || !strings.Contains(string(rows[0]), own) {
				t.Fatal("own join lost or foreign row visible")
			}
			// A repeated complete device-wide reply only acknowledges the retained subset.
			expect(t, request(s, "POST", "/api/responses", firstToken, full, nil), 200)
			if got := activityReceiptRows(t, s, id, activityObserverA); len(got) != 1 || strings.Contains(string(s.devices[0].results[id].data), strings.Repeat("2", 32)) {
				t.Fatal("duplicate widened retained receipt")
			}
			if got := activityReceiptRows(t, s, id, adminToken); len(got) != 1 {
				t.Fatal("admin restored foreign rows from operator receipt")
			}
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, activityObserverB, "", nil), 403)
		})
	}
}
func TestCommandActivityOperatorCurrentPermissionsAppliedAtStorageAndEveryRead(t *testing.T) {
	for _, stage := range []string{"before storage", "after storage"} {
		for _, revoke := range []string{"action", "device", "operator", "audit stale", "audit pruned"} {
			t.Run(stage+"/"+revoke, func(t *testing.T) {
				s := activityScopedServer(t)
				now := time.Now()
				s.now = func() time.Time { return now }
				own := strings.Repeat("1", 32)
				seedActivityAudit(s, own, "operator:observer-a", "first", "play", now)
				id := activitySubmit(t, s, activityObserverA)
				full := screenshotEnvelope(id, "ok", activityReport(activityRow(own, "play", 1)))
				revokeNow := func() {
					switch revoke {
					case "action":
						delete(s.operators[0].actions, "play")
					case "device":
						delete(s.operators[0].devices, "first")
					case "operator":
						s.operators = s.operators[1:]
					case "audit stale":
						for i := range s.requestAudit {
							s.requestAudit[i].accepted = now.Add(-10*time.Minute - time.Nanosecond)
						}
					case "audit pruned":
						s.requestAudit = nil
					}
				}
				if stage == "before storage" {
					revokeNow()
				}
				expect(t, request(s, "POST", "/api/responses", firstToken, full, nil), 200)
				stored := append([]byte(nil), s.devices[0].results[id].data...)
				if stage == "after storage" {
					revokeNow()
				}
				// Admin reading the operator-origin request must honor its bound owner too.
				if rows := activityReceiptRows(t, s, id, adminToken); len(rows) != 0 {
					t.Fatal("revoked or unproven row survived current filtering")
				}
				if string(stored) != string(s.devices[0].results[id].data) {
					t.Fatal("GET mutated retained subset")
				}
				if stage == "before storage" && strings.Contains(string(stored), own) {
					t.Fatal("forbidden row retained")
				}
				if revoke == "device" {
					expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, activityObserverA, "", nil), 403)
				}
				if revoke == "operator" {
					expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, activityObserverA, "", nil), 401)
				}
				if revoke != "device" && revoke != "operator" {
					if rows := activityReceiptRows(t, s, id, activityObserverA); len(rows) != 0 {
						t.Fatal("operator read bypassed original action scope")
					}
				}
				expect(t, request(s, "POST", "/api/responses", firstToken, full, nil), 200)
				if rows := activityReceiptRows(t, s, id, adminToken); len(rows) != 0 {
					t.Fatal("duplicate restored revoked activity")
				}
			})
		}
	}
}
func TestCommandActivityUnfilteredInputStillValidatedAndBoundOwnerFailsClosed(t *testing.T) {
	for _, bad := range []string{"malformed foreign field", "duplicate foreign field", "oversize", "missing owner"} {
		t.Run(bad, func(t *testing.T) {
			s := activityScopedServer(t)
			id := activitySubmit(t, s, activityObserverA)
			data := activityReport(activityRow(strings.Repeat("2", 32), "play", 1))
			full := screenshotEnvelope(id, "ok", data)
			switch bad {
			case "malformed foreign field":
				data["entries"].([]any)[0].(map[string]any)["source_url"] = "fixture-secret"
				full = screenshotEnvelope(id, "ok", data)
			case "duplicate foreign field":
				full = strings.Replace(full, `"sequence":1`, `"sequence":1,"sequence":1`, 1)
			case "oversize":
				full = strings.Repeat(" ", maxCommandActivityBytes) + full
			case "missing owner":
				s.devices[0].queue[0].commandActivity.Owner = ""
			}
			response := request(s, "POST", "/api/responses", firstToken, full, nil)
			expect(t, response, 400)
			if len(s.devices[0].results) != 0 || len(s.devices[0].queue) != 1 || strings.Contains(response.Body.String(), "fixture-secret") {
				t.Fatal("unvalidated foreign bytes consumed or exposed")
			}
		})
	}
}
func TestCommandActivityAdminBytesAndClosedErrorsRemainUnchanged(t *testing.T) {
	s := activityScopedServer(t)
	id := activitySubmit(t, s, adminToken)
	data, _ := json.MarshalIndent(activityReport(activityRow(nil, "play", 1), activityRow(strings.Repeat("2", 32), "plex_library", 2)), "", "  ")
	full := "{\n \"id\":\"" + id + "\",\n \"status\":\"ok\",\n \"data\":" + string(data) + "\n}"
	expect(t, request(s, "POST", "/api/responses", firstToken, full, nil), 200)
	if string(s.devices[0].results[id].data) != full {
		t.Fatal("admin-origin bytes rewritten")
	}
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, got, 200)
	if got.Body.String() != full {
		t.Fatal("admin receipt bytes rewritten")
	}
	id = activitySubmit(t, s, activityObserverA)
	negative := screenshotEnvelope(id, "rejected", map[string]any{"version": 2, "runtime": "receiver_current", "error": "runtime_mismatch"})
	expect(t, request(s, "POST", "/api/responses", firstToken, negative, nil), 200)
	got = request(s, "GET", "/api/requests?device_id=first&id="+id, activityObserverA, "", nil)
	expect(t, got, 200)
	if got.Body.String() != negative {
		t.Fatal("closed error rewritten")
	}
}
