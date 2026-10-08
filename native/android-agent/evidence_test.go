package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestSurfaceEvidenceDoesNotClaimVideoOrPhysicalDisplay(t *testing.T) {
	for _, s := range []string{"", "16666666\n0\t0\t0\n", "16666666\n1 9223372036854775807 2\n", "16666666\nprivate data"} {
		if surfaceEvidence([]byte(s))["state"] != "unavailable" {
			t.Fatal("invalid fence accepted")
		}
	}
	got := surfaceEvidence([]byte("16666666\n10 20 15\n0 0 0\n30 40 35\n"))
	if got["latest_present_ns"] != int64(40) || got["completed_frames"] != 2 || got["video_verified"] != false {
		t.Fatal(got)
	}
	name := packageName + "/" + packageName + ".MainActivity"
	if ownSurface([]byte("private app\n"+name+"\n")) != name || ownSurface([]byte(name+"\n"+name)) != "" || ownSurface([]byte("SurfaceView")) != "" {
		t.Fatal("ambiguous surface ownership")
	}
}
func TestAudioEvidenceFiltersOtherProcessesAndUnknownFormats(t *testing.T) {
	header := "Output thread 0x1 active tracks\n   Name Client Type      Fmt Chn mask Session fCount S F SRate  L dB  R dB    Server Main buf  Aux Buf Flags UndFrmCnt\n"
	row := "  1 123 3 00000001 00000003 44 1024 A 1 48000 0 0 000000ff 00000000 00000000 0x001 12*\n"
	got := audioEvidence([]byte("private info\n"+header+row), 123)
	if got["state"] != "observed" || got["audible_verified"] != false {
		t.Fatal(got)
	}
	tracks := got["tracks"].([]map[string]any)
	if len(tracks) != 1 || tracks[0]["server_frames"] != uint64(255) || tracks[0]["underrun_frames"] != uint64(12) {
		t.Fatal(tracks)
	}
	if audioEvidence([]byte(header+row), 999)["reason"] != "no_matching_audio_track" {
		t.Fatal("other process attributed")
	}
	if audioEvidence([]byte(strings.ReplaceAll(header, "Session fCount", "unknown")+row), 123)["state"] != "unavailable" {
		t.Fatal("unknown vendor format accepted")
	}
	b, _ := json.Marshal(got)
	if strings.Contains(string(b), "private") {
		t.Fatal("raw dump leaked")
	}
}
