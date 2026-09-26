// keenetic.go — Keenetic .bat file generator.
//
// Routes (all require auth):
//
//	POST /api/keenetic/generate-bat  — resolve domains → return .bat file for download
package api

import (
	"context"
	"fmt"
	"net"
	"sort"
	"strings"
	"time"

	"github.com/gofiber/fiber/v2"
)

// RegisterKeenetic registers /api/keenetic/* routes.
func RegisterKeenetic(api fiber.Router) {
	g := api.Group("/keenetic")
	g.Post("/generate-bat", keeneticGenerateBat)
}

type keeneticRequest struct {
	Domains []string `json:"domains"`
}

// keeneticGenerateBat resolves provided domains via multiple DNS servers and
// returns a Windows .bat file with "route add" commands for each resolved IP.
func keeneticGenerateBat(c *fiber.Ctx) error {
	var req keeneticRequest
	if err := c.BodyParser(&req); err != nil {
		return fiber.NewError(fiber.StatusBadRequest, "invalid JSON body")
	}

	// Clean and deduplicate domains.
	seen := make(map[string]bool)
	var domains []string
	for _, d := range req.Domains {
		d = strings.TrimSpace(d)
		if d == "" {
			continue
		}
		// Strip protocol and path
		d = strings.TrimPrefix(d, "https://")
		d = strings.TrimPrefix(d, "http://")
		d = strings.TrimPrefix(d, "www.")
		if i := strings.IndexByte(d, '/'); i != -1 {
			d = d[:i]
		}
		if i := strings.IndexByte(d, ':'); i != -1 {
			d = d[:i]
		}
		d = strings.ToLower(d)
		if d == "" || seen[d] {
			continue
		}
		seen[d] = true
		domains = append(domains, d)
	}

	if len(domains) == 0 {
		return fiber.NewError(fiber.StatusBadRequest, "no valid domains provided")
	}

	// DNS servers to query (same list as awgbot dnsResolver.js)
	dnsServers := []string{
		"77.88.8.8",       // Yandex DNS
		"77.88.8.1",       // Yandex DNS Secondary
		"8.8.8.8",         // Google DNS
		"8.8.4.4",         // Google DNS Secondary
		"1.1.1.1",         // Cloudflare DNS
		"1.0.0.1",         // Cloudflare DNS Secondary
		"208.67.222.222",  // OpenDNS
		"208.67.220.220",  // OpenDNS Secondary
	}

	// Resolve all domains.
	// domainIPs maps domain → sorted unique IPs.
	domainIPs := make(map[string][]string)
	for _, domain := range domains {
		ipSet := make(map[string]bool)

		// System resolver first.
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		addrs, err := net.DefaultResolver.LookupHost(ctx, domain)
		cancel()
		if err == nil {
			for _, a := range addrs {
				if ip := net.ParseIP(a); ip != nil && ip.To4() != nil {
					ipSet[a] = true
				}
			}
		}

		// Query each DNS server via custom resolver.
		for _, srv := range dnsServers {
			srv := srv // capture loop variable
			r := &net.Resolver{
				PreferGo: true,
				Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
					d := net.Dialer{Timeout: 3 * time.Second}
					return d.DialContext(ctx, "udp", srv+":53")
				},
			}
			ctx2, cancel2 := context.WithTimeout(context.Background(), 5*time.Second)
			addrs2, err2 := r.LookupHost(ctx2, domain)
			cancel2()
			if err2 != nil {
				continue
			}
			for _, a := range addrs2 {
				if ip := net.ParseIP(a); ip != nil && ip.To4() != nil {
					ipSet[a] = true
				}
			}
		}

		if len(ipSet) > 0 {
			ips := make([]string, 0, len(ipSet))
			for ip := range ipSet {
				ips = append(ips, ip)
			}
			sort.Strings(ips)
			domainIPs[domain] = ips
		}
	}

	// Build .bat content — same format as awgbot fileGenerator.js.
	var sb strings.Builder
	for _, domain := range domains {
		ips, ok := domainIPs[domain]
		if !ok {
			continue
		}
		for _, ip := range ips {
			sb.WriteString(fmt.Sprintf("route add %s mask 255.255.255.255 0.0.0.0 :: rem %s\n", ip, domain))
		}
	}

	if sb.Len() == 0 {
		return fiber.NewError(fiber.StatusUnprocessableEntity, "could not resolve any of the provided domains")
	}

	// Build filename.
	filename := buildKeeneticFilename(domains)

	c.Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	c.Set("Content-Type", "application/octet-stream")
	return c.SendString(sb.String())
}

// buildKeeneticFilename mirrors awgbot generateMultipleDomainsFilename().
func buildKeeneticFilename(domains []string) string {
	clean := func(d string) string {
		r := strings.NewReplacer(".", "_")
		return r.Replace(d)
	}
	if len(domains) == 1 {
		return clean(domains[0]) + "_keenetic.bat"
	}
	return fmt.Sprintf("%s_and_%d_other_keenetic.bat", clean(domains[0]), len(domains)-1)
}
