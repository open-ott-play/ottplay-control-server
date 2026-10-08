package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

func randomID() string {
	b := make([]byte, 12)
	if _, e := rand.Read(b); e != nil {
		panic(e)
	}
	return hex.EncodeToString(b)
}
func appPID() (int, error) {
	entries, e := os.ReadDir("/proc")
	if e != nil {
		return 0, e
	}
	for _, entry := range entries {
		pid, e := strconv.Atoi(entry.Name())
		if e != nil {
			continue
		}
		b, e := os.ReadFile("/proc/" + entry.Name() + "/cmdline")
		if e == nil && strings.Split(string(b), "\x00")[0] == packageName {
			return pid, nil
		}
	}
	return 0, errors.New("player process unavailable")
}

type CDP struct{ Origin string }
type socket struct {
	net.Conn
	r *bufio.Reader
}

func (s *socket) Close() error {
	// KitKat retains DevTools ownership unless the WebSocket closes cleanly.
	_ = s.SetWriteDeadline(time.Now().Add(100 * time.Millisecond))
	if _, err := s.Write([]byte{0x88, 0x82, 0, 0, 0, 0, 3, 232}); err == nil {
		// Wait for the peer to release its single debugger attachment before
		// opening the next connection. A TCP close immediately after sending
		// the frame races the next /json request on Chromium 30.
		_ = s.SetReadDeadline(time.Now().Add(time.Second))
		for i := 0; i < 8; i++ {
			if _, err := s.receive(); err != nil {
				break
			}
		}
	}
	return s.Conn.Close()
}
func (s *socket) send(v any) error {
	b, e := json.Marshal(v)
	if e != nil {
		return e
	}
	if len(b) > 1024*1024 {
		return errors.New("oversized CDP request")
	}
	h := []byte{0x81}
	n := len(b)
	if n < 126 {
		h = append(h, byte(n)|0x80)
	} else if n < 65536 {
		h = append(h, 0xfe, byte(n>>8), byte(n))
	} else {
		h = append(h, 0xff, 0, 0, 0, 0, byte(n>>24), byte(n>>16), byte(n>>8), byte(n))
	}
	mask := make([]byte, 4)
	if _, e = rand.Read(mask); e != nil {
		return e
	}
	h = append(h, mask...)
	for i := range b {
		b[i] ^= mask[i%4]
	}
	_, e = s.Write(append(h, b...))
	return e
}
func (s *socket) receive() (map[string]any, error) {
	var message []byte
	for {
		h := make([]byte, 2)
		if _, e := io.ReadFull(s.r, h); e != nil {
			return nil, e
		}
		n := uint64(h[1] & 127)
		if h[1]&128 != 0 {
			return nil, errors.New("masked server frame")
		}
		if n == 126 {
			b := make([]byte, 2)
			if _, e := io.ReadFull(s.r, b); e != nil {
				return nil, e
			}
			n = uint64(binary.BigEndian.Uint16(b))
		} else if n == 127 {
			b := make([]byte, 8)
			if _, e := io.ReadFull(s.r, b); e != nil {
				return nil, e
			}
			n = binary.BigEndian.Uint64(b)
		}
		if n > 2*1024*1024 || uint64(len(message))+n > 2*1024*1024 {
			return nil, errors.New("oversized CDP frame")
		}
		b := make([]byte, int(n))
		if _, e := io.ReadFull(s.r, b); e != nil {
			return nil, e
		}
		if h[0]&15 == 8 {
			return nil, io.EOF
		}
		if h[0]&15 != 0 && h[0]&15 != 1 {
			return nil, errors.New("unexpected CDP frame")
		}
		message = append(message, b...)
		if h[0]&128 != 0 {
			var v map[string]any
			e := json.Unmarshal(message, &v)
			return v, e
		}
	}
}
func (c *CDP) connect(ctx context.Context) (*socket, error) {
	pid, e := appPID()
	if e != nil {
		return nil, e
	}
	address := "\x00webview_devtools_remote_" + strconv.Itoa(pid)
	dial := func(ctx context.Context, _, _ string) (net.Conn, error) {
		var d net.Dialer
		return d.DialContext(ctx, "unix", address)
	}
	client := &http.Client{Transport: &http.Transport{DialContext: dial, DisableKeepAlives: true}, Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("CDP redirect refused") }}
	req, _ := http.NewRequestWithContext(ctx, "GET", "http://localhost/json", nil)
	resp, e := client.Do(req)
	if e != nil {
		return nil, e
	}
	defer resp.Body.Close()
	var targets []struct {
		URL string `json:"url"`
		WS  string `json:"webSocketDebuggerUrl"`
	}
	if e = json.NewDecoder(io.LimitReader(resp.Body, 65536)).Decode(&targets); e != nil {
		return nil, e
	}
	ws := ""
	for _, t := range targets {
		if sameOrigin(c.Origin, t.URL) && t.WS != "" {
			if ws != "" {
				return nil, errors.New("ambiguous player WebView")
			}
			ws = t.WS
		}
	}
	u, e := url.Parse(ws)
	if e != nil || u.Path == "" {
		return nil, errors.New("trusted player page unavailable")
	}
	conn, e := dial(ctx, "", "")
	if e != nil {
		return nil, e
	}
	deadline := time.Now().Add(5 * time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	conn.SetDeadline(deadline)
	key := make([]byte, 16)
	rand.Read(key)
	k := base64.StdEncoding.EncodeToString(key)
	fmt.Fprintf(conn, "GET %s HTTP/1.1\r\nHost: localhost:9222\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n", u.RequestURI(), k)
	reader := bufio.NewReader(conn)
	response, e := http.ReadResponse(reader, &http.Request{Method: "GET"})
	if e != nil {
		conn.Close()
		return nil, e
	}
	digest := sha1.Sum([]byte(k + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
	if response.StatusCode != 101 || response.Header.Get("Sec-WebSocket-Accept") != base64.StdEncoding.EncodeToString(digest[:]) {
		conn.Close()
		return nil, errors.New("CDP handshake rejected")
	}
	return &socket{conn, reader}, nil
}
func sameOrigin(a, b string) bool {
	u, e := url.Parse(a)
	v, f := url.Parse(b)
	return e == nil && f == nil && u.Scheme == v.Scheme && strings.EqualFold(u.Host, v.Host) && v.User == nil
}
func object(v any) map[string]any { m, _ := v.(map[string]any); return m }
func (c *CDP) evaluate(ctx context.Context, expression string) (json.RawMessage, error) {
	s, e := c.connect(ctx)
	if e != nil {
		return nil, e
	}
	defer s.Close()
	contexts := []map[string]any{}
	if e = s.send(map[string]any{"id": 1, "method": "Runtime.enable"}); e != nil {
		return nil, e
	}
	if e = s.send(map[string]any{"id": 2, "method": "Page.getResourceTree"}); e != nil {
		return nil, e
	}
	frameID := ""
	sent := false
	for count := 0; count < 300; count++ {
		msg, e := s.receive()
		if e != nil {
			return nil, e
		}
		if msg["method"] == "Runtime.executionContextCreated" {
			contexts = append(contexts, object(object(msg["params"])["context"]))
		}
		if msg["id"] == float64(2) {
			frame := object(object(object(msg["result"])["frameTree"])["frame"])
			address, _ := frame["url"].(string)
			if !sameOrigin(c.Origin, address) {
				return nil, errors.New("player origin changed")
			}
			frameID, _ = frame["id"].(string)
		}
		if !sent && frameID != "" {
			for _, candidate := range contexts {
				aux := object(candidate["auxData"])
				fid := candidate["frameId"]
				if fid == nil {
					fid = aux["frameId"]
				}
				if fid != frameID || candidate["isPageContext"] == false || aux["isDefault"] == false {
					continue
				}
				e = s.send(map[string]any{"id": 3, "method": "Runtime.evaluate", "params": map[string]any{"expression": expression, "contextId": candidate["id"], "returnByValue": true}})
				if e != nil {
					return nil, e
				}
				sent = true
				break
			}
		}
		if msg["id"] == float64(3) {
			r := object(msg["result"])
			if msg["error"] != nil || r["wasThrown"] == true || r["exceptionDetails"] != nil {
				return nil, errors.New("player operation failed")
			}
			return json.Marshal(object(r["result"])["value"])
		}
	}
	return nil, errors.New("player context unavailable")
}
