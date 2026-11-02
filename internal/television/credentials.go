package television

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
)

type credentials struct {
	ClientKey         string `json:"client_key"`
	CertificateSHA256 string `json:"certificate_sha256,omitempty"`
}

func readPrivateFile(path string) ([]byte, error) {
	before, err := os.Lstat(path)
	if err != nil {
		return nil, failure("credential_unavailable")
	}
	if !before.Mode().IsRegular() || before.Size() > 8192 {
		return nil, failure("credential_invalid")
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, failure("credential_unavailable")
	}
	defer f.Close()
	after, err := f.Stat()
	if err != nil || !os.SameFile(before, after) || !privateFile(f, after) {
		return nil, failure("credential_invalid")
	}
	b, err := io.ReadAll(io.LimitReader(f, 8193))
	if err != nil || len(b) > 8192 {
		return nil, failure("credential_invalid")
	}
	return b, nil
}

func validKey(key string) bool {
	if len(key) == 0 || len(key) > 512 {
		return false
	}
	for _, ch := range key {
		if ch < 33 || ch > 126 {
			return false
		}
	}
	return true
}

func validPin(pin string) bool {
	b, err := hex.DecodeString(pin)
	return err == nil && len(b) == 32 && pin == hex.EncodeToString(b)
}

func readCredentials(path string) (credentials, error) {
	var c credentials
	b, err := readPrivateFile(path)
	if err != nil {
		return c, err
	}
	b = bytes.TrimSpace(b)
	if len(b) > 0 && b[0] == '{' {
		d := json.NewDecoder(bytes.NewReader(b))
		d.DisallowUnknownFields()
		if d.Decode(&c) != nil || d.Decode(new(any)) != io.EOF {
			return c, failure("credential_invalid")
		}
	} else {
		c.ClientKey = string(b)
	}
	if !validKey(c.ClientKey) || (c.CertificateSHA256 != "" && !validPin(c.CertificateSHA256)) {
		return credentials{}, failure("credential_invalid")
	}
	return c, nil
}

func writeCredentials(path string, c credentials) error {
	if !validKey(c.ClientKey) || (c.CertificateSHA256 != "" && !validPin(c.CertificateSHA256)) {
		return failure("credential_invalid")
	}
	if _, err := os.Lstat(path); err == nil {
		if _, err := readPrivateFile(path); err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return failure("credential_unavailable")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return failure("credential_unavailable")
	}
	f, err := createPrivateTemp(filepath.Dir(path))
	if err != nil {
		return failure("credential_unavailable")
	}
	defer os.Remove(f.Name())
	b, _ := json.Marshal(c)
	b = append(b, '\n')
	_, err = f.Write(b)
	if err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	if err != nil || os.Rename(f.Name(), path) != nil {
		return failure("credential_unavailable")
	}
	return nil
}

func readToken(path string) (string, error) {
	b, err := readPrivateFile(path)
	if err != nil {
		return "", err
	}
	token := strings.TrimSpace(string(b))
	if len(token) == 0 || strings.ContainsAny(token, "\r\n\t ") {
		return "", failure("credential_invalid")
	}
	return token, nil
}
