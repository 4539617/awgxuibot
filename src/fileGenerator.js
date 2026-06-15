import fs from 'fs';
import path from 'path';
import { config } from './config.js';

/**
 * Generate routing commands for Windows batch file
 * @param {Map<string, {ipv4: string[]}>} domainsMap - Map of domains to their IP addresses
 * @param {string} filename - Output filename
 * @returns {string} - Generated file content
 */
export function generateBatchFile(domainsMap, filename) {
  let content = '';

  for (const [domain, addresses] of domainsMap) {
    // Add IPv4 routes with domain comment
    for (const ip of addresses.ipv4) {
      content += `route add ${ip} mask 255.255.255.255 0.0.0.0 :: rem ${domain}\n`;
    }
  }

  return content;
}

/**
 * Save batch file to disk
 * @param {string} content - File content
 * @param {string} filename - Output filename
 * @returns {string} - Full path to saved file
 */
export function saveBatchFile(content, filename) {
  // Ensure output directory exists
  if (!fs.existsSync(config.outputDir)) {
    fs.mkdirSync(config.outputDir, { recursive: true });
  }

  // Sanitize filename
  const sanitizedFilename = filename
    .replace(/[^a-z0-9_\-\.]/gi, '_')
    .replace(/_{2,}/g, '_');

  const filepath = path.join(config.outputDir, sanitizedFilename);
  fs.writeFileSync(filepath, content, 'utf8');

  return filepath;
}

/**
 * Generate filename from domain name
 * @param {string} domain - Domain name
 * @returns {string} - Generated filename
 */
export function generateFilename(domain) {
  const cleanDomain = domain
    .replace(/^https?:\/\//, '')
    .replace(/^www\./, '')
    .split('/')[0]
    .split(':')[0]
    .trim()
    .replace(/\./g, '_');

  return `${cleanDomain}_keenetic.bat`;
}

/**
 * Generate filename for multiple domains
 * @param {string[]} domains - Array of domain names
 * @returns {string} - Generated filename
 */
export function generateMultipleDomainsFilename(domains) {
  if (domains.length === 1) {
    return generateFilename(domains[0]);
  }

  // Get first domain name
  const firstDomain = domains[0]
    .replace(/^https?:\/\//, '')
    .replace(/^www\./, '')
    .split('/')[0]
    .split(':')[0]
    .trim()
    .replace(/\./g, '_');

  // Calculate number of other domains
  const otherCount = domains.length - 1;

  return `${firstDomain}_and_${otherCount}_other_keenetic.bat`;
}
