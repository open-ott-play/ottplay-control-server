package config

import (
	"errors"
	"path/filepath"
)

type CommandJournal struct {
	Path             string `json:"path"`
	MaxRecords       int    `json:"max_records"`
	RetentionSeconds int    `json:"retention_seconds"`
}

func (c *CommandJournal) Defaults() {
	if c.MaxRecords == 0 {
		c.MaxRecords = 1024
	}
	if c.RetentionSeconds == 0 {
		c.RetentionSeconds = 86400
	}
}
func (c *CommandJournal) Validate() error {
	if !filepath.IsAbs(c.Path) || c.MaxRecords < 1 || c.MaxRecords > 4096 || c.RetentionSeconds < 60 || c.RetentionSeconds > 86400 {
		return errors.New("journal requires an absolute private path, 1..4096 records and 60..86400 seconds retention")
	}
	return nil
}
