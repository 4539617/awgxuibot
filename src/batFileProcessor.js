import fs from 'fs';
import path from 'path';
import { promises as dns } from 'dns';
import { config } from './config.js';
import { logger } from './logger.js';

/**
 * Parse .bat file content and extract route commands with IP addresses
 * @param {string} content - File content
 * @returns {Array<{line: string, ip: string, gateway: string, comment: string|null}>}
 */
export function parseBatFile(content) {
  const lines = content.split('\n');
  const routes = [];
  
  // Regex to match route add commands
  // Matches: route add IP mask MASK GATEWAY [:: rem COMMENT]
  const routeRegex = /route\s+add\s+([\d.]+)\s+mask\s+([\d.]+)\s+([\d.]+)(?:\s+::\s+rem\s+(.+))?/i;
  
  for (const line of lines) {
    const match = line.trim().match(routeRegex);
    if (match) {
      routes.push({
        line: line.trim(),
        ip: match[1],
        mask: match[2],
        gateway: match[3],
        comment: match[4] ? match[4].trim() : null
      });
    }
  }
  
  return routes;
}

/**
 * Extract main domain from hostname (e.g., yandex.net from kp-nginx-stable-balancer.kp.yandex.net)
 * @param {string} hostname - Full hostname
 * @returns {string}
 */
export function extractMainDomain(hostname) {
  if (!hostname) return hostname;
  
  // Split by dots
  const parts = hostname.split('.');
  
  // If less than 2 parts, return as is
  if (parts.length < 2) {
    return hostname;
  }
  
  // Common two-part TLDs including Russian domains
  const twoPartTlds = [
    // International
    'co.uk', 'com.au', 'com.br', 'co.jp', 'co.kr', 'co.nz', 'co.za',
    'com.ar', 'com.mx', 'com.tr', 'com.tw', 'com.ua', 'com.vn',
    'net.au', 'org.uk', 'ac.uk', 'gov.uk', 'edu.au', 'gov.au',
    // Russian domains
    'gov.ru', 'edu.ru', 'com.ru', 'net.ru', 'org.ru', 'mil.ru',
    'pp.ru', 'msk.ru', 'spb.ru', 'nnov.ru', 'msk.su', 'spb.su'
  ];
  
  // Check if it's a two-part TLD
  if (parts.length >= 3) {
    const lastTwoParts = `${parts[parts.length - 2]}.${parts[parts.length - 1]}`;
    if (twoPartTlds.includes(lastTwoParts)) {
      // Return domain.gov.ru format
      return parts.slice(-3).join('.');
    }
  }
  
  // Return last two parts (domain.tld)
  return parts.slice(-2).join('.');
}

/**
 * Perform reverse DNS lookup for an IP address and verify it
 * @param {string} ip - IP address
 * @returns {Promise<string|null>}
 */
export async function reverseDnsLookup(ip) {
  try {
    const hostnames = await dns.reverse(ip);
    if (hostnames && hostnames.length > 0) {
      // Try each hostname and verify it resolves back to the same IP
      for (let hostname of hostnames) {
        // Remove trailing dot if present
        if (hostname.endsWith('.')) {
          hostname = hostname.slice(0, -1);
        }
        
        try {
          // Verify the hostname resolves back to the original IP
          const addresses = await dns.resolve4(hostname);
          if (addresses && addresses.includes(ip)) {
            const mainDomain = extractMainDomain(hostname);
            logger.info(`Reverse DNS verified for ${ip}: ${hostname} -> ${mainDomain}`);
            return mainDomain;
          }
        } catch (verifyError) {
          // Hostname doesn't resolve back, try next one
          logger.debug(`Hostname ${hostname} doesn't resolve back to ${ip}`);
        }
      }
      
      // If no hostname verified, return the first one anyway
      let hostname = hostnames[0];
      if (hostname.endsWith('.')) {
        hostname = hostname.slice(0, -1);
      }
      const mainDomain = extractMainDomain(hostname);
      logger.info(`Reverse DNS found (unverified) for ${ip}: ${hostname} -> ${mainDomain}`);
      return mainDomain;
    }
  } catch (error) {
    // Reverse DNS lookup failed - this is normal for many IPs
    logger.debug(`No reverse DNS for ${ip}: ${error.message}`);
  }
  
  return null;
}

/**
 * Get IP info from ip-api.com (free, no key required)
 * @param {string} ip - IP address
 * @returns {Promise<string|null>}
 */
export async function getIpInfo(ip) {
  try {
    const response = await fetch(`http://ip-api.com/json/${ip}?fields=status,org,isp,as`);
    if (!response.ok) {
      return null;
    }
    
    const data = await response.json();
    if (data.status === 'success') {
      // Prefer org, then isp, then as
      const info = data.org || data.isp || data.as;
      if (info) {
        logger.info(`IP info found for ${ip}: ${info}`);
        return info;
      }
    }
  } catch (error) {
    logger.debug(`Failed to get IP info for ${ip}: ${error.message}`);
  }
  
  return null;
}

/**
 * Process routes and add missing comments via reverse DNS and IP info
 * @param {Array} routes - Parsed routes
 * @returns {Promise<Array>}
 */
export async function addMissingComments(routes) {
  const processedRoutes = [];
  let apiCallCount = 0;
  const maxApiCalls = 45; // ip-api.com limit: 45 requests per minute
  
  for (const route of routes) {
    // Create a copy of the route object to avoid modifying the original
    const processedRoute = { ...route };
    
    if (!processedRoute.comment) {
      // Try reverse DNS lookup first
      let hostname = await reverseDnsLookup(processedRoute.ip);
      
      // If reverse DNS failed and we haven't hit API limit, try IP info API
      if (!hostname && apiCallCount < maxApiCalls) {
        hostname = await getIpInfo(processedRoute.ip);
        apiCallCount++;
        
        // Add small delay to avoid hitting rate limit
        if (apiCallCount < maxApiCalls) {
          await new Promise(resolve => setTimeout(resolve, 1500)); // 1.5 second delay
        }
      }
      
      if (hostname) {
        processedRoute.comment = hostname;
      }
    }
    
    processedRoutes.push(processedRoute);
  }
  
  return processedRoutes;
}

/**
 * Generate new .bat file content with comments
 * @param {Array} routes - Processed routes
 * @returns {string}
 */
export function generateBatContent(routes) {
  let content = '';
  
  for (const route of routes) {
    if (route.comment) {
      content += `route add ${route.ip} mask ${route.mask} ${route.gateway} :: rem ${route.comment}\n`;
    } else {
      content += `route add ${route.ip} mask ${route.mask} ${route.gateway}\n`;
    }
  }
  
  return content;
}

/**
 * Process a .bat file: parse, resolve domains, add comments
 * @param {string} filepath - Path to input file
 * @param {string} originalFilename - Original filename
 * @returns {Promise<{outputPath: string, stats: object}>}
 */
export async function processBatFile(filepath, originalFilename) {
  logger.info(`Processing .bat file: ${originalFilename}`);
  
  // Read file content
  const content = fs.readFileSync(filepath, 'utf8');
  
  // Parse routes
  const routes = parseBatFile(content);
  logger.info(`Parsed ${routes.length} routes from file`);
  
  if (routes.length === 0) {
    throw new Error('No route commands found in file');
  }
  
  // Count initial statistics
  const initialWithComments = routes.filter(r => r.comment).length;
  const initialWithoutComments = routes.filter(r => !r.comment).length;
  
  logger.info(`Initial state: ${initialWithComments} with comments, ${initialWithoutComments} without comments`);
  
  // Add missing comments via reverse DNS and IP info API
  logger.info(`Starting DNS lookups and IP info queries for ${initialWithoutComments} routes`);
  const processedRoutes = await addMissingComments(routes);
  
  // Count final statistics
  const finalWithComments = processedRoutes.filter(r => r.comment).length;
  const finalWithoutComments = processedRoutes.filter(r => !r.comment).length;
  const commentsAdded = finalWithComments - initialWithComments;
  
  logger.info(`Added ${commentsAdded} comments (via reverse DNS and IP info API)`);
  logger.info(`Final state: ${finalWithComments} with comments, ${finalWithoutComments} without comments`);
  
  // Generate new content
  const newContent = generateBatContent(processedRoutes);
  
  // Generate output filename
  const baseName = path.basename(originalFilename, '.bat');
  const outputFilename = `${baseName}_netcrazybot.bat`;
  
  // Ensure output directory exists
  if (!fs.existsSync(config.outputDir)) {
    fs.mkdirSync(config.outputDir, { recursive: true });
  }
  
  // Save to output directory
  const outputPath = path.join(config.outputDir, outputFilename);
  fs.writeFileSync(outputPath, newContent, 'utf8');
  logger.info(`Saved processed file: ${outputPath}`);
  
  const stats = {
    totalRoutes: routes.length,
    initialWithComments,
    initialWithoutComments,
    commentsAdded,
    finalWithComments,
    finalWithoutComments
  };
  
  logger.info(`Processing complete. Stats: ${JSON.stringify(stats)}`);
  
  return {
    outputPath,
    stats
  };
}

// Made with Bob