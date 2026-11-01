//go:build !windows

package television

import "os"

func privateFile(_ *os.File, info os.FileInfo) bool {
	return info.Mode().Perm()&0077 == 0
}

func createPrivateTemp(dir string) (*os.File, error) {
	// CreateTemp creates its file with mode 0600 before any secret is written.
	return os.CreateTemp(dir, ".television-key-*")
}
