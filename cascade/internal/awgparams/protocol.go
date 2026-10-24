package awgparams

import (
	"fmt"
	"os"
)

// Protocol string constants stored in interfaces.protocol / used across the
// tunnel/peer/api packages. Centralized here (rather than duplicated as
// string literals) so a future protocol version bump is a small, safe change
// instead of finding every exact-match comparison by hand.
const (
	ProtocolWireGuard1  = "wireguard-1.0"
	ProtocolAmneziaWG1  = "amneziawg-1.0" // AWG 1.0 — legacy Keenetic-compatible subset: Jc/Jmin/Jmax/S1/S2/H1-H4 only (no S3/S4/I1-I5)
	ProtocolAmneziaWG2  = "amneziawg-2.0"
	ProtocolAmneziaWG3  = "amneziawg-3.0"
)

// IsAmneziaWG reports whether protocol is any AmneziaWG obfuscation line
// (1.0, 2.0 or 3.0). All use the same AWG2Settings-shaped params for storage;
// 1.0 omits S3/S4/I1-I5 from generated configs (Keenetic legacy compatibility);
// 3.0 additionally allows Transport Protection fields (enforced separately).
func IsAmneziaWG(protocol string) bool {
	return protocol == ProtocolAmneziaWG1 || protocol == ProtocolAmneziaWG2 || protocol == ProtocolAmneziaWG3
}

// IsAmneziaWGLegacy reports whether the protocol is the Keenetic-compatible
// AWG 1.0 subset — configs must omit S3, S4 and I1-I5.
func IsAmneziaWGLegacy(protocol string) bool {
	return protocol == ProtocolAmneziaWG1
}

// IsUserspaceMode reports whether AmneziaWG is running via the amneziawg-go
// userspace daemon rather than the in-kernel module. This is a deployment-wide
// setting baked into the container's environment at startup (see
// deploy/setup.sh and deploy/switch-mode.sh) — it does not change for the
// lifetime of the process, so it is safe to read on every call.
//
// This is the single place that reads WG_QUICK_USERSPACE_IMPLEMENTATION.
// Do not duplicate this os.Getenv check elsewhere: internal/tunnel uses it to
// pick a safe kernel-sync strategy (Reload vs. disruptive Restart) and
// internal/api reports it for diagnostics — if those checks ever drifted
// apart (e.g. one is updated for a new implementation value and the other
// isn't), tunnel and API would silently disagree about which mode is active.
func IsUserspaceMode() bool {
	return os.Getenv("WG_QUICK_USERSPACE_IMPLEMENTATION") == "amneziawg-go"
}

// ValidateHeaderProtectionKeyPadding returns an error if headerProtectionKey
// is set but s3/s4 are below 12 — amneziawg-go and the kernel module both
// refuse to start with a smaller S3/S4 padding buffer once a
// HeaderProtectionKey is configured (the key's cipher nonce is taken from
// that padding). Shared by every path that can persist an AWG2Settings with
// HeaderProtectionKey set: internal/settings.CreateTemplate/UpdateTemplate,
// internal/api's interface create/update handlers, and
// internal/tunnel.CreateInterface (used directly by ImportConf, which
// bypasses the API handlers' validation).
func ValidateHeaderProtectionKeyPadding(headerProtectionKey string, s3, s4 int) error {
	if headerProtectionKey != "" && (s3 < 12 || s4 < 12) {
		return fmt.Errorf("headerProtectionKey requires S3 and S4 to both be at least 12")
	}
	return nil
}
