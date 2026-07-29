import fs from 'fs';
import yaml from 'js-yaml';
import path from 'path';

// ── Структура по умолчанию для одной записи cascades ──────────────────────────
// Порядок приоритетов авторизации:
//   1. api_key заполнен              → прямое подключение по Bearer-токену
//   2. api_key пуст, password заполнен → прямое подключение login+password → получаем токен
//   3. api_key и password пусты       → remote-only: интерфейсы видны только через
//                                        autodiscovery другого сервера (/api/remotes)
const CASCADE_ENTRY_DEFAULTS = {
  label:    '',
  url:      '',
  api_key:  '',      // Bearer-токен из Cascade UI → Settings → Tokens (приоритет 1)
  username: 'admin', // логин для авторизации через login+password (приоритет 2)
  password: '',      // пароль для авторизации через login+password (приоритет 2)
  enabled:  true,
  version:  '',
};

/**
 * Дописывает в data.cascades недостающие поля для каждой записи.
 * Если секции cascades нет вообще — создаёт пустую.
 * Возвращает true если файл был изменён и нужно его перезаписать.
 */
function normalizeCascadesSection(data) {
  let changed = false;

  // Если секции нет — создаём пустой объект
  if (!data.cascades) {
    data.cascades = {};
    changed = true;
    console.log('  ℹ️  Добавлена секция cascades: {} в config.yaml');
  }

  // Для каждой существующей записи дописываем недостающие поля
  for (const [key, entry] of Object.entries(data.cascades)) {
    if (!entry || typeof entry !== 'object') continue;
    for (const [field, defaultVal] of Object.entries(CASCADE_ENTRY_DEFAULTS)) {
      if (!(field in entry)) {
        entry[field] = defaultVal;
        changed = true;
        console.log(`  ℹ️  Добавлено поле cascades.${key}.${field}: ${JSON.stringify(defaultVal)}`);
      }
    }
  }

  return changed;
}

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

    // ── Self-healing: дописываем недостающие поля cascades ───────────────────
    // Работает только если в файле нет старой секции `cascade` (обратная совместимость).
    // Если старый формат — не трогаем, чтобы не сломать.
    if (!data.cascade) {
      const needsWrite = normalizeCascadesSection(data);
      if (needsWrite) {
        fs.writeFileSync(configPath, yaml.dump(data, { lineWidth: -1, quotingType: '"' }), 'utf8');
        console.log('  ✅ config.yaml обновлён (добавлены недостающие поля cascades)');
      }
    }

    console.log('✅ config.yaml успешно загружен');

    // ── Парсинг Cascade-серверов ──────────────────────────────────────────────
    // Поддерживаются два формата:
    //
    //   Новый (рекомендуемый) — cascade0 / cascade1 / ... / cascadeN:
    //     cascades:
    //       cascade0:
    //         label: "Основной"
    //         url: "http://localhost:51821"
    //         api_key: "ws_..."      # токен из Cascade UI (Settings → Tokens)
    //         # username: "admin"   # альтернатива api_key — логин/пароль
    //         # password: ""
    //         # enabled: true       # по умолчанию true; false — пропустить
    //
    //   Если api_key (или token) НЕ заполнен — сервер не подключается напрямую,
    //   его интерфейсы будут видны только если он добавлен как Remote в Cascade UI.
    //
    //   Старый формат (обратная совместимость) — секция `cascade`:
    //     cascade:
    //       primary:
    //         url: "..."
    //         token: "..."
    //
    const cascadeServers = [];

    /**
     * Разбирает одну запись сервера и определяет метод авторизации:
     *   'token'    — есть api_key/token → Bearer
     *   'password' — есть password (и нет api_key) → login+password → получаем токен
     *   'none'     — ничего нет → remote-only (через autodiscovery другого сервера)
     */
    function parseCascadeEntry(key, srv) {
      const apiKey   = (srv.api_key || srv.token || '').trim();
      const password = (srv.password || '').trim();
      const username = (srv.username || 'admin').trim();

      let authMethod;
      if (apiKey) {
        authMethod = 'token';       // приоритет 1: api_key
      } else if (password) {
        authMethod = 'password';    // приоритет 2: login + password
      } else {
        authMethod = 'none';        // приоритет 3: remote-only
      }

      return {
        label:      (srv.label || key).trim() || key,
        url:        (srv.url   || '').trim(),
        token:      apiKey    || null,
        username,
        password,
        version:    srv.version || null,
        authMethod,
        // noAuth — обратная совместимость с кодом AWGManagerCascade
        noAuth:     authMethod === 'none',
      };
    }

    // Новый формат: секция `cascades`
    if (data.cascades && typeof data.cascades === 'object') {
      for (const [key, srv] of Object.entries(data.cascades)) {
        if (!srv || typeof srv !== 'object') continue;
        if (!srv.url || !srv.url.trim()) continue;      // url обязателен
        if (srv.enabled === false) continue;            // явно отключён
        cascadeServers.push(parseCascadeEntry(key, srv));
      }
    }

    // Старый формат: секция `cascade` (обратная совместимость)
    if (cascadeServers.length === 0 && data.cascade && typeof data.cascade === 'object') {
      for (const [label, srv] of Object.entries(data.cascade)) {
        if (!srv || typeof srv !== 'object') continue;
        if (!srv.url || !srv.url.trim()) continue;
        cascadeServers.push(parseCascadeEntry(label, srv));
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
if (config.cascadeEnabled) {
  console.log(`  Cascade mode: enabled (${config.cascadeServers.length} server(s))`);
  config.cascadeServers.forEach((s, i) => {
    const authDesc = {
      token:    'api_key (Bearer)',
      password: 'login+password',
      none:     'remote-only (autodiscovery)',
    }[s.authMethod] || s.authMethod;
    console.log(`    [cascade${i}] "${s.label}"  ${s.url}  auth=${authDesc}`);
  });
} else {
  console.log('  Cascade mode: disabled (docker exec)');
}

// Made with Bob
