import fs from 'fs';
import yaml from 'js-yaml';
import path from 'path';

/**
 * Загрузка конфигурации из config.yaml
 */
function loadConfig() {
  const configPath = path.join(process.cwd(), 'config.yaml');
  
  // Проверка наличия config.yaml
  if (!fs.existsSync(configPath)) {
    throw new Error('❌ config.yaml не найден! Создайте файл config.yaml на основе config.yaml.example');
  }
  
  try {
    console.log('📄 Загрузка конфигурации из config.yaml');
    const fileContents = fs.readFileSync(configPath, 'utf8');
    const data = yaml.load(fileContents);
    
    if (!data || !data.common) {
      throw new Error('config.yaml пустой или содержит некорректные данные');
    }
    
    console.log('✅ config.yaml успешно загружен');
    // Парсинг Cascade-серверов из секции `cascade`
    const cascadeServers = [];
    if (data.cascade) {
      for (const [label, srv] of Object.entries(data.cascade)) {
        if (!srv || !srv.url) continue;
        cascadeServers.push({
          label:    srv.label    || label,
          url:      srv.url,
          token:    srv.token    || null,
          username: srv.username || 'admin',
          password: srv.password || '',
          // version: опционально, принудительно задаёт версию ('v1' или 'v2')
          // если не задан — определяется автоматически по protocol интерфейса
          version:  srv.version  || null,
        });
      }
    }

    return {
      // Telegram Bot Token для AWGBot
      telegramToken: data.common.awg_bot_token || '',

      // Admin IDs
      adminIds: data.common.admin_ids || [],

      // Server Label
      serverLabel: (data.common.server_label || '').toUpperCase(),

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

      // Cascade серверы (если настроены — бот использует Cascade API вместо docker exec)
      cascadeServers,
      cascadeEnabled: cascadeServers.length > 0,

      // Source
      configSource: 'config.yaml'
    };
  } catch (e) {
    console.error('❌ Ошибка загрузки config.yaml:', e.message);
    throw e;
  }
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
