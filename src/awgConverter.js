import fs from 'fs';
import path from 'path';
import { config } from './config.js';
import { logger } from './logger.js';
import {
  detectAwgVersion,
  validateAwgConfig,
  convertHParameterV2toV1
} from './awg/validator.js';

/**
 * Parse AWG config file
 * @param {string} content - File content
 * @returns {Object} Parsed config
 */
export function parseAwgConfig(content) {
  const lines = content.split('\n');
  const config = {
    interface: {},
    peer: {}
  };
  
  let currentSection = null;
  
  for (const line of lines) {
    const trimmed = line.trim();
    
    // Skip empty lines and comments
    if (!trimmed || trimmed.startsWith('#')) {
      continue;
    }
    
    // Check for section headers
    if (trimmed === '[Interface]') {
      currentSection = 'interface';
      continue;
    } else if (trimmed === '[Peer]') {
      currentSection = 'peer';
      continue;
    }
    
    // Parse key-value pairs
    const match = trimmed.match(/^([^=]+)=(.*)$/);
    if (match && currentSection) {
      const key = match[1].trim();
      const value = match[2].trim();
      config[currentSection][key] = value;
    }
  }
  
  return config;
}

// Используем функции из модуля validator
// detectAwgVersion и convertHParameterV2toV1 импортированы выше

/**
 * Convert S parameters from v2 to v1
 * In v2 there are S1-S4, in v1 only S1-S2
 * @param {Object} interfaceConfig - Interface section
 * @returns {Object} Converted S parameters
 */
export function convertSParameters(interfaceConfig) {
  const result = {};
  
  // Keep S1 and S2
  if (interfaceConfig.S1) result.S1 = interfaceConfig.S1;
  if (interfaceConfig.S2) result.S2 = interfaceConfig.S2;
  
  // S3 and S4 are not used in v1, so we skip them
  
  return result;
}

/**
 * Convert AWG config from v2 to v1
 * @param {Object} config - Parsed v2 config
 * @returns {Object} Converted v1 config
 */
export function convertAwgV2toV1(config) {
  const v1Config = {
    interface: { ...config.interface },
    peer: { ...config.peer }
  };
  
  // Convert H parameters (remove ranges, keep first value)
  if (v1Config.interface.H1) {
    v1Config.interface.H1 = convertHParameterV2toV1(v1Config.interface.H1);
  }
  if (v1Config.interface.H2) {
    v1Config.interface.H2 = convertHParameterV2toV1(v1Config.interface.H2);
  }
  if (v1Config.interface.H3) {
    v1Config.interface.H3 = convertHParameterV2toV1(v1Config.interface.H3);
  }
  if (v1Config.interface.H4) {
    v1Config.interface.H4 = convertHParameterV2toV1(v1Config.interface.H4);
  }
  
  // Remove S3 and S4 (not used in v1)
  delete v1Config.interface.S3;
  delete v1Config.interface.S4;
  
  // Remove I parameters (CPS concealment, not supported in v1)
  delete v1Config.interface.I1;
  delete v1Config.interface.I2;
  delete v1Config.interface.I3;
  delete v1Config.interface.I4;
  delete v1Config.interface.I5;
  
  return v1Config;
}

/**
 * Generate AWG config file content
 * @param {Object} config - Config object
 * @returns {string} Config file content
 */
export function generateAwgConfig(config) {
  let content = '[Interface]\n';
  
  // Interface section - maintain order
  const interfaceOrder = [
    'Address', 'DNS', 'PrivateKey',
    'Jc', 'Jmin', 'Jmax',
    'S1', 'S2',
    'H1', 'H2', 'H3', 'H4',
    'I1', 'I2', 'I3', 'I4', 'I5'
  ];
  
  for (const key of interfaceOrder) {
    if (config.interface[key] !== undefined) {
      content += `${key} = ${config.interface[key]}\n`;
    }
  }
  
  content += '\n[Peer]\n';
  
  // Peer section - maintain order
  const peerOrder = [
    'PublicKey', 'PresharedKey', 'AllowedIPs', 'Endpoint', 'PersistentKeepalive'
  ];
  
  for (const key of peerOrder) {
    if (config.peer[key] !== undefined) {
      content += `${key} = ${config.peer[key]}\n`;
    }
  }
  
  return content;
}

/**
 * Process AWG config file: detect version and convert if needed
 * @param {string} filepath - Path to input file
 * @param {string} originalFilename - Original filename
 * @returns {Promise<{outputPath: string, version: number, converted: boolean}>}
 */
export async function processAwgConfig(filepath, originalFilename) {
  logger.info(`Processing AWG config file: ${originalFilename}`);
  
  // Read file content
  const content = fs.readFileSync(filepath, 'utf8');
  
  // Parse config
  const parsedConfig = parseAwgConfig(content);
  
  // Detect version and validate
  const validation = validateAwgConfig(parsedConfig);
  const version = validation.version;
  
  if (!validation.valid) {
    logger.warn(`⚠️ Конфиг содержит ошибки валидации:`);
    validation.errors.forEach(err => logger.warn(`   - ${err}`));
  } else {
    logger.info(`✅ AWG ${version} конфиг валиден`);
  }
  
  let outputConfig = parsedConfig;
  let converted = false;
  
  // Convert if v2
  if (version === 2) {
    logger.info('Converting from v2 to v1...');
    outputConfig = convertAwgV2toV1(parsedConfig);
    converted = true;
    
    // Log conversion details
    logger.info('Conversion details:');
    if (parsedConfig.interface.H1) {
      logger.info(`  H1: ${parsedConfig.interface.H1} -> ${outputConfig.interface.H1}`);
    }
    if (parsedConfig.interface.H2) {
      logger.info(`  H2: ${parsedConfig.interface.H2} -> ${outputConfig.interface.H2}`);
    }
    if (parsedConfig.interface.H3) {
      logger.info(`  H3: ${parsedConfig.interface.H3} -> ${outputConfig.interface.H3}`);
    }
    if (parsedConfig.interface.H4) {
      logger.info(`  H4: ${parsedConfig.interface.H4} -> ${outputConfig.interface.H4}`);
    }
    if (parsedConfig.interface.S3 || parsedConfig.interface.S4) {
      logger.info(`  Removed S3 and S4 parameters (not used in v1)`);
    }
  } else {
    logger.info('File is already v1, no conversion needed');
  }
  
  // Generate new content
  const newContent = generateAwgConfig(outputConfig);
  
  // Generate output filename
  const baseName = path.basename(originalFilename, '.conf');
  const outputFilename = converted 
    ? `${baseName}_v1.conf`
    : `${baseName}_checked.conf`;
  
  // Ensure output directory exists
  if (!fs.existsSync(config.outputDir)) {
    fs.mkdirSync(config.outputDir, { recursive: true });
  }
  
  // Save to output directory
  const outputPath = path.join(config.outputDir, outputFilename);
  fs.writeFileSync(outputPath, newContent, 'utf8');
  logger.info(`Saved processed file: ${outputPath}`);
  
  return {
    outputPath,
    version,
    converted
  };
}