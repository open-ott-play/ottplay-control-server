package television

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type tvRequest struct {
	ID      string         `json:"id"`
	Type    string         `json:"type"`
	URI     string         `json:"uri"`
	Payload map[string]any `json:"payload"`
}
type tvFixture struct {
	server       *httptest.Server
	config       config.Television
	mu           sync.Mutex
	requests     []tvRequest
	volume       int
	muted        bool
	power        string
	app          string
	hook         func(*websocket.Conn, tvRequest) bool
	registerHook func(*websocket.Conn, tvRequest) bool
}

func sendJSON(conn *websocket.Conn, value any) error {
	b, _ := json.Marshal(value)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	return conn.Write(ctx, websocket.MessageText, b)
}
func readJSON(conn *websocket.Conn, value any) error {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_, b, err := conn.Read(ctx)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, value)
}
func respond(conn *websocket.Conn, request tvRequest, payload any) {
	_ = sendJSON(conn, map[string]any{"id": request.ID, "type": "response", "payload": payload})
}

func newTV(t *testing.T, secure bool) *tvFixture {
	t.Helper()
	f := &tvFixture{volume: 11, power: "Active", app: "de.benzac.msx"}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer conn.CloseNow()
		var req tvRequest
		if readJSON(conn, &req) != nil {
			return
		}
		if req.Type != "hello" {
			t.Errorf("first request is %q", req.Type)
			return
		}
		if sendJSON(conn, map[string]any{"id": req.ID, "type": "hello", "payload": map[string]any{}}) != nil {
			return
		}
		if readJSON(conn, &req) != nil {
			return
		}
		if req.Type != "register" {
			t.Errorf("second request is %q", req.Type)
			return
		}
		f.mu.Lock()
		f.requests = append(f.requests, req)
		f.mu.Unlock()
		if f.registerHook != nil && f.registerHook(conn, req) {
			return
		}
		if sendJSON(conn, map[string]any{"id": req.ID, "type": "registered", "payload": map[string]any{"client-key": "fixture-paired-key"}}) != nil {
			return
		}
		for {
			if readJSON(conn, &req) != nil {
				return
			}
			f.mu.Lock()
			f.requests = append(f.requests, req)
			f.mu.Unlock()
			if f.hook != nil && f.hook(conn, req) {
				continue
			}
			p := map[string]any{"returnValue": true}
			endpoint := strings.TrimPrefix(req.URI, "ssap://")
			f.mu.Lock()
			switch endpoint {
			case getPower:
				p["state"] = f.power
			case getApp:
				p["appId"] = f.app
			case getVolume:
				p["volumeStatus"] = map[string]any{"volume": f.volume, "muteStatus": f.muted}
			case getMute:
				p["mute"] = f.muted
			case setVolume:
				f.volume = int(req.Payload["volume"].(float64))
			case setMute:
				f.muted = req.Payload["mute"].(bool)
			case turnOff:
				f.power = "Power Off"
			case launchApp:
				f.app = req.Payload["id"].(string)
			case openURL:
				f.app = "com.webos.app.browser"
			case getApps:
				p["launchPoints"] = []any{}
			default:
				t.Errorf("unexpected endpoint %q", endpoint)
			}
			f.mu.Unlock()
			respond(conn, req, p)
		}
	})
	if secure {
		f.server = httptest.NewTLSServer(handler)
	} else {
		f.server = httptest.NewServer(handler)
	}
	t.Cleanup(f.server.Close)
	u, _ := url.Parse(f.server.URL)
	host, port, _ := net.SplitHostPort(u.Host)
	number, _ := strconv.Atoi(port)
	f.config = config.Television{DeviceID: "living", Host: host, Port: number, TLS: &secure, ClientKeyFile: filepath.Join(t.TempDir(), "key.json"), AppID: "de.benzac.msx"}
	f.config.Defaults()
	key := credentials{ClientKey: "fixture-paired-key"}
	if secure {
		sum := sha256.Sum256(f.server.Certificate().Raw)
		key.CertificateSHA256 = hex.EncodeToString(sum[:])
	}
	if err := writeCredentials(f.config.ClientKeyFile, key); err != nil {
		t.Fatal(err)
	}
	return f
}
func (f *tvFixture) manager(t *testing.T) *Manager {
	t.Helper()
	m, err := New([]config.Television{f.config})
	if err != nil {
		t.Fatal(err)
	}
	return m
}
func (f *tvFixture) count(endpoint string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, r := range f.requests {
		if r.URI == "ssap://"+endpoint {
			n++
		}
	}
	return n
}
func intPointer(v int) *int    { return &v }
func boolPointer(v bool) *bool { return &v }

func writePrivateFixture(t *testing.T, path string, data []byte) {
	t.Helper()
	f, err := createPrivateTemp(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(data); err != nil {
		_ = f.Close()
		t.Fatal(err)
	}
	if err = f.Close(); err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(f.Name(), path); err != nil {
		t.Fatal(err)
	}
}
func assertCode(t *testing.T, err error, code string, uncertain bool) {
	t.Helper()
	var e *Error
	if !errors.As(err, &e) || e.Code != code || e.Uncertain != uncertain {
		t.Fatalf("error=%v, want code=%s uncertain=%t", err, code, uncertain)
	}
}

func TestNativeStatusVolumeMuteAndLaunch(t *testing.T) {
	f := newTV(t, true)
	m := f.manager(t)
	ctx := context.Background()
	r, err := m.Execute(ctx, "living", Command{Action: "status"})
	if err != nil || r.Outcome != "confirmed" || r.Transport != "webos" || r.State.Power != "on" || *r.State.Volume != 11 || *r.State.Muted || r.State.AppID != "de.benzac.msx" {
		t.Fatalf("status=%+v error=%v", r, err)
	}
	for _, test := range []struct {
		cmd    Command
		volume int
	}{
		{Command{Action: "volume", Delta: intPointer(100)}, 100},
		{Command{Action: "volume", Delta: intPointer(-100)}, 0},
		{Command{Action: "volume", Volume: intPointer(11)}, 11},
	} {
		r, err = m.Execute(ctx, "living", test.cmd)
		if err != nil || r.Outcome != "confirmed" || *r.State.Volume != test.volume {
			t.Fatalf("volume=%+v error=%v", r, err)
		}
	}
	r, err = m.Execute(ctx, "living", Command{Action: "mute", Muted: boolPointer(true)})
	if err != nil || r.Outcome != "confirmed" || r.State.Muted == nil || !*r.State.Muted {
		t.Fatalf("mute=%+v error=%v", r, err)
	}
	r, err = m.Execute(ctx, "living", Command{Action: "go"})
	if err != nil || r.Outcome != "confirmed" || r.State.AppID != f.config.AppID || f.count(launchApp) != 1 {
		t.Fatalf("go=%+v error=%v", r, err)
	}
}

func TestNativeAppsEmptyArrayAndBindingSanitized(t *testing.T) {
	f := newTV(t, false)
	m := f.manager(t)
	r, err := m.Execute(context.Background(), "living", Command{Action: "apps"})
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(r)
	if !strings.Contains(string(b), `"apps":[]`) {
		t.Fatalf("empty apps omitted: %s", b)
	}
	b, _ = json.Marshal(m.Binding("living"))
	for _, secret := range []string{f.config.Host, f.config.ClientKeyFile, "fixture-paired-key"} {
		if strings.Contains(string(b), secret) {
			t.Fatalf("binding leaks configuration: %s", b)
		}
	}
	if m.Binding("absent").Configured {
		t.Fatal("absent binding marked configured")
	}
}

func TestExplicitPairPersistsCertificateAndKey(t *testing.T) {
	f := newTV(t, true)
	if err := os.Remove(f.config.ClientKeyFile); err != nil {
		t.Fatal(err)
	}
	m := f.manager(t)
	_, err := m.Execute(context.Background(), "living", Command{Action: "status"})
	assertCode(t, err, "credential_unavailable", false)
	r, err := m.Execute(context.Background(), "living", Command{Action: "pair"})
	if err != nil || r.Pairing == nil || !r.Pairing.Paired || r.Outcome != "confirmed" {
		t.Fatalf("pair=%+v error=%v", r, err)
	}
	key, err := readCredentials(f.config.ClientKeyFile)
	if err != nil || key.CertificateSHA256 == "" {
		t.Fatalf("pair not persisted: %v", err)
	}
	info, _ := os.Stat(f.config.ClientKeyFile)
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0600 {
		t.Fatal("key not private")
	}
	_, err = m.Execute(context.Background(), "living", Command{Action: "status"})
	if err != nil {
		t.Fatal(err)
	}
}

func TestCertificateMismatchNeverUsesConnection(t *testing.T) {
	f := newTV(t, true)
	f.config.CertificateSHA256 = strings.Repeat("0", 64)
	// Use a plaintext imported key, so the mismatch is checked against the TV
	// rather than rejected earlier as conflicting administrator configuration.
	writePrivateFixture(t, f.config.ClientKeyFile, []byte("fixture-paired-key"))
	_, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "status"})
	assertCode(t, err, "certificate_mismatch", false)
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.requests) != 0 {
		t.Fatal("registered despite mismatched pin")
	}
}

func TestNoFallbackAfterNativeMutationAndPowerOffWithoutAck(t *testing.T) {
	for _, action := range []string{"volume", "off"} {
		t.Run(action, func(t *testing.T) {
			f := newTV(t, false)
			var fallbackCalls atomic.Int32
			ha := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				fallbackCalls.Add(1)
				http.Error(w, "must not call", 500)
			}))
			defer ha.Close()
			token := filepath.Join(t.TempDir(), "ha-token")
			writePrivateFixture(t, token, []byte("fixture-token"))
			f.config.Fallback = &config.TelevisionFallback{URL: ha.URL, TokenFile: token, EntityID: "media_player.native_tv"}
			f.hook = func(conn *websocket.Conn, req tvRequest) bool {
				if req.URI == "ssap://"+setVolume || req.URI == "ssap://"+turnOff {
					_ = conn.CloseNow()
					return true
				}
				return false
			}
			cmd := Command{Action: action}
			if action == "volume" {
				cmd.Delta = intPointer(5)
			}
			r, err := f.manager(t).Execute(context.Background(), "living", cmd)
			if action == "volume" {
				assertCode(t, err, "connection_failed", true)
				if r.Outcome != "uncertain" {
					t.Fatal(r.Outcome)
				}
			} else if err != nil || r.Outcome != "accepted" {
				t.Fatalf("off=%+v error=%v", r, err)
			}
			if fallbackCalls.Load() != 0 {
				t.Fatal("unsafe fallback after mutation")
			}
		})
	}
}

func TestAlreadyOffDoesNotToggleAndBrowserLaunchIsOnlyAccepted(t *testing.T) {
	f := newTV(t, false)
	f.power = "Power Off"
	r, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "off"})
	if err != nil || r.Outcome != "confirmed" || r.State.Power != "off" || f.count(turnOff) != 0 {
		t.Fatalf("off=%+v error=%v", r, err)
	}
	f.config.AppID = ""
	f.config.LaunchURL = "https://player.ottplay.here.now/"
	r, err = f.manager(t).Execute(context.Background(), "living", Command{Action: "launch"})
	if err != nil || r.Outcome != "accepted" || r.State.AppID != "com.webos.app.browser" {
		t.Fatalf("launch=%+v error=%v", r, err)
	}
}

func TestCommandValidationAndPerTVDeadline(t *testing.T) {
	f := newTV(t, false)
	m := f.manager(t)
	for _, cmd := range []Command{{Action: "raw"}, {Action: "volume", Volume: intPointer(101)}, {Action: "volume", Volume: intPointer(5), Delta: intPointer(1)}, {Action: "mute"}, {Action: "on", Muted: boolPointer(true)}} {
		_, err := m.Execute(context.Background(), "living", cmd)
		assertCode(t, err, "invalid_command", false)
	}
	<-m.devices["living"].gate
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	_, err := m.Execute(ctx, "living", Command{Action: "status"})
	assertCode(t, err, "deadline", false)
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.requests) != 0 {
		t.Fatal("invalid or expired operation reached TV")
	}
}

func TestWOLPacketAndNoFalseAwakeConfirmation(t *testing.T) {
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err = sendMagicPacket("02:03:04:05:06:07", "127.0.0.1", conn.LocalAddr().(*net.UDPAddr).Port); err != nil {
		t.Fatal(err)
	}
	_ = conn.SetReadDeadline(time.Now().Add(time.Second))
	packet := make([]byte, 200)
	n, _, err := conn.ReadFromUDP(packet)
	if err != nil || n != 102 {
		t.Fatalf("packet length %d error %v", n, err)
	}
	for i := range 6 {
		if packet[i] != 255 {
			t.Fatal("missing magic prefix")
		}
	}
	for i := 6; i < 102; i++ {
		if packet[i] != byte((i-6)%6+2) {
			t.Fatal("invalid repeated MAC")
		}
	}
	f := newTV(t, false)
	f.power = "Power Off"
	f.config.MAC = "02:03:04:05:06:07"
	m := f.manager(t)
	m.wakeWait = 20 * time.Millisecond
	m.wakeInterval = time.Millisecond
	wakes := 0
	m.sendWake = func(string, string, int) error { wakes++; return nil }
	r, err := m.Execute(context.Background(), "living", Command{Action: "on"})
	if err != nil || r.Outcome != "accepted" || r.Transport != "wake_on_lan" || r.State.Power != "unknown" || wakes != 1 {
		t.Fatalf("wake=%+v error=%v sends=%d", r, err, wakes)
	}
	r, err = m.Execute(context.Background(), "living", Command{Action: "go"})
	assertCode(t, err, "not_ready", true)
	if r.Outcome != "uncertain" {
		t.Fatal("unobserved wake was not reported as a possible partial effect")
	}
	if f.count(launchApp) != 0 {
		t.Fatal("launched before wake confirmed")
	}
}

func TestPrivateCredentialsRejectPublicFileAndSymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows ACL and symlink behavior is covered by platform-specific tests")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "key")
	if err := os.WriteFile(path, []byte("private-key"), 0644); err != nil {
		t.Fatal(err)
	}
	_, err := readCredentials(path)
	assertCode(t, err, "credential_invalid", false)
	if err = os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "symlink")
	if err = os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	_, err = readCredentials(link)
	assertCode(t, err, "credential_invalid", false)
	err = writeCredentials(link, credentials{ClientKey: "replacement"})
	assertCode(t, err, "credential_invalid", false)
	b, _ := os.ReadFile(path)
	if string(b) != "private-key" {
		t.Fatal("symlink target overwritten")
	}
}

func TestOrdinaryCommandDoesNotContinueInteractivePairing(t *testing.T) {
	f := newTV(t, false)
	f.registerHook = func(conn *websocket.Conn, request tvRequest) bool {
		respond(conn, request, map[string]any{"pairingType": "PROMPT"})
		return true
	}
	_, err := f.manager(t).Execute(context.Background(), "living", Command{Action: "status"})
	assertCode(t, err, "pairing_required", false)
	if f.count(getPower) != 0 {
		t.Fatal("command was sent before registration")
	}
}

func TestUnpinnedTLSIsRejectedWithoutContactingTV(t *testing.T) {
	f := newTV(t, true)
	writePrivateFixture(t, f.config.ClientKeyFile, []byte("fixture-paired-key"))
	m := f.manager(t)
	if m.Binding("living").Paired {
		t.Fatal("unpinned TLS reported ready")
	}
	_, err := m.Execute(context.Background(), "living", Command{Action: "status"})
	assertCode(t, err, "certificate_required", false)
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.requests) != 0 {
		t.Fatal("contacted unpinned TV during ordinary command")
	}
}

func TestPairedBindingRequiresUsableConsistentTLSPin(t *testing.T) {
	for _, test := range []struct {
		name      string
		keyPin    bool
		configPin string
		paired    bool
	}{
		{name: "key pin", keyPin: true, paired: true},
		{name: "config pin", configPin: "same", paired: true},
		{name: "matching pins", keyPin: true, configPin: "same", paired: true},
		{name: "no pin", paired: false},
		{name: "conflicting pins", keyPin: true, configPin: "different", paired: false},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newTV(t, true)
			key, err := readCredentials(f.config.ClientKeyFile)
			if err != nil {
				t.Fatal(err)
			}
			if test.configPin == "same" {
				f.config.CertificateSHA256 = key.CertificateSHA256
			}
			if test.configPin == "different" {
				f.config.CertificateSHA256 = strings.Repeat("0", 64)
			}
			if !test.keyPin {
				key.CertificateSHA256 = ""
				if err := writeCredentials(f.config.ClientKeyFile, key); err != nil {
					t.Fatal(err)
				}
			}
			if got := f.manager(t).Binding("living").Paired; got != test.paired {
				t.Fatalf("paired=%t, want %t", got, test.paired)
			}
		})
	}
}

func TestLegacyPowerEndpointAndMalformedVolume(t *testing.T) {
	f := newTV(t, false)
	f.hook = func(conn *websocket.Conn, request tvRequest) bool {
		if request.URI == "ssap://"+getPower {
			_ = sendJSON(conn, map[string]any{"id": request.ID, "type": "error", "error": "404 no such service"})
			return true
		}
		if request.URI == "ssap://"+getVolume {
			respond(conn, request, map[string]any{"returnValue": true, "volume": 101})
			return true
		}
		return false
	}
	m := f.manager(t)
	r, err := m.Execute(context.Background(), "living", Command{Action: "status"})
	if err != nil || r.State.Power != "on" || r.State.Volume != nil {
		t.Fatalf("legacy state=%+v err=%v", r, err)
	}
	_, err = m.Execute(context.Background(), "living", Command{Action: "volume", Delta: intPointer(1)})
	assertCode(t, err, "unsupported", false)
	if f.count(setVolume) != 0 {
		t.Fatal("relative volume mutated without a valid baseline")
	}
}

func TestOversizedAndMismatchedResponsesAreBounded(t *testing.T) {
	for _, oversized := range []bool{true, false} {
		t.Run(strconv.FormatBool(oversized), func(t *testing.T) {
			f := newTV(t, false)
			f.hook = func(conn *websocket.Conn, request tvRequest) bool {
				if request.URI != "ssap://"+getApps {
					return false
				}
				if oversized {
					respond(conn, request, map[string]any{"returnValue": true, "junk": strings.Repeat("x", 300*1024)})
				} else {
					for range 32 {
						respond(conn, tvRequest{ID: "wrong-request"}, map[string]any{"returnValue": true})
					}
				}
				return true
			}
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			_, err := f.manager(t).Execute(ctx, "living", Command{Action: "apps"})
			if err == nil {
				t.Fatal("accepted unbounded or unrelated response")
			}
		})
	}
}

func TestNativeNullReadingsCannotConfirmOrBecomeRelativeBaseline(t *testing.T) {
	f := newTV(t, false)
	f.hook = func(conn *websocket.Conn, request tvRequest) bool {
		if request.URI == "ssap://"+getVolume {
			respond(conn, request, map[string]any{"returnValue": true, "volumeStatus": map[string]any{"volume": nil, "muteStatus": nil}})
			return true
		}
		if request.URI == "ssap://"+getMute {
			respond(conn, request, map[string]any{"returnValue": true, "mute": nil})
			return true
		}
		return false
	}
	m := f.manager(t)
	_, err := m.Execute(context.Background(), "living", Command{Action: "volume", Delta: intPointer(1)})
	assertCode(t, err, "unsupported", false)
	if f.count(setVolume) != 0 {
		t.Fatal("used null as relative volume baseline")
	}
	r, err := m.Execute(context.Background(), "living", Command{Action: "volume", Volume: intPointer(0)})
	if err != nil || r.Outcome != "accepted" || r.State != nil {
		t.Fatalf("null falsely confirmed zero volume: %+v, %v", r, err)
	}
	r, err = m.Execute(context.Background(), "living", Command{Action: "mute", Muted: boolPointer(false)})
	if err != nil || r.Outcome != "accepted" || r.State != nil {
		t.Fatalf("null falsely confirmed unmute: %+v, %v", r, err)
	}
	r, err = m.Execute(context.Background(), "living", Command{Action: "status"})
	if err != nil || r.State.Volume != nil || r.State.Muted != nil {
		t.Fatalf("null surfaced as real status: %+v, %v", r, err)
	}
}
