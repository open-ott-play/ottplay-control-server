package main

import (
	"context"
	"errors"
	"time"
)

// A standalone APK must not silently change the helper protocol while the
// broker keeps executing the system copy. Use a signed pair bundle for that.
func apkOnlyPreflight(ctx context.Context, apk []byte) error {
	guard, err := bundleReadFile(bundleHelperPath, bundleGuardLimit, true)
	if err != nil || validateBundlePair(bundlePayload{apk, guard}) != nil {
		return errors.New("APK helper differs from system helper; use a signed bundle")
	}
	raw, err := bundleCommand(ctx, 5*time.Second, "/system/bin/dumpsys", "package", packageName)
	if err != nil {
		return err
	}
	enabled, err := parseAPKEnabled(raw)
	if err != nil || !enabled {
		return errors.New("APK update requires an enabled OTT app")
	}
	raw, err = bundleCommand(ctx, 5*time.Second, "/system/bin/dumpsys", "activity", "activities")
	if err != nil || !bundleForeground(raw) {
		return errors.New("APK update requires OTT in foreground")
	}
	return nil
}
