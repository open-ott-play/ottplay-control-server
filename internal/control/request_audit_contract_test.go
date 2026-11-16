package control

import (
	"encoding/json"
	"os"
	"sort"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func TestRequestAuditSharedContractPermissions(t *testing.T) {
	raw, err := os.ReadFile("../../contracts/request-audit-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Version         int `json:"version"`
		Scope, Endpoint string
		Families        []string         `json:"tracked_action_families"`
		Permissions     []string         `json:"permissions"`
		Limits          map[string]int64 `json:"limits"`
	}
	if json.Unmarshal(raw, &f) != nil || f.Version != 1 || f.Scope != "controller_rpc_ingress" || f.Endpoint != requestAuditPath || len(f.Families) != 13 || len(f.Permissions) != 69 || f.Limits["max_entries_per_device"] != requestAuditPerDevice || f.Limits["max_entries_total"] != requestAuditTotal || f.Limits["max_age_ms"] != requestAuditAgeMS || f.Limits["max_response_bytes"] != requestAuditMaxResponseBytes {
		t.Fatal("incomplete audit contract")
	}
	for _, family := range f.Families {
		if !requestAuditFamily(family) {
			t.Fatal("shared tracked family refused")
		}
	}
	for _, permission := range f.Permissions {
		if !requestAuditFamily(permission) || !config.ValidCommandAction(permission) {
			t.Fatalf("shared permission invalid %s", permission)
		}
	}
	actual := []string{"play", "play_catalog", "provider", "profile"}
	for family, ops := range config.CommandOperations {
		if requestAuditFamily(family) {
			for _, op := range strings.Fields(ops) {
				actual = append(actual, family+"."+op)
			}
		}
	}
	sort.Strings(actual)
	if len(actual) != len(f.Permissions) {
		t.Fatal("shared permissions do not cover controller")
	}
	for i, p := range actual {
		if p != f.Permissions[i] {
			t.Fatal("shared permissions differ")
		}
	}
}
