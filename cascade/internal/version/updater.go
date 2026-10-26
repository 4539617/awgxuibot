package version

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	checkInterval = 24 * time.Hour
	// Delay first check so the container has time to come fully online.
	initialDelay = 10 * time.Second

	githubReleaseURL = "https://api.github.com/repos/JohnnyVBut/cascade/releases/latest"
	releasesPageURL  = "https://github.com/JohnnyVBut/cascade/releases"
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
	mu      sync.RWMutex
	status  UpdateStatus
	started bool
	startMu sync.Mutex
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
	startMu.Lock()
	defer startMu.Unlock()
	if started {
		return
	}
	started = true
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

// check fetches the latest release from GitHub and updates the cached status.
func check() {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, githubReleaseURL, nil)
	if err != nil {
		setError(fmt.Sprintf("build request: %v", err))
		return
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", "cascade/"+Version)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		setError(fmt.Sprintf("github fetch: %v", err))
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		setError(fmt.Sprintf("github returned %d", resp.StatusCode))
		return
	}

	var release struct {
		TagName string `json:"tag_name"`
		HTMLURL string `json:"html_url"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&release); err != nil {
		setError(fmt.Sprintf("decode: %v", err))
		return
	}

	latest := release.TagName
	releaseURL := release.HTMLURL
	if releaseURL == "" {
		releaseURL = releasesPageURL
	}

	mu.Lock()
	defer mu.Unlock()
	status = UpdateStatus{
		LatestVersion:   latest,
		ReleaseURL:      releaseURL,
		UpdateAvailable: latest != "" && Version != "dev" && compareSemver(latest, Version) > 0,
		CheckedAt:       time.Now(),
	}
}

func setError(msg string) {
	mu.Lock()
	defer mu.Unlock()
	status.Error = msg
	status.CheckedAt = time.Now()
}

// parseSemver parses a semver string into [major, minor, patch].
// Strips a leading "v" and any pre-release suffix (e.g. "-rc1").
// Returns {0,0,0} for "dev" or unparseable input.
func parseSemver(v string) [3]int {
	v = strings.TrimPrefix(v, "v")
	// strip pre-release suffix
	if idx := strings.IndexAny(v, "-+"); idx >= 0 {
		v = v[:idx]
	}
	parts := strings.SplitN(v, ".", 3)
	var result [3]int
	for i, p := range parts {
		if i >= 3 {
			break
		}
		n, _ := strconv.Atoi(p)
		result[i] = n
	}
	return result
}

// compareSemver returns 1 if a > b, -1 if a < b, 0 if equal.
func compareSemver(a, b string) int {
	pa := parseSemver(a)
	pb := parseSemver(b)
	for i := range pa {
		if pa[i] > pb[i] {
			return 1
		}
		if pa[i] < pb[i] {
			return -1
		}
	}
	return 0
}
