package control

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func waitForReceiptWaiters(t *testing.T, s *Server, d *device, count int) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for {
		s.mu.Lock()
		got := d.receiptWaiters
		s.mu.Unlock()
		if got == count {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("receipt waiters = %d, want %d", got, count)
		}
		time.Sleep(time.Millisecond)
	}
}

func asyncReceipt(s *Server, path, token string) (context.CancelFunc, <-chan *httptest.ResponseRecorder) {
	ctx, cancel := context.WithCancel(context.Background())
	r := httptest.NewRequest("GET", path, nil).WithContext(ctx)
	r.Header.Set("Authorization", "Bearer "+token)
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		done <- w
	}()
	return cancel, done
}

func receiptWithin(t *testing.T, done <-chan *httptest.ResponseRecorder) *httptest.ResponseRecorder {
	t.Helper()
	select {
	case w := <-done:
		return w
	case <-time.After(time.Second):
		t.Fatal("receipt handler did not return")
		return nil
	}
}

func TestReceiptWaitWakesOnResultAndPreservesLegacyEnvelope(t *testing.T) {
	s := newTestServer(t)
	w := request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"status","params":{}}`, nil)
	expect(t, w, 202)
	var queued map[string]string
	if err := json.Unmarshal(w.Body.Bytes(), &queued); err != nil || queued["receipt_wait_ms"] != "1000" {
		t.Fatalf("invalid backward-compatible enqueue receipt: %s (%v)", w.Body.String(), err)
	}
	id := queued["id"]
	path := "/api/requests?device_id=first&id=" + id
	legacyCancel, legacy := asyncReceipt(s, path, adminToken)
	defer legacyCancel()
	expect(t, receiptWithin(t, legacy), 202)
	waitForReceiptWaiters(t, s, s.devices[0], 0)
	cancel, done := asyncReceipt(s, path+"&wait_ms=1000", adminToken)
	defer cancel()
	waitForReceiptWaiters(t, s, s.devices[0], 1)
	// A held receipt must leave both the queue and unrelated devices usable.
	expect(t, request(s, "GET", "/api/webhook/commands?delivery=ack", secondToken, "", nil), 200)
	body := `{"id":"` + id + `","status":"ok","data":{"volume":35,"text":"<raw>"}}`
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	result := receiptWithin(t, done)
	expect(t, result, 200)
	if result.Body.String() != body || result.Header().Get("Content-Type") != "application/json" {
		t.Fatal("long poll changed the validated player result")
	}
	waitForReceiptWaiters(t, s, s.devices[0], 0)
	// A ready result must not start a new wait.
	expect(t, request(s, "GET", path+"&wait_ms=1000", adminToken, "", nil), 200)
}

func TestReceiptWaitCancellationAndCapacity(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"status","params":{}}`)
	path := "/api/requests?device_id=first&id=" + id + "&wait_ms=1000"
	cancels := make([]context.CancelFunc, 0, 8)
	results := make([]<-chan *httptest.ResponseRecorder, 0, 8)
	defer func() {
		for _, cancel := range cancels {
			cancel()
		}
	}()
	for i := 0; i < 8; i++ {
		cancel, done := asyncReceipt(s, path, adminToken)
		cancels, results = append(cancels, cancel), append(results, done)
	}
	waitForReceiptWaiters(t, s, s.devices[0], 8)
	// The ninth caller gets the existing pending contract immediately.
	overflowCancel, overflow := asyncReceipt(s, path, adminToken)
	defer overflowCancel()
	expect(t, receiptWithin(t, overflow), 202)
	waitForReceiptWaiters(t, s, s.devices[0], 8)
	for i, cancel := range cancels {
		cancel()
		if w := receiptWithin(t, results[i]); w.Body.Len() != 0 {
			t.Fatalf("cancelled wait wrote a response body: %s", w.Body.String())
		}
	}
	waitForReceiptWaiters(t, s, s.devices[0], 0)
	s.mu.Lock()
	pending := len(s.devices[0].queue)
	s.mu.Unlock()
	if pending != 1 {
		t.Fatal("cancelling a receipt cancelled or replayed the command")
	}
	// Released slots can be reused; cancellation must not leave a phantom cap.
	cancel, done := asyncReceipt(s, path, adminToken)
	defer cancel()
	waitForReceiptWaiters(t, s, s.devices[0], 1)
	cancel()
	receiptWithin(t, done)
	waitForReceiptWaiters(t, s, s.devices[0], 0)
}

func TestReceiptWaitValidatesParametersAndBoundsTimeout(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"status","params":{}}`)
	path := "/api/requests?device_id=first&id=" + id
	for _, value := range []string{"", "0", "-1", "1001", "01", "+1", "%2B1", "1.0", "1e3", " 1", "true", "999999999999999999999"} {
		t.Run("value="+value, func(t *testing.T) {
			// Encode literal spaces so httptest.NewRequest sees a valid request target.
			value = strings.ReplaceAll(value, " ", "%20")
			expect(t, request(s, "GET", path+"&wait_ms="+value, adminToken, "", nil), 400)
		})
	}
	for _, suffix := range []string{"&wait_ms=1&wait_ms=2", "&unexpected=1"} {
		expect(t, request(s, "GET", path+suffix, adminToken, "", nil), 400)
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first&wait_ms=1", adminToken, `{"action":"status","params":{}}`, nil), 400)
	expect(t, request(s, "POST", "/api/responses?wait_ms=1", firstToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 400)
	started := time.Now()
	expect(t, request(s, "GET", path+"&wait_ms=20", adminToken, "", nil), 202)
	if elapsed := time.Since(started); elapsed < 15*time.Millisecond || elapsed > time.Second {
		t.Fatalf("unexpected bounded wait: %s", elapsed)
	}
	waitForReceiptWaiters(t, s, s.devices[0], 0)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+strings.Repeat("0", 32)+"&wait_ms=1000", adminToken, "", nil), 404)
}

func TestReceiptWaitKeepsDeviceOwnerAndPermissionScopes(t *testing.T) {
	tokens := []string{strings.Repeat("o", 32), strings.Repeat("p", 32)}
	c := testConfig()
	for i, token := range tokens {
		sum := sha256.Sum256([]byte(token))
		c.Operators = append(c.Operators, config.CommandOperator{ID: []string{"one", "two"}[i],
			CredentialSHA256: hex.EncodeToString(sum[:]), DeviceIDs: []string{"first"}, Actions: []string{"status"}})
	}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	w := request(s, "POST", "/api/requests?device_id=first", tokens[0], `{"action":"status","params":{}}`, nil)
	expect(t, w, 202)
	var receipt map[string]string
	if err := json.Unmarshal(w.Body.Bytes(), &receipt); err != nil {
		t.Fatal(err)
	}
	id := receipt["id"]
	path := "/api/requests?device_id=first&id=" + id + "&wait_ms=1000"
	for _, token := range []string{tokens[1], firstToken, secondToken} {
		expect(t, request(s, "GET", path, token, "", nil), 403)
	}
	expect(t, request(s, "GET", path, "", "", nil), 401)
	expect(t, request(s, "GET", strings.Replace(path, "first", "second", 1), tokens[0], "", nil), 403)
	waitForReceiptWaiters(t, s, s.devices[0], 0)
	cancel, done := asyncReceipt(s, path, tokens[0])
	defer cancel()
	waitForReceiptWaiters(t, s, s.devices[0], 1)
	body := `{"id":"` + id + `","status":"ok","data":{}}`
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	expect(t, receiptWithin(t, done), 200)
	expect(t, request(s, "GET", path, tokens[1], "", nil), 403)
	s.operators[0].actions = map[string]bool{}
	expect(t, request(s, "GET", path, tokens[0], "", nil), 403)
}
