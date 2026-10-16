package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const updateFileLimit int64 = 64 * 1024 * 1024

// The status surface deliberately contains no filesystem paths or raw command
// output. All commands are fixed and all reads have an explicit size limit.
func boundedUpdateCommand(ctx context.Context, name string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	pipe, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err = cmd.Start(); err != nil {
		return nil, err
	}
	raw, err := io.ReadAll(io.LimitReader(pipe, 64*1024+1))
	if err != nil || len(raw) > 64*1024 {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		return nil, errors.New("command output unavailable")
	}
	if err = cmd.Wait(); err != nil {
		return nil, errors.New("command unavailable")
	}
	return raw, nil
}

func parseInstalledAPKPath(raw []byte) (string, error) {
	line := strings.TrimSpace(string(raw))
	// API 19 uses a single APK, never split APKs. Do not interpret arbitrary
	// PackageManager output as a root-readable path.
	pattern := `^package:(/data/app/play\.ott\.kitkat-[0-9]+\.apk|/system/app/play\.ott\.kitkat\.apk)$`
	match := regexp.MustCompile(pattern).FindStringSubmatch(line)
	if len(raw) > 1024 || len(match) != 2 {
		return "", errors.New("package path unavailable")
	}
	return match[1], nil
}

func installedAPKPath(ctx context.Context) (string, error) {
	raw, err := boundedUpdateCommand(ctx, "/system/bin/pm", "path", packageName)
	if err != nil {
		return "", err
	}
	return parseInstalledAPKPath(raw)
}

func parseAPKVersion(raw []byte) (string, int64, error) {
	bad := errors.New("package metadata unavailable")
	if len(raw) > 64*1024 {
		return "", 0, bad
	}
	name := ""
	code := int64(0)
	codeCount, nameCount := 0, 0
	codePattern := regexp.MustCompile(`^versionCode=([0-9]+)(?:[ \t].*)?$`)
	namePattern := regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.+\-]{0,63}$`)
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "versionCode=") {
			codeCount++
			match := codePattern.FindStringSubmatch(line)
			if len(match) != 2 {
				return "", 0, bad
			}
			var err error
			code, err = strconv.ParseInt(match[1], 10, 32)
			if err != nil || code < 1 {
				return "", 0, bad
			}
		}
		if strings.HasPrefix(line, "versionName=") {
			nameCount++
			name = strings.TrimPrefix(line, "versionName=")
			if !namePattern.MatchString(name) {
				return "", 0, bad
			}
		}
	}
	if codeCount != 1 || nameCount != 1 {
		return "", 0, bad
	}
	return name, code, nil
}

// PackageManager state is per user. The supported tablet runs the owner user;
// app process absence and the stopped flag are not evidence of being disabled.
func parseAPKEnabled(raw []byte) (bool, error) {
	bad := errors.New("package user state unavailable")
	if len(raw) > 64*1024 {
		return false, bad
	}
	found := false
	enabled := false
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "User 0:") {
			continue
		}
		if found {
			return false, bad
		}
		found = true
		values := map[string]string{}
		for _, field := range strings.Fields(strings.TrimPrefix(line, "User 0:")) {
			pair := strings.SplitN(field, "=", 2)
			if len(pair) != 2 {
				continue
			}
			if _, exists := values[pair[0]]; exists {
				return false, bad
			}
			values[pair[0]] = pair[1]
		}
		if values["installed"] != "true" && values["installed"] != "false" {
			return false, bad
		}
		switch values["enabled"] {
		case "0", "1":
			enabled = values["installed"] == "true"
		case "2", "3", "4":
			enabled = false
		default:
			return false, bad
		}
		for _, key := range []string{"hidden", "blocked"} {
			if value, exists := values[key]; exists {
				if value != "true" && value != "false" {
					return false, bad
				}
				if value == "true" {
					enabled = false
				}
			}
		}
	}
	if !found {
		return false, bad
	}
	return enabled, nil
}

func hashUpdateFile(ctx context.Context, path string, limit int64) (string, error) {
	bad := errors.New("component file unavailable")
	before, err := os.Lstat(path)
	if err != nil || !before.Mode().IsRegular() || before.Size() < 1 || before.Size() > limit {
		return "", bad
	}
	f, err := os.Open(path)
	if err != nil {
		return "", bad
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil || !os.SameFile(before, opened) {
		return "", bad
	}
	h := sha256.New()
	buffer := make([]byte, 64*1024)
	var count int64
	for {
		if ctx.Err() != nil {
			return "", bad
		}
		n, readErr := f.Read(buffer)
		if n > 0 {
			count += int64(n)
			if count > limit {
				return "", bad
			}
			_, _ = h.Write(buffer[:n])
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return "", bad
		}
	}
	after, err := os.Lstat(path)
	if err != nil || !after.Mode().IsRegular() || !os.SameFile(opened, after) || count != before.Size() || after.Size() != before.Size() || !after.ModTime().Equal(before.ModTime()) || ctx.Err() != nil {
		return "", bad
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

type updateStatusProbe struct {
	command func(context.Context, string, ...string) ([]byte, error)
	hash    func(context.Context, string, int64) (string, error)
}

func collectUpdateStatus(ctx context.Context, probe updateStatusProbe) map[string]any {
	component := func(path string, limit int64) map[string]any {
		sum, err := probe.hash(ctx, path, limit)
		if err != nil {
			return map[string]any{"state": "unavailable", "reason": "file_unavailable"}
		}
		return map[string]any{"state": "ready", "sha256": sum}
	}
	agent := component(binaryPath, updateFileLimit)
	agent["version"] = version
	guard := component("/system/bin/ott-touch-guard", 4*1024*1024)
	apk := map[string]any{"state": "unavailable", "reason": "package_metadata_unavailable", "package": packageName}
	readPackage := func() (string, string, int64, string, error) {
		raw, err := probe.command(ctx, "/system/bin/pm", "path", packageName)
		if err != nil {
			return "", "", 0, "unavailable", err
		}
		path, err := parseInstalledAPKPath(raw)
		if err != nil {
			return "", "", 0, "unavailable", err
		}
		raw, err = probe.command(ctx, "/system/bin/dumpsys", "package", packageName)
		if err != nil {
			return "", "", 0, "unavailable", err
		}
		name, code, err := parseAPKVersion(raw)
		state := "unavailable"
		if enabled, stateErr := parseAPKEnabled(raw); stateErr == nil {
			state = "disabled"
			if enabled {
				state = "enabled"
			}
		}
		return path, name, code, state, err
	}
	path, name, code, state, err := readPackage()
	if err == nil {
		sum, hashErr := probe.hash(ctx, path, updateFileLimit)
		if hashErr == nil {
			afterPath, afterName, afterCode, afterState, afterErr := readPackage()
			if afterErr == nil && path == afterPath && name == afterName && code == afterCode && state == afterState {
				apk = map[string]any{"state": "ready", "package": packageName, "version_name": name, "version_code": code, "sha256": sum, "app_state": state}
			} else {
				apk["reason"] = "package_changed"
			}
		} else {
			apk["reason"] = "file_unavailable"
		}
	}
	return map[string]any{"version": 1, "agent": agent, "apk": apk, "touch_guard": guard}
}

func updateStatus(ctx context.Context) map[string]any {
	child, cancel := context.WithTimeout(ctx, 4*time.Second)
	defer cancel()
	result := collectUpdateStatus(child, updateStatusProbe{boundedUpdateCommand, hashUpdateFile})
	result["bundle_state"] = bundleUpdateState()
	return result
}
