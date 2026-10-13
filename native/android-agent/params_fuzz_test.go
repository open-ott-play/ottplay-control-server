package main

import (
	"encoding/json"
	"testing"
)

func FuzzNativeParameters(f *testing.F) {
	for _, raw := range []string{`{}`, `{"operation":"pause"}`, `{"operation":"seek","position":10}`, `{"operation":"pause","operation":"reboot_device"}`, `{"nested":{"a":1}}`, `{} {}`, `null`} {
		f.Add([]byte(raw))
	}
	f.Fuzz(func(t *testing.T, raw []byte) {
		if len(raw) > 64*1024 {
			t.Skip()
		}
		parsed, err := params(raw)
		if err != nil {
			return
		}
		if !json.Valid(raw) || parsed == nil {
			t.Fatal("accepted non-object or malformed command parameters")
		}
		wire, err := json.Marshal(parsed)
		if err != nil {
			t.Fatal(err)
		}
		again, err := params(wire)
		if err != nil || len(again) != len(parsed) {
			t.Fatal("re-encoding changed accepted command parameters")
		}
		// Every accepted key, including escaped Unicode keys, must be rejected
		// if repeated in the same operation envelope.
		for key, value := range parsed {
			encodedKey, err := json.Marshal(key)
			if err != nil {
				t.Fatal(err)
			}
			duplicate := append([]byte{'{'}, encodedKey...)
			duplicate = append(duplicate, ':')
			duplicate = append(duplicate, value...)
			duplicate = append(duplicate, ',')
			duplicate = append(duplicate, wire[1:]...)
			if _, err := params(duplicate); err == nil {
				t.Fatal("duplicate operation parameter accepted")
			}
			break
		}
		if _, err := params(append(wire, []byte(" {}")...)); err == nil {
			t.Fatal("trailing JSON command accepted")
		}
	})
}
