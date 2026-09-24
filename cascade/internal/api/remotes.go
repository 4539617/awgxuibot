// remotes.go — HTTP handlers for remote Cascade server management.
//
// Routes:
//
//	GET    /api/remotes              — list registered remote servers
//	POST   /api/remotes              — add remote (login→token→logout)
//	DELETE /api/remotes/:id          — remove remote
//	POST   /api/remotes/:id/test     — test connectivity (ping)
//	ALL    /api/remotes/:id/proxy/*  — proxy request to remote server
package api

import (
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/gofiber/fiber/v2"

	"github.com/JohnnyVBut/cascade/internal/remoteclient"
	"github.com/JohnnyVBut/cascade/internal/remotes"
)

// newProxyClient builds an HTTP client for proxying requests to a remote server.
//
// Two problems are addressed:
//
//  1. HTTP/2 panic: concurrent requests through a shared HTTP/2 ClientConn
//     trigger a data race in Go's hpack encoder. TLSNextProto (empty map)
//     disables the post-handshake HTTP/2 upgrade.
//
//  2. SNI + ALPN mismatch: Go's TLS stack always sends the URL hostname as
//     the TLS ServerName (SNI). When the remote URL contains a bare IP address
//     (e.g. https://1.2.3.4/path), Go sends that IP as SNI. Some Caddy/nginx
//     deployments reject an IP in the SNI field with TLS alert 80
//     (internal_error). Fix: set ServerName="" when the host is an IP, which
//     suppresses SNI entirely — matching what `openssl s_client -noservername`
//     does. Additionally restrict ALPN to "http/1.1" to avoid h2 negotiation
//     errors on servers that don't support HTTP/2 on that vhost.
func newProxyClient(remoteURL string, skipVerify bool, timeout time.Duration) *http.Client {
	tlsCfg := &tls.Config{
		InsecureSkipVerify: skipVerify,       //nolint:gosec
		NextProtos:         []string{"http/1.1"},
	}

	// Suppress SNI when the host is a bare IP address.
	if u, err := url.Parse(remoteURL); err == nil {
		if net.ParseIP(u.Hostname()) != nil {
			tlsCfg.ServerName = " " // non-empty sentinel → Go skips auto-SNI from the request host
		}
	}

	return &http.Client{
		Timeout: timeout,
		Transport: &http.Transport{
			TLSNextProto:    make(map[string]func(string, *tls.Conn) http.RoundTripper),
			DialContext:     remoteclient.SafeDialContext,
			TLSClientConfig: tlsCfg,
		},
	}
}

// Package-level shared HTTP clients for proxying.
//
// proxyClient / proxyClientInsecure — standard 5 s timeout for all proxy calls.
// speedtestProxyClient / speedtestProxyClientInsecure — 120 s timeout for
// /speedtest/client calls (iperf3 can take up to 30 s + overhead).
//
// These are initialised with an empty remoteURL so SNI suppression (IP-host
// detection inside newProxyClient) is skipped; SNI behaviour is the same as
// before for the shared clients.
var (
	proxyClient                  = newProxyClient("", false, 5*time.Second)
	proxyClientInsecure          = newProxyClient("", true, 5*time.Second)
	speedtestProxyClient         = newProxyClient("", false, 120*time.Second)
	speedtestProxyClientInsecure = newProxyClient("", true, 120*time.Second)
)

// RegisterRemotes registers all /api/remotes/* routes.
func RegisterRemotes(api fiber.Router) {
	g := api.Group("/remotes")
	g.Get("/", listRemotes)
	g.Post("/", addRemote)
	g.Delete("/:id", deleteRemote)
	g.Post("/:id/test", testRemote)
	g.All("/:id/proxy/*", proxyRemote)
}

// GET /api/remotes
func listRemotes(c *fiber.Ctx) error {
	list, err := remotes.List()
	if err != nil {
		return fiber.NewError(fiber.StatusInternalServerError, err.Error())
	}
	return c.JSON(fiber.Map{"remotes": list})
}

// POST /api/remotes
// Body: { name, url, username, password, totpCode? } — login mode, OR
//       { name, url, token }                          — explicit-token mode.
//
// Login mode: connects to the remote, obtains an API token, stores it.
// If the remote has 2FA enabled and totpCode is omitted, responds with
// 422 { totp_required: true } so the client can ask for the TOTP code
// and retry with it included.
//
// Explicit-token mode (token field set): validates the supplied token
// against the remote (Ping) and stores it directly — no login is performed.
func addRemote(c *fiber.Ctx) error {
	var body struct {
		Name          string `json:"name"`
		URL           string `json:"url"`
		Username      string `json:"username"`
		Password      string `json:"password"`
		TOTPCode      string `json:"totpCode"`      // optional — only needed when remote has 2FA
		Token         string `json:"token"`         // optional — when set, skip login and use this token directly
		SkipTLSVerify bool   `json:"skipTlsVerify"` // optional — skip TLS cert verification (self-signed)
	}
	if err := c.BodyParser(&body); err != nil {
		return fiber.NewError(fiber.StatusBadRequest, "invalid JSON body")
	}
	if body.Name == "" || body.URL == "" {
		return fiber.NewError(fiber.StatusBadRequest, "name and url are required")
	}

	// Validate URL — must be http/https and resolve only to public addresses
	// (SSRF guard; see remoteclient/ssrf.go).
	if err := remoteclient.ValidateRemoteURL(body.URL); err != nil {
		return fiber.NewError(fiber.StatusBadRequest, err.Error())
	}

	var token string
	if body.Token != "" {
		// ── Explicit-token mode ──────────────────────────────────────────────
		// The user supplied an API token directly. Validate it against the remote
		// before storing so a typo or revoked token is caught immediately.
		if err := remoteclient.Ping(body.URL, body.Token, body.SkipTLSVerify); err != nil {
			return fiber.NewError(fiber.StatusBadGateway, fmt.Sprintf("token validation failed: %s", err.Error()))
		}
		token = body.Token
	} else {
		// ── Login mode ───────────────────────────────────────────────────────
		if body.Username == "" || body.Password == "" {
			return fiber.NewError(fiber.StatusBadRequest, "username and password are required")
		}
		// Login → (TOTP verify) → create token → logout on remote.
		t, err := remoteclient.ObtainToken(body.URL, body.Username, body.Password, body.TOTPCode, body.SkipTLSVerify)
		if errors.Is(err, remoteclient.ErrTOTPRequired) {
			// Remote has 2FA — tell the client to ask for the TOTP code.
			return c.Status(fiber.StatusUnprocessableEntity).JSON(fiber.Map{"totp_required": true})
		}
		if err != nil {
			return fiber.NewError(fiber.StatusBadGateway, fmt.Sprintf("could not obtain token from remote: %s", err.Error()))
		}
		token = t
	}

	remote, err := remotes.Add(body.Name, body.URL, token, body.SkipTLSVerify)
	if err != nil {
		return fiber.NewError(fiber.StatusInternalServerError, err.Error())
	}
	return c.Status(fiber.StatusCreated).JSON(fiber.Map{"remote": remote})
}

// DELETE /api/remotes/:id
func deleteRemote(c *fiber.Ctx) error {
	if err := remotes.Delete(c.Params("id")); err != nil {
		return fiber.NewError(fiber.StatusNotFound, err.Error())
	}
	return c.SendStatus(fiber.StatusNoContent)
}

// POST /api/remotes/:id/test
// Returns { ok: true, version: "..." } or error.
func testRemote(c *fiber.Ctx) error {
	r, err := remotes.Get(c.Params("id"))
	if err != nil {
		return fiber.NewError(fiber.StatusNotFound, err.Error())
	}
	if err := remoteclient.Ping(r.URL, r.Token, r.SkipTLSVerify); err != nil {
		return fiber.NewError(fiber.StatusBadGateway, err.Error())
	}
	return c.JSON(fiber.Map{"ok": true})
}

// ALL /api/remotes/:id/proxy/*
// Forwards the request to the remote server with its stored Bearer token.
// The browser never sees the token — it only communicates with this server.
func proxyRemote(c *fiber.Ctx) error {
	r, err := remotes.Get(c.Params("id"))
	if err != nil {
		return fiber.NewError(fiber.StatusNotFound, "remote not found")
	}

	// Build target URL: remote base + /api/ + everything after /proxy
	// The local api.js strips the /api prefix from paths (e.g. "/tunnel-interfaces"),
	// so we must re-add /api/ when forwarding to the remote server.
	subPath := c.Params("*")
	if subPath == "" {
		subPath = "/"
	}
	if !strings.HasPrefix(subPath, "/") {
		subPath = "/" + subPath
	}
	targetURL := strings.TrimRight(r.URL, "/") + "/api" + subPath
	if qs := string(c.Request().URI().QueryString()); qs != "" {
		targetURL += "?" + qs
	}

	// Create outgoing request.
	req, err := http.NewRequest(c.Method(), targetURL, strings.NewReader(string(c.Body())))
	if err != nil {
		return fiber.NewError(fiber.StatusInternalServerError, "proxy build request: "+err.Error())
	}

	// Forward relevant headers (Content-Type, Accept, etc.) but inject our token.
	for k, vals := range c.GetReqHeaders() {
		lower := strings.ToLower(k)
		if lower == "authorization" || lower == "cookie" || lower == "host" {
			continue // don't forward auth/session headers from the browser
		}
		for _, v := range vals {
			req.Header.Add(k, v)
		}
	}
	req.Header.Set("Authorization", "Bearer "+r.Token)

	// Use a longer timeout for speedtest/client — iperf3 runs can take up to 30 s.
	// Use insecure clients for remotes with self-signed certificates.
	var client *http.Client
	switch {
	case strings.HasSuffix(subPath, "/speedtest/client") && r.SkipTLSVerify:
		client = speedtestProxyClientInsecure
	case strings.HasSuffix(subPath, "/speedtest/client"):
		client = speedtestProxyClient
	case r.SkipTLSVerify:
		client = proxyClientInsecure
	default:
		client = proxyClient
	}
	resp, err := client.Do(req)
	if err != nil {
		log.Printf("[proxy] %s %s → remote error: %v", c.Method(), targetURL, err)
		return fiber.NewError(fiber.StatusBadGateway, "proxy request failed: "+err.Error())
	}
	defer resp.Body.Close()

	// Copy response status + safe headers + body.
	// IMPORTANT: never forward Set-Cookie from the remote — it would overwrite
	// the browser's local session cookie, causing immediate auth loss on the
	// local server. Also skip hop-by-hop headers that must not be forwarded.
	skipHeaders := map[string]bool{
		"Set-Cookie":          true,
		"Connection":          true,
		"Transfer-Encoding":   true,
		"Upgrade":             true,
		"Proxy-Authenticate":  true,
		"Proxy-Authorization": true,
		"Te":                  true,
		"Trailer":             true,
	}
	c.Status(resp.StatusCode)
	for k, vals := range resp.Header {
		if skipHeaders[k] {
			continue
		}
		for _, v := range vals {
			c.Set(k, v)
		}
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("[proxy] %s %s → read error (status %d): %v", c.Method(), targetURL, resp.StatusCode, err)
		return fiber.NewError(fiber.StatusBadGateway, "proxy read response: "+err.Error())
	}
	// Log non-2xx responses from the remote for diagnostics.
	if resp.StatusCode >= 400 {
		log.Printf("[proxy] %s %s → remote status %d", c.Method(), targetURL, resp.StatusCode)
	}
	return c.Send(body)
}
