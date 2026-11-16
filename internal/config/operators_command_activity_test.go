package config

import "testing"

func TestCommandActivityExactPermission(t *testing.T) {
	if !ValidCommandAction("command_activity_diagnostics") {
		t.Fatal("passive action permission missing")
	}
	for _, action := range []string{"command_activity_diagnostics.*", "command_activity_diagnostics.read", "command_activity_diagnostics.start", "command_activity_diagnostics ", "COMMAND_ACTIVITY_DIAGNOSTICS"} {
		if ValidCommandAction(action) {
			t.Fatal("overbroad permission accepted")
		}
	}
}
