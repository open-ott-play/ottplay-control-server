package config

import (
	"errors"
	"path/filepath"
)

// PlayerState enables durable semantic history. A nil configuration uses RAM.
type PlayerState struct {
	Path string `json:"path"`
}

func (p PlayerState) Validate() error {
	if !filepath.IsAbs(p.Path) || filepath.Clean(p.Path) != p.Path || filepath.Base(p.Path) == "." || filepath.Base(p.Path) == string(filepath.Separator) {
		return errors.New("player_state requires an absolute private file path")
	}
	return nil
}
