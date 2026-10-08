package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
)

type Update struct {
	Schema  int    `json:"schema"`
	Kind    string `json:"kind"`
	Version string `json:"version"`
	Package string `json:"package"`
	URL     string `json:"url"`
	SHA256  string `json:"sha256"`
	Size    int64  `json:"size"`
}

func httpsURL(raw string) bool {
	u, e := url.Parse(raw)
	return e == nil && len(raw) <= 2048 && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && u.Fragment == ""
}
func digestOK(raw []byte, want string) bool {
	sum := sha256.Sum256(raw)
	return regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(want) && hex.EncodeToString(sum[:]) == want
}
func verifyUpdate(raw []byte, want, key string) (Update, error) {
	var u Update
	fields, e := params(raw)
	if e != nil || len(fields) != 2 {
		return u, errors.New("invalid envelope")
	}
	payload, e := base64.StdEncoding.Strict().DecodeString(stringParam(fields, "payload"))
	if e != nil {
		return u, e
	}
	sig, e := base64.StdEncoding.Strict().DecodeString(stringParam(fields, "signature"))
	if e != nil {
		return u, e
	}
	pub, e := base64.StdEncoding.Strict().DecodeString(key)
	if e != nil || len(pub) != 32 || !digestOK(raw, want) || !ed25519.Verify(pub, payload, sig) {
		return u, errors.New("untrusted update")
	}
	fields, e = params(payload)
	if e != nil || len(fields) != 7 {
		return u, errors.New("invalid manifest")
	}
	d := json.NewDecoder(bytes.NewReader(payload))
	d.DisallowUnknownFields()
	if e = d.Decode(&u); e != nil {
		return u, e
	}
	if u.Schema != 1 || (u.Kind != "agent" && u.Kind != "apk") || !regexp.MustCompile(`^[A-Za-z0-9_.-]{1,64}$`).MatchString(u.Version) || u.Package != packageName || !httpsURL(u.URL) || !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(u.SHA256) || u.Size < 1 || u.Size > 64*1024*1024 {
		return u, errors.New("unsupported manifest")
	}
	return u, nil
}
func (a *Agent) download(ctx context.Context, address string, limit int64) ([]byte, error) {
	if !httpsURL(address) {
		return nil, errors.New("HTTPS required")
	}
	req, e := http.NewRequestWithContext(ctx, "GET", address, nil)
	if e != nil {
		return nil, e
	}
	// No device credential ever leaves the controller API, including on errors
	// and redirects. newHTTPClient refuses every redirect.
	req.Header.Set("User-Agent", "ottplay-android-agent/"+version)
	client := *a.client
	client.Timeout = 30 * time.Second
	resp, e := client.Do(req)
	if e != nil {
		return nil, errors.New("download unavailable")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, errors.New("download rejected")
	}
	b, e := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if e != nil {
		return nil, e
	}
	if int64(len(b)) > limit {
		return nil, errors.New("download too large")
	}
	return b, nil
}
func (a *Agent) prepareUpdate(ctx context.Context, id, address, digest string) (func() error, error) {
	stages, _ := filepath.Glob(dataDir + "/update-*.apk")
	if len(stages) > 0 {
		return nil, errors.New("another update is pending")
	}
	raw, e := a.download(ctx, address, 16384)
	if e != nil {
		return nil, e
	}
	u, e := verifyUpdate(raw, digest, a.cfg.UpdatePublicKey)
	if e != nil {
		return nil, e
	}
	payload, e := a.download(ctx, u.URL, u.Size)
	if e != nil {
		return nil, e
	}
	if int64(len(payload)) != u.Size || !digestOK(payload, u.SHA256) {
		return nil, errors.New("invalid payload digest")
	}
	if u.Kind == "agent" {
		if len(payload) < 20 || string(payload[:4]) != "\x7fELF" || payload[4] != 1 || payload[5] != 1 || payload[18] != 40 || payload[19] != 0 {
			return nil, errors.New("expected ARM ELF")
		}
	}
	if u.Kind == "apk" && !bytes.HasPrefix(payload, []byte("PK\x03\x04")) {
		return nil, errors.New("expected APK archive")
	}
	// A unique, root-private stage prevents a subsequent request from replacing
	// bytes already approved by this request while its ACK is retried.
	stage := dataDir + "/update-" + id + ".apk"
	if e = atomicFile(stage, payload, 0600); e != nil {
		return nil, e
	}
	return func() error {
		defer os.Remove(stage)
		if u.Kind == "apk" {
			child, cancel := context.WithTimeout(context.Background(), 60*time.Second)
			defer cancel()
			out, e := runCommand(child, "/system/bin/pm", "install", "-r", stage)
			if e != nil || !strings.Contains(string(out), "Success") {
				return errors.New("package installation rejected")
			}
			return a.lifecycle("restart_app")
		}
		old, e := os.ReadFile(binaryPath)
		if e != nil {
			return e
		}
		if e = atomicFile(binaryPath+".previous", old, 0700); e != nil {
			return e
		}
		if e = os.Chmod(stage, 0700); e != nil {
			return e
		}
		if e = atomicFile(dataDir+"/update.pending", []byte(u.Version), 0600); e != nil {
			return e
		}
		if e = os.Rename(stage, binaryPath); e != nil {
			return e
		}
		// The startup supervisor restores .previous if the new process exits before
		// its first successful controller poll. Successful exec closes the lock FD.
		if e = syscall.Exec(binaryPath, []string{binaryPath}, os.Environ()); e != nil {
			_ = os.Rename(binaryPath+".previous", binaryPath)
			_ = os.Remove(dataDir + "/update.pending")
			return e
		}
		return nil
	}, nil
}
