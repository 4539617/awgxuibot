import { promises as dns } from 'dns';
import { Resolver } from 'dns';

// DNS servers to query
const DNS_SERVERS = [
  '77.88.8.8',      // Yandex DNS
  '77.88.8.1',      // Yandex DNS Secondary
  '77.88.8.88',     // Yandex DNS Family
  '8.8.8.8',        // Google DNS
  '8.8.4.4',        // Google DNS Secondary
  '1.1.1.1',        // Cloudflare DNS
  '1.0.0.1',        // Cloudflare DNS Secondary
  '208.67.222.222', // OpenDNS
  '208.67.220.220', // OpenDNS Secondary
];

/**
 * Resolve domain using multiple DNS servers
 * @param {string} domain - Domain name to resolve
 * @returns {Promise<{ipv4: string[]}>}
 */
export async function resolveDomain(domain) {
  // Remove protocol and path if present
  const cleanDomain = domain
    .replace(/^https?:\/\//, '')
    .replace(/^www\./, '')
    .split('/')[0]
    .split(':')[0]
    .trim();

  const ipSet = new Set();

  // Try system DNS first
  try {
    const ipv4Addresses = await dns.resolve4(cleanDomain);
    ipv4Addresses.forEach(ip => ipSet.add(ip));
  } catch (error) {
    console.log(`System DNS: No IPv4 addresses found for ${cleanDomain}`);
  }

  // Query each DNS server
  for (const dnsServer of DNS_SERVERS) {
    try {
      const resolver = new Resolver();
      resolver.setServers([dnsServer]);
      
      const resolve4Promise = new Promise((resolve, reject) => {
        resolver.resolve4(cleanDomain, (err, addresses) => {
          if (err) reject(err);
          else resolve(addresses);
        });
      });

      const addresses = await resolve4Promise;
      addresses.forEach(ip => ipSet.add(ip));
      console.log(`DNS ${dnsServer}: Found ${addresses.length} addresses for ${cleanDomain}`);
    } catch (error) {
      console.log(`DNS ${dnsServer}: No addresses found for ${cleanDomain}`);
    }
  }

  return {
    ipv4: Array.from(ipSet).sort()
  };
}

/**
 * Resolve multiple domains
 * @param {string[]} domains - Array of domain names
 * @returns {Promise<Map<string, {ipv4: string[]}>>}
 */
export async function resolveMultipleDomains(domains) {
  const results = new Map();

  for (const domain of domains) {
    try {
      const addresses = await resolveDomain(domain);
      if (addresses.ipv4.length > 0) {
        results.set(domain, addresses);
      }
    } catch (error) {
      console.error(`Error resolving ${domain}:`, error.message);
    }
  }

  return results;
}
