package control

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"hash/crc32"
	"image"
	"image/color"
	"image/png"
	"strings"
	"testing"
	"time"
)

func screenshotFixture(t *testing.T) map[string]any {
	t.Helper()
	picture := image.NewNRGBA(image.Rect(0, 0, 2, 1))
	picture.Set(0, 0, color.NRGBA{R: 255, A: 255})
	picture.Set(1, 0, color.NRGBA{B: 255, A: 255})
	var buffer bytes.Buffer
	if err := png.Encode(&buffer, picture); err != nil {
		t.Fatal(err)
	}
	return map[string]any{"version": 1, "runtime": "page-123", "mime": "image/png", "encoding": "base64", "image": base64.StdEncoding.EncodeToString(buffer.Bytes()), "width": 2, "height": 1, "captured_at": int64(1791264000000), "source": "player-view", "video": "unknown"}
}

func screenshotEnvelope(id, status string, data any) string {
	body, _ := json.Marshal(map[string]any{"id": id, "status": status, "data": data})
	return string(body)
}

func TestScreenshotRequestValidation(t *testing.T) {
	for _, params := range []string{`{}`, `null`, `[]`, `{"runtime":null}`, `{"runtime":true}`, `{"runtime":1}`, `{"runtime":""}`, `{"runtime":"PAGE"}`, `{"runtime":"page_123"}`, `{"runtime":"page/123"}`, `{"runtime":"page\n123"}`, `{"runtime":"` + strings.Repeat("x", 65) + `"}`, `{"runtime":"page-123","runtime":"page-456"}`, `{"runtime":"page-123","output":"/tmp/screen.png"}`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"screenshot","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("invalid screenshot request queued")
		}
	}
	for _, action := range []string{"shot", "SCREENSHOT", "capture"} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"`+action+`","params":{"runtime":"page-123"}}`, nil), 400)
	}
}

func TestScreenshotAuthenticatedBoundedRoundTrip(t *testing.T) {
	s := newTestServer(t)
	payload := `{"action":"screenshot","params":{"runtime":"page-123"}}`
	for _, credentials := range []struct {
		token  string
		status int
	}{{"", 401}, {firstToken, 403}} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", credentials.token, payload, nil), credentials.status)
	}
	id := rpcID(t, s, payload)
	poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
	expect(t, poll, 200)
	if !strings.Contains(poll.Body.String(), `"runtime":"page-123"`) || s.devices[0].queue[0].screenshotRuntime != "page-123" {
		t.Fatal("runtime binding was lost")
	}
	body := screenshotEnvelope(id, "ok", screenshotFixture(t))
	expect(t, request(s, "POST", "/api/responses", adminToken, body, nil), 403)
	expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, map[string]string{"Origin": "https://evil.example"}), 403)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, firstToken, "", nil), 403)
	expect(t, request(s, "GET", "/api/requests?device_id=second&id="+id, adminToken, "", nil), 404)
	result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, result, 200)
	if result.Body.String() != body || s.resultBytes != len(body) || s.bytes != 0 || len(s.devices[0].queue) != 0 {
		t.Fatal("screenshot receipt changed, was duplicated or stayed queued")
	}
	s.now = func() time.Time { return time.Now().Add(61 * time.Second) }
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil), 404)
	if s.resultBytes != 0 {
		t.Fatal("image was retained after result TTL")
	}
}

func TestScreenshotRejectsUntrustedReceiptsWithoutRemovingRequest(t *testing.T) {
	mutations := []func(map[string]any){
		func(v map[string]any) { v["runtime"] = "page-other" },
		func(v map[string]any) { v["version"] = true },
		func(v map[string]any) { v["version"] = 2 },
		func(v map[string]any) { v["Version"] = v["version"]; delete(v, "version") },
		func(v map[string]any) { v["mime"] = "image/jpeg" },
		func(v map[string]any) { v["encoding"] = "url" },
		func(v map[string]any) { v["image"] = "https://private.invalid/secret" },
		func(v map[string]any) { v["image"] = v["image"].(string) + "\n" },
		func(v map[string]any) {
			v["image"] = base64.StdEncoding.EncodeToString(make([]byte, maxScreenshotBytes+1))
		},
		func(v map[string]any) { v["width"] = 3 },
		func(v map[string]any) { v["width"] = 1281 },
		func(v map[string]any) { v["height"] = 721 },
		func(v map[string]any) { v["height"] = nil },
		func(v map[string]any) { v["captured_at"] = "1791264000000" },
		func(v map[string]any) { v["captured_at"] = 9007199254740992 },
		func(v map[string]any) { v["source"] = "other-app" },
		func(v map[string]any) { v["video"] = "included" },
		func(v map[string]any) { v["url"] = "https://private.invalid" },
		func(v map[string]any) { delete(v, "video") },
	}
	for _, mutate := range mutations {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"screenshot","params":{"runtime":"page-123"}}`)
		v := screenshotFixture(t)
		mutate(v)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", v), nil), 400)
		if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || s.resultBytes != 0 {
			t.Fatal("invalid receipt consumed request or image storage")
		}
	}
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"screenshot","params":{"runtime":"page-123"}}`)
	v := screenshotFixture(t)
	v["image"] = strings.Repeat("A", maxResponseBytes)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", v), nil), 413)
}

func TestScreenshotPNGIntegrityAndDimensions(t *testing.T) {
	v := screenshotFixture(t)
	original, _ := base64.StdEncoding.DecodeString(v["image"].(string))
	for _, mutation := range []func([]byte) []byte{
		func(b []byte) []byte { return append(b, 0) },
		func(b []byte) []byte { return b[:len(b)-1] },
		func(b []byte) []byte { b[40] ^= 1; return b },
		func(b []byte) []byte {
			binary.BigEndian.PutUint32(b[16:20], 1000000000)
			binary.BigEndian.PutUint32(b[29:33], crc32.ChecksumIEEE(b[12:29]))
			return b
		},
		func(b []byte) []byte {
			// Corrupt the zlib stream but repair its outer PNG checksum.
			for off := 8; off < len(b); {
				n := int(binary.BigEndian.Uint32(b[off:]))
				if string(b[off+4:off+8]) == "IDAT" {
					b[off+8] = 0
					binary.BigEndian.PutUint32(b[off+8+n:], crc32.ChecksumIEEE(b[off+4:off+8+n]))
					break
				}
				off += n + 12
			}
			return b
		},
	} {
		v["image"] = base64.StdEncoding.EncodeToString(mutation(bytes.Clone(original)))
		raw, _ := json.Marshal(v)
		if validScreenshotResult(raw, "page-123") {
			t.Fatal("invalid PNG accepted")
		}
	}
	for _, source := range []string{"player-view", "player-window", "browser-tab", "window", "display"} {
		v := screenshotFixture(t)
		v["source"] = source
		raw, _ := json.Marshal(v)
		if !validScreenshotResult(raw, "page-123") {
			t.Fatalf("valid %s screenshot rejected", source)
		}
	}
}

func TestScreenshotNegativeResultsAndGenericResultsRemainCompatible(t *testing.T) {
	for _, status := range []string{"unsupported", "rejected"} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"screenshot","params":{"runtime":"page-123"}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, map[string]string{"error": "permission_required"}), nil), 200)
	}
	// Screenshot-shaped values on other RPCs must not acquire new validation.
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"status","params":{}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", map[string]string{"image": "arbitrary"}), nil), 200)
}

func TestScreenshotRechecksExpiryAfterImageValidation(t *testing.T) {
	s := newTestServer(t)
	started := time.Now()
	s.now = func() time.Time { return started }
	id := rpcID(t, s, `{"action":"screenshot","params":{"runtime":"page-123"}}`)
	calls := 0
	s.now = func() time.Time {
		calls++
		if calls >= 3 {
			return started.Add(2 * time.Minute)
		}
		return started
	}
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", screenshotFixture(t)), nil), 404)
	if len(s.devices[0].results) != 0 || s.resultBytes != 0 {
		t.Fatal("image validation revived an expired request")
	}
}
