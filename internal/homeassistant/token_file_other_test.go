//go:build !windows

package homeassistant

import "os"

func replaceTokenFile(source, target string) error {
	return os.Rename(source, target)
}
