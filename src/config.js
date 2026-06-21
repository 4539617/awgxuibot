import fs from 'fs';
import yaml from 'js-yaml';
import path from 'path';
import { migrateEnvToYaml } from './migrateEnvToYaml.js';

/**
 * Загрузка конфигурации из config.yaml с автоматической миграцией
 */
function loadConfig() {
  const configPath = path.join(process.cwd(), 'config.yaml');
  const envPath = path.join(process.cwd(), '.env');
  
  // Проверка наличия config.yaml
  if (fs.existsSync(configPath)) {
    try {
      console.log('📄 Загрузка конфигурации из config.yaml');
      const fileContents = fs.readFileSync(configPath, 'utf8');
      const data = yaml.load(fileContents);
      
      if (data && data.common) {
        console.log('✅ config.yaml успешно загружен');
        return {
          // Telegram Bot Token для AWGBot
          telegramToken: data.common.awg_bot_token || '',
          
          // Admin IDs
          adminIds: data.common.admin_ids || [],
          
          // Output directory
          outputDir: './output',
          
          // Standalone mode (из переменной окружения, если нужно)
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
      } else {
        throw new Error('config.yaml пустой или содержит некорректные данные');
      }
    } catch (e) {
      console.error('❌ Ошибка загрузки config.yaml:', e.message);
      throw e;
    }
  }
  
  // config.yaml не найден, проверяем .env для автоматической миграции
  console.log('⚠️ config.yaml не найден');
  
  if (fs.existsSync(envPath)) {
    console.log('🔄 Обнаружен .env файл, запуск автоматической миграции...');
    
    // Выполняем миграцию
    const success = migrateEnvToYaml(envPath, configPath);
    
    if (success) {
      console.log('✅ Миграция завершена успешно!');
      console.log(`📄 Создан файл: ${configPath}`);
      // Рекурсивно загружаем созданный config.yaml
      return loadConfig();
    } else {
      throw new Error(
        '❌ Не удалось выполнить миграцию .env → config.yaml\n' +
        '💡 Проверьте .env файл или создайте config.yaml вручную из config.yaml.example'
      );
    }
  }
  
  // Нет ни config.yaml, ни .env
  throw new Error(
    '❌ Не найден config.yaml\n' +
    '💡 Создайте config.yaml из config.yaml.example\n' +
    '💡 Или поместите .env файл для автоматической миграции'
  );
}

export const config = loadConfig();

// Проверка токена только если не standalone режим
if (!config.standaloneMode && !config.telegramToken) {
  console.error('❌ Error: AWG_BOT_TOKEN is not set in config.yaml');
  console.error('💡 Hint: Set awg_bot_token in config.yaml');
  console.error('💡 Or set STANDALONE_MODE=true for standalone config generation');
  process.exit(1);
}

// Вывод информации о конфигурации
console.log('\n📋 AWGBot Configuration:');
console.log(`  Source: ${config.configSource}`);
console.log(`  Admin IDs: ${config.adminIds.length > 0 ? config.adminIds.join(', ') : 'не установлены'}`);
console.log(`  Allow User DNS: ${config.allowUserDnsQueries}`);
console.log(`  Log Level: ${config.logLevel}`);
console.log(`  Standalone Mode: ${config.standaloneMode}`);

// Made with Bob
