package homeassistant

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"image"
	"image/color"
	"image/jpeg"
	"io"
	"mime"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const CameraJPEGMaxBytes = 160 * 1024
const cameraSourceMaxBytes = 2 * 1024 * 1024
const cameraSourceMaxPixels = 16_000_000
const cameraCaptureTimeout = 15 * time.Second

// CameraFrame is sanitized, ephemeral JPEG data. It must never enter a journal.
type CameraFrame struct {
	JPEG          []byte
	Width, Height int
}

var errCamera = errors.New("camera_unavailable")

// AcquireCameraFrame reads one frame from an explicitly configured HA camera.
// The stream route matches the existing Cisco/mobile-notification integration:
// close immediately after its first complete JPEG, never subscribe to live video.
func AcquireCameraFrame(ctx context.Context, c config.HomeAssistant, key string) (*CameraFrame, error) {
	camera, ok := c.Cameras[key]
	if !ok || c.Validate() != nil {
		return nil, errCamera
	}
	token, err := readToken(c.TokenFile)
	if err != nil {
		return nil, errCamera
	}
	u, err := url.Parse(c.URL)
	if err != nil {
		return nil, errCamera
	}
	u.Path = strings.TrimSuffix(strings.TrimRight(u.Path, "/"), "/api/websocket") + "/api/camera_proxy_stream/" + camera.EntityID
	ctx, cancel := context.WithTimeout(ctx, cameraCaptureTimeout)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, errCamera
	}
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Accept", "multipart/x-mixed-replace,image/jpeg")
	// No environment proxy, cookie jar, redirect, or insecure TLS fallback.
	transport := &http.Transport{TLSHandshakeTimeout: cameraCaptureTimeout, ResponseHeaderTimeout: cameraCaptureTimeout, DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(request)
	if err != nil {
		return nil, errCamera
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, errCamera
	}
	mediaType, params, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil {
		return nil, errCamera
	}
	var reader io.Reader = io.LimitReader(response.Body, cameraSourceMaxBytes+1)
	if mediaType == "multipart/x-mixed-replace" {
		if params["boundary"] == "" || len(params["boundary"]) > 200 {
			return nil, errCamera
		}
		// HA's still-stream implementation includes leading dashes in its
		// boundary parameter. Like the existing Cisco consumer, locate SOI
		// within a bounded preamble rather than depending on MIME framing.
		reader, err = cameraJPEGStart(reader)
		if err != nil {
			return nil, errCamera
		}
	} else if mediaType != "image/jpeg" {
		return nil, errCamera
	}
	raw, err := firstCameraJPEG(reader)
	if err != nil || ctx.Err() != nil {
		return nil, errCamera
	}
	frame, err := sanitizeCameraJPEG(raw)
	if err != nil || ctx.Err() != nil {
		return nil, errCamera
	}
	return frame, nil
}

func cameraJPEGStart(source io.Reader) (io.Reader, error) {
	r := bufio.NewReaderSize(source, 4096)
	var previous byte
	for range 32 * 1024 {
		b, err := r.ReadByte()
		if err != nil {
			return nil, errCamera
		}
		if previous == 0xff && b == 0xd8 {
			return io.MultiReader(bytes.NewReader([]byte{0xff, 0xd8}), r), nil
		}
		previous = b
	}
	return nil, errCamera
}

// Parse JPEG markers rather than searching for FF D9 inside EXIF/other segments.
// Do not wait for an HTTP/MJPEG stream to finish after the first image.
func firstCameraJPEG(source io.Reader) ([]byte, error) {
	r := bufio.NewReaderSize(io.LimitReader(source, cameraSourceMaxBytes+1), 4096)
	var out bytes.Buffer
	read := func() (byte, error) {
		b, e := r.ReadByte()
		if e == nil {
			out.WriteByte(b)
			if out.Len() > cameraSourceMaxBytes {
				return 0, errCamera
			}
		}
		return b, e
	}
	a, e := read()
	if e != nil || a != 0xff {
		return nil, errCamera
	}
	a, e = read()
	if e != nil || a != 0xd8 {
		return nil, errCamera
	}
	scan, sawScan := false, false
	for {
		marker, e := read()
		if e != nil {
			return nil, errCamera
		}
		if scan && marker != 0xff {
			continue
		}
		if marker != 0xff {
			return nil, errCamera
		}
		for {
			marker, e = read()
			if e != nil {
				return nil, errCamera
			}
			if marker != 0xff {
				break
			}
		}
		if scan && (marker == 0 || marker >= 0xd0 && marker <= 0xd7) {
			continue
		}
		scan = false
		if marker == 0xd9 {
			if !sawScan {
				return nil, errCamera
			}
			return out.Bytes(), nil
		}
		if marker == 0 || marker == 0xd8 || marker >= 0xd0 && marker <= 0xd7 {
			return nil, errCamera
		}
		if marker == 1 {
			continue
		}
		hi, e := read()
		if e != nil {
			return nil, errCamera
		}
		lo, e := read()
		if e != nil {
			return nil, errCamera
		}
		length := int(hi)*256 + int(lo) - 2
		if length < 0 {
			return nil, errCamera
		}
		for i := 0; i < length; i++ {
			if _, e = read(); e != nil {
				return nil, errCamera
			}
		}
		if marker == 0xda {
			scan = true
			sawScan = true
		}
	}
}

func sanitizeCameraJPEG(raw []byte) (*CameraFrame, error) {
	if len(raw) == 0 || len(raw) > cameraSourceMaxBytes {
		return nil, errCamera
	}
	size, err := jpeg.DecodeConfig(bytes.NewReader(raw))
	if err != nil || size.Width < 1 || size.Height < 1 || size.Width > 16384 || size.Height > 16384 || int64(size.Width)*int64(size.Height) > cameraSourceMaxPixels {
		return nil, errCamera
	}
	decoded, err := jpeg.Decode(bytes.NewReader(raw))
	if err != nil {
		return nil, errCamera
	}
	w, h := size.Width, size.Height
	if w > 640 {
		h = max(1, h*640/w)
		w = 640
	}
	if h > 360 {
		w = max(1, w*360/h)
		h = 360
	}
	// Bounded box sampling avoids a scaler dependency and strips all metadata.
	resized := image.NewRGBA(image.Rect(0, 0, w, h))
	bounds := decoded.Bounds()
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			x0, x1 := x*size.Width/w, (x+1)*size.Width/w
			y0, y1 := y*size.Height/h, (y+1)*size.Height/h
			var rr, gg, bb, n uint64
			for sy := y0; sy < y1; sy++ {
				for sx := x0; sx < x1; sx++ {
					r, g, b, _ := decoded.At(bounds.Min.X+sx, bounds.Min.Y+sy).RGBA()
					rr += uint64(r)
					gg += uint64(g)
					bb += uint64(b)
					n++
				}
			}
			resized.SetRGBA(x, y, color.RGBA{uint8(rr / n >> 8), uint8(gg / n >> 8), uint8(bb / n >> 8), 255})
		}
	}
	var out bytes.Buffer
	if jpeg.Encode(&out, resized, &jpeg.Options{Quality: 70}) != nil || out.Len() > CameraJPEGMaxBytes {
		return nil, errCamera
	}
	return &CameraFrame{JPEG: out.Bytes(), Width: w, Height: h}, nil
}
