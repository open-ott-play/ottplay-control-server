//go:build !windows

package homeassistant

import "os"

func openTokenFile(path string) (*os.File, error) {
	return os.Open(path)
}
