import dotenv from 'dotenv';
import fs from 'fs';
import yaml from 'js-yaml';
import path from 'path';

dotenv.config();

/**
 * Загрузка конфигурации из config.yaml
 * С fallback на .env если config.yaml не найден
 */
function loadConfig() {
  const configPath = path.join(process.cwd(), 'config.yaml');
  
  // Попытка загрузить config.yaml
  if (fs.existsSync(configPath)) {
    try {
      console.log('📄 Загрузка конфигурации из config.yaml');
      const fileContents = fs.readFileSync(configPath, 'utf8');
      const data = yaml.load(fileContents);
      
      if (data && data.common) {
        console.log('✅ config.yaml успешно загружен');
        return {
          // Telegram Bot Token для AWGBot
          telegramToken: data.common.awg_bot_token || process.env.AWG_BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN,
          
          // Admin IDs
          adminIds: data.common.admin_ids || [],
          
          // Output directory
          outputDir: './output',
          
          // Standalone mode
          standaloneMode: process.env.STANDALONE_MODE === 'true',
          
          // Allow user DNS queries
          allowUserDnsQueries: data.common.allow_user_dns_queries || false,
          
          // Logging
          logLevel: data.common.log_level || 'INFO',
          logFileEnabled: data.common.log_file_enabled !== false,
          logFilePath: data.common.log_file_path || '/app/logs/awgbot.log',
          
          // Source
          configSource: 'config.yaml'
        };
      }
    } catch (e) {
      console.warn('⚠️ Ошибка загрузки config.yaml:', e.message);
      console.log('📄 Fallback на .env');
    }
  } else {
    console.log('📄 config.yaml не найден, используем .env');
  }
  
  // Fallback на .env
  return {
    telegramToken: process.env.AWG_BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN,
    adminIds: process.env.ADMIN_IDS ? process.env.ADMIN_IDS.split(',').map(id => parseInt(id.trim())) : [],
    outputDir: './output',
    standaloneMode: process.env.STANDALONE_MODE === 'true',
    allowUserDnsQueries: process.env.ALLOW_USER_DNS_QUERIES === 'true',
    logLevel: process.env.LOG_LEVEL || 'INFO',
    logFileEnabled: process.env.LOG_FILE_ENABLED !== 'false',
    logFilePath: process.env.LOG_FILE_PATH || '/app/logs/awgbot.log',
    configSource: '.env'
  };
}

export const config = loadConfig();

// Проверка токена только если не standalone режим
if (!config.standaloneMode && !config.telegramToken) {
  console.error('❌ Error: AWG_BOT_TOKEN is not set');
  console.error('💡 Hint: Set AWG_BOT_TOKEN in config.yaml or .env file');
  console.error('💡 Or set STANDALONE_MODE=true for standalone config generation');
  process.exit(1);
}

// Вывод информации о конфигурации
console.log('📋 AWGBot Configuration:');
console.log(`  Source: ${config.configSource}`);
console.log(`  Admin IDs: ${config.adminIds.join(', ')}`);
console.log(`  Allow User DNS: ${config.allowUserDnsQueries}`);
console.log(`  Log Level: ${config.logLevel}`);

// Made with Bob
