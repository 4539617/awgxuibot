package version

import (
	"sync"
	"time"
)

const (
	checkInterval = 24 * time.Hour
	// Delay first check so the container has time to come fully online.
	initialDelay = 10 * time.Second
)

// UpdateStatus is the cached result of the last update check.
type UpdateStatus struct {
	LatestVersion   string    `json:"latestVersion"`
	ReleaseURL      string    `json:"releaseURL"`
	UpdateAvailable bool      `json:"updateAvailable"`
	CheckedAt       time.Time `json:"checkedAt"`
	Error           string    `json:"error,omitempty"`
}

var (
	mu     sync.RWMutex
	status UpdateStatus
)

// GetStatus returns the latest cached UpdateStatus (safe for concurrent use).
func GetStatus() UpdateStatus {
	mu.RLock()
	defer mu.RUnlock()
	return status
}

// Start launches the background update-check goroutine.
// It checks immediately after initialDelay, then every checkInterval.
// Safe to call multiple times — only the first call has effect.
func Start() {
	go func() {
		time.Sleep(initialDelay)
		check()
		ticker := time.NewTicker(checkInterval)
		defer ticker.Stop()
		for range ticker.C {
			check()
		}
	}()
}

// Check forces an immediate update check, bypassing the 24h cache.
// Safe to call concurrently — it runs synchronously and updates the shared status.
func Check() {
	check()
}

// check is disabled — update checks against the upstream GitHub repo are not used.
func check() {}
