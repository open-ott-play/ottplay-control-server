package television

import (
	"os"
	"path/filepath"
	"testing"

	"golang.org/x/sys/windows"
)

func TestWindowsPrivateCredentialsCreatedAndReplacedWithACL(t *testing.T) {
	path := filepath.Join(t.TempDir(), "key.json")
	for _, key := range []string{"first-paired-key", "replacement-paired-key"} {
		if err := writeCredentials(path, credentials{ClientKey: key}); err != nil {
			t.Fatal(err)
		}
		stored, err := readCredentials(path)
		if err != nil || stored.ClientKey != key {
			t.Fatalf("private key round-trip failed: %v", err)
		}
	}
}

func TestWindowsCredentialsRejectBroadAndNullACL(t *testing.T) {
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		t.Fatal(err)
	}
	for _, nullACL := range []bool{false, true} {
		t.Run(map[bool]string{false: "everyone", true: "null"}[nullACL], func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "key.json")
			if err := writeCredentials(path, credentials{ClientKey: "private-key"}); err != nil {
				t.Fatal(err)
			}
			var dacl *windows.ACL
			if !nullACL {
				sd, err := windows.SecurityDescriptorFromString("D:P(A;;FA;;;" + user.User.Sid.String() + ")(A;;FR;;;WD)")
				if err != nil {
					t.Fatal(err)
				}
				dacl, _, err = sd.DACL()
				if err != nil {
					t.Fatal(err)
				}
			}
			if err := windows.SetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION, nil, nil, dacl, nil); err != nil {
				t.Fatal(err)
			}
			_, err := readCredentials(path)
			assertCode(t, err, "credential_invalid", false)
			if err := writeCredentials(path, credentials{ClientKey: "must-not-overwrite"}); err == nil {
				t.Fatal("replaced nonprivate key")
			}
		})
	}
}

func TestWindowsCredentialsRejectSymlinkWhenAvailable(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "key.json")
	if err := writeCredentials(path, credentials{ClientKey: "private-key"}); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "link.json")
	if err := os.Symlink(path, link); err != nil {
		t.Skipf("symlink privilege unavailable: %v", err)
	}
	_, err := readCredentials(link)
	assertCode(t, err, "credential_invalid", false)
}
