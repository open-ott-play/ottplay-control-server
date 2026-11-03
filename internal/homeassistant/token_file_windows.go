package homeassistant

import (
	"os"
	"strings"

	"golang.org/x/sys/windows"
)

func openTokenFile(path string) (*os.File, error) {
	name, err := tokenFileWindowsPath(path)
	if err != nil {
		return nil, err
	}
	namep, err := windows.UTF16PtrFromString(name)
	if err != nil {
		return nil, err
	}
	// A credential writer must be able to replace the file while a poll reads
	// the old handle. This permits rename/delete, not additional read or write
	// access; the file's ACL still applies. Nil security attributes also keep
	// the read-only handle non-inheritable, as with os.Open.
	h, err := windows.CreateFile(namep, windows.GENERIC_READ,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE,
		nil, windows.OPEN_EXISTING, windows.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(h), path), nil
}

func tokenFileWindowsPath(path string) (string, error) {
	// Keep explicit extended/device paths intact. For ordinary long paths,
	// retain os.Open's extended-length support even when Windows long paths
	// are disabled. FullPath resolves relative paths before adding the prefix.
	normalized := strings.ReplaceAll(path, "/", `\`)
	if strings.HasPrefix(normalized, `\\?\`) || strings.HasPrefix(normalized, `\\.\`) || strings.HasPrefix(normalized, `\??\`) {
		return path, nil
	}
	full, err := windows.FullPath(path)
	if err != nil {
		return "", err
	}
	if len(full) < 248 {
		return path, nil
	}
	if strings.HasPrefix(full, `\\`) {
		return `\\?\UNC\` + full[2:], nil
	}
	return `\\?\` + full, nil
}
