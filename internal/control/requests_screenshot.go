package control

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"hash/crc32"
	"image/png"
	"regexp"
)

const maxScreenshotBytes = 1024 * 1024

var screenshotRuntimePattern = regexp.MustCompile(`^[a-z0-9-]{1,64}$`)

func validScreenshotRequest(params map[string]json.RawMessage) bool {
	var runtime string
	return len(params) == 1 && json.Unmarshal(params["runtime"], &runtime) == nil && screenshotRuntimePattern.MatchString(runtime)
}

func validScreenshotResult(raw json.RawMessage, runtime string) bool {
	fields, err := decodeObject(raw)
	if err != nil || len(fields) != 10 {
		return false
	}
	// encoding/json accepts case-insensitive struct keys; the wire contract does
	// not. Require every exact key before decoding values into the typed struct.
	for _, key := range []string{"version", "runtime", "mime", "encoding", "image", "width", "height", "captured_at", "source", "video"} {
		if _, present := fields[key]; !present {
			return false
		}
	}
	var v struct {
		Version    int    `json:"version"`
		Runtime    string `json:"runtime"`
		MIME       string `json:"mime"`
		Encoding   string `json:"encoding"`
		Image      string `json:"image"`
		Width      int    `json:"width"`
		Height     int    `json:"height"`
		CapturedAt int64  `json:"captured_at"`
		Source     string `json:"source"`
		Video      string `json:"video"`
	}
	if json.Unmarshal(raw, &v) != nil || v.Version != 1 || v.Runtime != runtime || v.MIME != "image/png" || v.Encoding != "base64" || v.Width < 1 || v.Width > 1280 || v.Height < 1 || v.Height > 720 || v.CapturedAt < 1 || v.CapturedAt > 9007199254740991 || (v.Video != "unknown" && v.Video != "excluded") {
		return false
	}
	switch v.Source {
	case "player-view", "player-window", "browser-tab", "window", "display":
	default:
		return false
	}
	if len(v.Image) == 0 || len(v.Image) > base64.StdEncoding.EncodedLen(maxScreenshotBytes) {
		return false
	}
	image, err := base64.StdEncoding.Strict().DecodeString(v.Image)
	// Canonical base64 excludes ignored newlines and alternative padding.
	if err != nil || len(image) > maxScreenshotBytes || base64.StdEncoding.EncodeToString(image) != v.Image || !screenshotPNGChunks(image) {
		return false
	}
	config, err := png.DecodeConfig(bytes.NewReader(image))
	if err != nil || config.Width != v.Width || config.Height != v.Height {
		return false
	}
	// Decode only after checking dimensions: malicious PNGs cannot allocate an
	// unbounded canvas. This validates compressed pixel data as well as headers.
	_, err = png.Decode(bytes.NewReader(image))
	return err == nil
}

func screenshotPNGChunks(data []byte) bool {
	if len(data) < 8 || string(data[:8]) != "\x89PNG\r\n\x1a\n" {
		return false
	}
	for offset := 8; offset < len(data); {
		if len(data)-offset < 12 {
			return false
		}
		length := uint64(binary.BigEndian.Uint32(data[offset:]))
		if length > uint64(len(data)-offset-12) {
			return false
		}
		end := offset + 8 + int(length)
		kind := string(data[offset+4 : offset+8])
		if crc32.ChecksumIEEE(data[offset+4:end]) != binary.BigEndian.Uint32(data[end:]) {
			return false
		}
		if kind == "IEND" {
			return length == 0 && end+4 == len(data)
		}
		offset = end + 4
	}
	return false
}
