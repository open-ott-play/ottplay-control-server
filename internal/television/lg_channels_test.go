package television

import (
	"bytes"
	"compress/zlib"
	"context"
	"encoding/base64"
	"io"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
)

const testLGLineup = `{"genres":[{"genre":"News","channels":[{"channelNumber":"120","channelName":"ABC News Live","channelLogoUrl":"http://ignored.invalid/logo"}]}]}`

type lineupTransport func(*http.Request) (*http.Response, error)

func (f lineupTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func mockLineup(c *lgLineupCache, body string, calls *atomic.Int32) {
	c.client = &http.Client{Transport: lineupTransport(func(r *http.Request) (*http.Response, error) {
		calls.Add(1)
		if r.URL.String() != lgLineupURL || r.Header.Get("X-Device-Country") != "US" || r.Header.Get("X-Device-Language") != "en" {
			panic("unexpected request")
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}, nil
	})}
}
func TestLGLineupDecodeBoundsAndMetadataOnly(t *testing.T) {
	var compressed bytes.Buffer
	z := zlib.NewWriter(&compressed)
	_, _ = z.Write([]byte(testLGLineup))
	_ = z.Close()
	for _, b := range [][]byte{[]byte(testLGLineup), []byte(base64.StdEncoding.EncodeToString(compressed.Bytes()))} {
		rows, err := decodeLGLineup(b)
		if err != nil || len(rows) != 1 || rows[0].ID != "lgfast:US:120" || rows[0].Source != "lg_channels" {
			t.Fatalf("%+v %v", rows, err)
		}
	}
	for _, b := range []string{"", `{}`, `{"genres":[]}`, strings.Repeat("x", lgLineupLimit+1), strings.Replace(testLGLineup, `"120"`, `"https://bad.invalid"`, 1), strings.Replace(testLGLineup, `"ABC News Live"`, `"bad\nname"`, 1)} {
		if _, err := decodeLGLineup([]byte(b)); err == nil {
			t.Fatal("accepted invalid catalogue")
		}
	}
	var bomb bytes.Buffer
	w := zlib.NewWriter(&bomb)
	_, _ = w.Write(bytes.Repeat([]byte{' '}, lgLineupLimit+1))
	_ = w.Close()
	if _, err := decodeLGLineup([]byte(base64.StdEncoding.EncodeToString(bomb.Bytes()))); err == nil {
		t.Fatal("unbounded inflate")
	}
}
func TestLGLineupCacheSingleFlightAndStaleLabel(t *testing.T) {
	c := newLGLineupCache()
	var calls atomic.Int32
	mockLineup(c, testLGLineup, &calls)
	var wg sync.WaitGroup
	for range 12 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			rows, w := c.get(context.Background())
			if len(rows) != 1 || w != "" {
				t.Errorf("cache %v %q", rows, w)
			}
		}()
	}
	wg.Wait()
	if calls.Load() != 1 {
		t.Fatal("concurrent duplicate acquisitions")
	}
	c.mu.Lock()
	c.expires = time.Time{}
	c.retry = time.Time{}
	c.mu.Unlock()
	mockLineup(c, "bad", &calls)
	rows, w := c.get(context.Background())
	if len(rows) != 1 || !strings.Contains(w, "stale metadata") {
		t.Fatal("stale not labelled")
	}
	_, _ = c.get(context.Background())
	if calls.Load() != 2 {
		t.Fatal("failed refresh not bounded")
	}
}
func TestLGFASTLogicalSelectionAndProgrammeRequireObservedPhysicalChannel(t *testing.T) {
	f := newTV(t, true)
	enableChannels(t, f)
	f.config.LGChannelsCountry = "US"
	current := "120"
	f.hook = func(conn *websocket.Conn, q tvRequest) bool {
		switch q.URI {
		case "ssap://tv/getChannelList":
			respond(conn, q, map[string]any{"returnValue": true, "channelList": []any{map[string]any{"channelId": "physical", "channelName": "Antenna", "channelNumber": "2-1", "channelMode": "Terrestrial"}}})
		case "ssap://tv/openChannel":
			if q.Payload["channelNumber"] != "120" || q.Payload["channelMode"] != "Terrestrial" || q.Payload["channelId"] != nil {
				t.Error("not a physical number handoff")
			}
			respond(conn, q, map[string]any{"returnValue": true})
		case "ssap://tv/getCurrentChannel":
			respond(conn, q, map[string]any{"returnValue": true, "channelId": "physical-fast", "channelName": "Observed", "channelNumber": current, "channelMode": "Terrestrial"})
		case "ssap://tv/getChannelCurrentProgramInfo":
			respond(conn, q, map[string]any{"returnValue": true, "channelId": "physical-fast", "programName": "Actual EPG"})
		default:
			return false
		}
		return true
	}
	m := f.manager(t)
	var calls atomic.Int32
	mockLineup(m.lgLineup, testLGLineup, &calls)
	ctx := context.Background()
	r, err := m.Execute(ctx, "living", Command{Action: "channels"})
	if err != nil || len(r.Channels) != 2 || r.Channels[0].ID != "physical" || r.Channels[1].ID != "lgfast:US:120" {
		t.Fatalf("list %+v %v", r, err)
	}
	r, err = m.Execute(ctx, "living", Command{Action: "channel_select", ChannelID: "lgfast:US:120"})
	if err != nil || r.Outcome != "confirmed" || r.Channel.ID != "lgfast:US:120" || r.Channel.PhysicalID != "physical-fast" {
		t.Fatalf("select %+v %v", r, err)
	}
	r, err = m.Execute(ctx, "living", Command{Action: "channel_program", ChannelID: "lgfast:US:120"})
	if err != nil || r.Program.ChannelID != "lgfast:US:120" || r.Program.Title != "Actual EPG" {
		t.Fatalf("program %+v %v", r, err)
	}
	current = "121"
	r, err = m.Execute(ctx, "living", Command{Action: "channel_select", ChannelID: "lgfast:US:120"})
	if err != nil || r.Outcome != "accepted" || r.Channel.ID != "physical-fast" {
		t.Fatalf("false confirmation %+v %v", r, err)
	}
	_, err = m.Execute(ctx, "living", Command{Action: "channel_program", ChannelID: "lgfast:US:120"})
	assertCode(t, err, "channel_not_current", false)
	count := f.count("tv/openChannel")
	_, err = m.Execute(ctx, "living", Command{Action: "channel_select", ChannelID: "lgfast:US:999"})
	assertCode(t, err, "channel_not_found", false)
	if f.count("tv/openChannel") != count {
		t.Fatal("unknown selector tuned")
	}
}
func TestLGFASTFailureKeepsTunerAndOptInBoundary(t *testing.T) {
	f := newTV(t, true)
	enableChannels(t, f)
	f.config.LGChannelsCountry = "US"
	f.hook = func(conn *websocket.Conn, q tvRequest) bool {
		if q.URI != "ssap://tv/getChannelList" {
			return false
		}
		respond(conn, q, map[string]any{"returnValue": true, "channelList": []any{map[string]any{"channelId": "antenna"}}})
		return true
	}
	m := f.manager(t)
	var calls atomic.Int32
	mockLineup(m.lgLineup, "invalid", &calls)
	r, err := m.Execute(context.Background(), "living", Command{Action: "channels"})
	if err != nil || r.Outcome != "confirmed" || len(r.Channels) != 1 || len(r.Warnings) != 1 {
		t.Fatalf("lost tuner %+v %v", r, err)
	}
	m.devices["living"].config.LGChannelsCountry = ""
	_, err = m.Execute(context.Background(), "living", Command{Action: "channel_select", ChannelID: "lgfast:US:120"})
	assertCode(t, err, "channel_not_found", false)
}
