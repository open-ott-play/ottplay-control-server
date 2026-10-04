package control

import (
	"io"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type diagnosticSlowBody struct {
	reads   *atomic.Int32
	release <-chan struct{}
}

func (b diagnosticSlowBody) Read([]byte) (int, error) { b.reads.Add(1); <-b.release; return 0, io.EOF }
func (b diagnosticSlowBody) Close() error             { return nil }

func TestDiagnosticsPreauthenticationRejectsSlowBodiesBeforeAdmission(t *testing.T) {
	s, _ := diagnosticServer(t)
	rt, token := diagnosticRegister(t, s)
	started := diagnosticStart(t, s, rt, "start")
	diagnosticAck(t, s, rt, token, started, 200)
	budgetBefore := s.diagnostics.globalControl.used
	var reads atomic.Int32
	release := make(chan struct{})
	var once sync.Once
	unblock := func() { once.Do(func() { close(release) }) }
	defer unblock()
	responses := make(chan *httptest.ResponseRecorder, 64)
	var wg sync.WaitGroup
	for i := 0; i < 64; i++ {
		wg.Go(func() {
			r := httptest.NewRequest("POST", diagnosticsPrefix+"/repairs", nil)
			r.Body = diagnosticSlowBody{&reads, release}
			r.ContentLength = 100
			r.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			s.ServeHTTP(w, r)
			responses <- w
		})
	}
	// An unauthenticated body must never be read, even if it could block forever.
	for i := 0; i < 64; i++ {
		select {
		case w := <-responses:
			expect(t, w, 401)
		case <-time.After(time.Second):
			unblock()
			wg.Wait()
			t.Fatal("unauthenticated body consumed admission or blocked response")
		}
	}
	wg.Wait()
	if reads.Load() != 0 || len(s.diagnostics.controlSlots) != 0 || s.diagnostics.globalControl.used != budgetBefore {
		t.Fatal("unauthenticated requests read bodies or used control resources")
	}
	sid := started["session_id"].(string)
	stop := diagnosticCall(t, s, "POST", "/sessions/"+sid+"/stop", diagnosticOperatorToken, map[string]any{"server_epoch": s.diagnostics.epoch, "idempotency_key": "stop", "reason": "operator"}, 202)
	diagnosticAck(t, s, rt, token, stop, 200)
}

type diagnosticBodyCallback struct {
	before func()
	body   io.Reader
}

func (b *diagnosticBodyCallback) Read(p []byte) (int, error) {
	if b.before != nil {
		f := b.before
		b.before = nil
		f()
	}
	return b.body.Read(p)
}
func (b *diagnosticBodyCallback) Close() error { return nil }

func TestDiagnosticsPreauthenticationRevalidatesAfterBodyRead(t *testing.T) {
	s, _ := diagnosticServer(t)
	rt, token := diagnosticRegister(t, s)
	payload := `{"runtime_id":"` + rt + `","poll_seq":1,"last_control_revision":0,"consent":{"granted":true,"epoch":"grant"}}`
	r := httptest.NewRequest("POST", diagnosticsPrefix+"/poll", nil)
	r.Header.Set("Authorization", "Bearer "+token)
	r.Header.Set("Content-Type", "application/json")
	r.ContentLength = int64(len(payload))
	r.Body = &diagnosticBodyCallback{before: func() { s.diagnostics.mu.Lock(); delete(s.diagnostics.runtimes, rt); s.diagnostics.mu.Unlock() }, body: strings.NewReader(payload)}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	expect(t, w, 401)
	if s.diagnostics.runtimes[rt] != nil {
		t.Fatal("credential revoked during body read was resurrected")
	}
}

func TestDiagnosticsPreauthenticationChecksRouteScopeBeforeBody(t *testing.T) {
	s, _ := diagnosticServer(t)
	var reads atomic.Int32
	released := make(chan struct{})
	close(released)
	r := httptest.NewRequest("POST", diagnosticsPrefix+"/repairs", nil)
	r.Body = diagnosticSlowBody{&reads, released}
	r.ContentLength = 100
	r.Header.Set("Authorization", "Bearer "+diagnosticOperatorToken)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	expect(t, w, 404)
	if reads.Load() != 0 || len(s.diagnostics.controlSlots) != 0 {
		t.Fatal("out-of-scope operator body was read")
	}
}
