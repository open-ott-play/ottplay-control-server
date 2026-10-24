package config

import "testing"

func TestHomeAssistantCameraAllowlist(t *testing.T) {
	valid := func() HomeAssistant {
		c := validHAConfig(t)
		c.Cameras = map[string]HomeAssistantCamera{"garage": {EntityID: "camera.garage_notifications", Label: "Garage"}}
		c.Rules = []HomeAssistantRule{{ID: "camera", EventType: "cisco_motion_snapshot_requested", CameraKey: "garage", Title: "Motion", Message: "{{name}}: motion detected"}}
		c.Defaults()
		return c
	}
	c := valid()
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	for name, change := range map[string]func(*HomeAssistant){
		"url": func(c *HomeAssistant) {
			c.Cameras["garage"] = HomeAssistantCamera{EntityID: "https://camera.invalid", Label: "Garage"}
		},
		"path": func(c *HomeAssistant) {
			c.Cameras["garage"] = HomeAssistantCamera{EntityID: "camera.x/../../other", Label: "Garage"}
		},
		"unknown key":       func(c *HomeAssistant) { c.Rules[0].CameraKey = "other" },
		"unfiltered event":  func(c *HomeAssistant) { c.Rules[0].CameraKey = "" },
		"wrong event":       func(c *HomeAssistant) { c.Rules[0].EventType = "other" },
		"duplicate capture": func(c *HomeAssistant) { r := c.Rules[0]; r.ID = "other"; c.Rules = append(c.Rules, r) },
		"template label": func(c *HomeAssistant) {
			c.Cameras["garage"] = HomeAssistantCamera{EntityID: "camera.garage", Label: "{{state}}"}
		},
	} {
		t.Run(name, func(t *testing.T) {
			c := valid()
			change(&c)
			if c.Validate() == nil {
				t.Fatal("unsafe camera config accepted")
			}
		})
	}
}
