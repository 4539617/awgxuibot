#!/usr/bin/env node
/**
 * migrate-to-cascade.js
 *
 * Миграция существующих AWG-контейнеров Amnezia в Cascade.
 *
 * Что делает:
 *   1. Читает cascade.url / cascade.token из config.yaml (автоматически)
 *   2. Читает серверные .conf из работающих Amnezia-контейнеров через docker exec
 *   3. Читает серверные ключи (privateKey, publicKey, presharedKey)
 *   4. Строит серверный .conf с PrivateKey для Cascade
 *   5. Импортирует через POST /api/tunnel-interfaces/import-conf-server
 *      (все пиры переносятся с теми же ключами — существующие клиенты не требуют переконфигурации)
 *   6. Загружает клиентские .conf из ./output/ через import-client-configs
 *      (восстанавливает приватные ключи → включает QR-коды и скачивание конфигов)
 *   7. Записывает token в config.yaml если его ещё нет
 *
 * Использование:
 *   node migrate-to-cascade.js                          # авточтение из config.yaml
 *   node migrate-to-cascade.js --dry-run                # показать план без выполнения
 *   node migrate-to-cascade.js --v1-only                # только AWG v1
 *   node migrate-to-cascade.js --v2-only                # только AWG v2
 *   node migrate-to-cascade.js --cascade-url http://localhost:51821 --token TOKEN
 *   node migrate-to-cascade.js --username admin --password pass
 *
 * Флаги:
 *   --cascade-url  URL Cascade сервера (override config.yaml)
 *   --token        API-токен Cascade (override config.yaml)
 *   --username     Логин Cascade (default: admin)
 *   --password     Пароль Cascade
 *   --dry-run      Показать что будет импортировано, не делать запросов
 *   --v1-only      Мигрировать только AWG v1 (amnezia-awg)
 *   --v2-only      Мигрировать только AWG v2 (amnezia-awg2)
 *   --listen-port  Порт для v1 в Cascade (default: тот же что на контейнере)
 *   --listen-port2 Порт для v2 в Cascade (default: тот же что на контейнере)
 */

import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs';
import path from 'path';

const execAsync = promisify(exec);

// ── CLI args ───────────────────────────────────────────────────────────────────

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    cascadeUrl:  '',
    token:       '',
    username:    'admin',
    password:    '',
    dryRun:      false,
    v1Only:      false,
    v2Only:      false,
    listenPort:  0,
    listenPort2: 0,
  };
  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--cascade-url':  opts.cascadeUrl  = args[++i]; break;
      case '--token':        opts.token       = args[++i]; break;
      case '--username':     opts.username    = args[++i]; break;
      case '--password':     opts.password    = args[++i]; break;
      case '--dry-run':      opts.dryRun      = true;      break;
      case '--v1-only':      opts.v1Only      = true;      break;
      case '--v2-only':      opts.v2Only      = true;      break;
      case '--listen-port':  opts.listenPort  = parseInt(args[++i]); break;
      case '--listen-port2': opts.listenPort2 = parseInt(args[++i]); break;
    }
  }
  return opts;
}

// ── Config.yaml reader ─────────────────────────────────────────────────────────

/**
 * Читает config.yaml и возвращает секцию cascade.
 * Минимальный YAML-парсер для cascade.primary.url / .token / .username / .password
 */
function readConfigYaml() {
  const configPath = path.join(process.cwd(), 'config.yaml');
  if (!fs.existsSync(configPath)) return null;

  try {
    const content = fs.readFileSync(configPath, 'utf8');
    const lines   = content.split('\n');

    const cascade = {};
    let inCascade = false;
    let currentServer = null;
    let currentIndent = 0;

    for (const rawLine of lines) {
      const stripped = rawLine.trimEnd();
      if (!stripped || stripped.startsWith('#')) continue;

      const indent = rawLine.length - rawLine.trimStart().length;
      const line   = stripped.trim();

      if (line === 'cascade:') {
        inCascade = true;
        currentIndent = indent;
        continue;
      }

      if (!inCascade) continue;

      // Вышли из секции cascade (следующая ключ на том же уровне или выше)
      if (indent <= currentIndent && line !== 'cascade:' && /^\w/.test(line) && !line.startsWith('-')) {
        inCascade = false;
        continue;
      }

      // Уровень вложенности = 2 (имя сервера)
      if (indent === currentIndent + 2 && line.endsWith(':')) {
        currentServer = line.slice(0, -1);
        cascade[currentServer] = {};
        continue;
      }

      // Уровень вложенности = 4 (поля сервера)
      if (indent === currentIndent + 4 && currentServer) {
        const m = line.match(/^(\w+):\s*"?([^"#]*)"?/);
        if (m) {
          cascade[currentServer][m[1]] = m[2].trim();
        }
      }
    }

    return Object.keys(cascade).length > 0 ? cascade : null;
  } catch {
    return null;
  }
}

/**
 * Обновляет или добавляет значение в config.yaml.
 * Простая замена строки — только для случая cascade.primary.token.
 */
function writeTokenToConfig(token) {
  const configPath = path.join(process.cwd(), 'config.yaml');
  if (!fs.existsSync(configPath)) return false;

  try {
    let content = fs.readFileSync(configPath, 'utf8');

    // Если уже есть token: "..." — заменяем
    if (/token:\s*"[^"]*"/.test(content) || /token:\s*[^\s#\n]+/.test(content)) {
      content = content
        .replace(/(^\s*token:\s*)"[^"]*"/m, `$1"${token}"`)
        .replace(/(^\s*token:\s*)(?!")\S+/m, `$1"${token}"`);
    }

    fs.writeFileSync(configPath, content, 'utf8');
    return true;
  } catch {
    return false;
  }
}

// ── Docker helpers ─────────────────────────────────────────────────────────────

async function containerExists(name) {
  try {
    const { stdout } = await execAsync(
      `docker ps -a --filter "name=^${name}$" --format "{{.Names}}"`
    );
    return stdout.trim() === name;
  } catch { return false; }
}

async function containerRunning(name) {
  try {
    const { stdout } = await execAsync(
      `docker ps --filter "name=^${name}$" --format "{{.Names}}"`
    );
    return stdout.trim() === name;
  } catch { return false; }
}

async function dockerRead(containerName, filePath) {
  const { stdout } = await execAsync(
    `docker exec ${containerName} cat ${filePath}`
  );
  return stdout.trim();
}

async function dockerReadKey(containerName, keyPaths, filename) {
  for (const keyPath of keyPaths) {
    try {
      return await dockerRead(containerName, `${keyPath}/${filename}`);
    } catch { /* try next */ }
  }
  return null;
}

// ── Config reader ─────────────────────────────────────────────────────────────

/**
 * Читает clientsTable.json из контейнера Amnezia и возвращает Map<publicKey, name>.
 * Amnezia хранит имена клиентов отдельно от .conf — в clientsTable.json.
 * Формат: [{ userData: { clientId, userName }, config: "<client .conf>" }, ...]
 */
async function readClientNames(containerName, confDir) {
  try {
    const raw = await dockerRead(containerName, `${confDir}/clientsTable.json`);
    const table = JSON.parse(raw);
    const map = new Map();
    for (const entry of table) {
      const name = entry?.userData?.userName || entry?.userData?.clientId || '';
      if (!name) continue;
      // Извлекаем PublicKey из клиентского .conf в поле config
      const conf = entry?.config || '';
      const m = conf.match(/^\s*PublicKey\s*=\s*(\S+)/m);
      if (m) map.set(m[1].trim(), name);
    }
    return map;
  } catch {
    return new Map(); // файл не найден или неверный формат — не критично
  }
}

/**
 * Вставляет "# Name = <имя>" перед каждым [Peer] в серверном .conf,
 * используя маппинг publicKey → name из clientsTable.json.
 * Cascade парсит этот комментарий и использует как имя пира.
 */
function injectPeerNames(confContent, nameMap) {
  if (!nameMap || nameMap.size === 0) return confContent;

  const lines  = confContent.split('\n');
  const result = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (/^\[Peer\]/i.test(line.trim())) {
      // Ищем PublicKey в следующих строках этой [Peer] секции
      let pubKey = null;
      for (let j = i + 1; j < lines.length; j++) {
        const l = lines[j].trim();
        if (l.startsWith('[')) break; // следующая секция
        const m = l.match(/^PublicKey\s*=\s*(\S+)/i);
        if (m) { pubKey = m[1]; break; }
      }
      const name = pubKey && nameMap.get(pubKey);
      if (name) result.push(`# Name = ${name}`);
    }

    result.push(line);
    i++;
  }

  return result.join('\n');
}

async function readContainerConfig(containerName) {
  const confPaths = [
    '/opt/amnezia/amneziawg',
    '/opt/amnezia/awg',
    '/etc/amnezia/amneziawg',
  ];
  const confFiles = ['awg0.conf', 'wg0.conf'];

  let confContent = null;
  let confPath    = null;
  let confDir     = null;

  for (const dir of confPaths) {
    for (const file of confFiles) {
      try {
        confContent = await dockerRead(containerName, `${dir}/${file}`);
        confPath = `${dir}/${file}`;
        confDir  = dir;
        break;
      } catch { /* try next */ }
    }
    if (confContent) break;
  }

  if (!confContent) {
    throw new Error(`Конфиг не найден в контейнере ${containerName}`);
  }

  const keyPaths = [
    '/opt/amnezia/amneziawg',
    '/opt/amnezia/awg',
    '/etc/amnezia/amneziawg',
  ];

  const privateKey   = await dockerReadKey(containerName, keyPaths, 'wireguard_server_private_key.key');
  const publicKey    = await dockerReadKey(containerName, keyPaths, 'wireguard_server_public_key.key');
  const presharedKey = await dockerReadKey(containerName, keyPaths, 'wireguard_psk.key');

  // Имена клиентов берём из комментариев конфига (# Peer: <name> | ...)
  // или из clientsTable.json если конфиг без комментариев (официальное приложение Amnezia).
  const peerComments = (confContent.match(/^#\s*Peer:/gim) || []).length;
  if (peerComments > 0) {
    console.log(`   📛 Имена клиентов найдены в комментариях конфига: ${peerComments}`);
  } else {
    // Fallback: clientsTable.json (официальное приложение Amnezia)
    const nameMap = await readClientNames(containerName, confDir);
    if (nameMap.size > 0) {
      confContent = injectPeerNames(confContent, nameMap);
      console.log(`   📛 Имена клиентов из clientsTable.json: ${nameMap.size}`);
    } else {
      console.log(`   ℹ️  Имена клиентов не найдены — будут использованы peer-1, peer-2, ...`);
    }
  }

  return { confContent, confPath, privateKey, publicKey, presharedKey };
}

// ── Config builder ─────────────────────────────────────────────────────────────

/**
 * Строим серверный .conf для импорта в Cascade.
 * Cascade принимает полный серверный conf с PrivateKey + все [Peer] секции.
 * Если PrivateKey уже есть в конфиге — возвращаем как есть.
 * Если нет (хранится в .key файле) — вставляем после [Interface].
 */
function buildServerConf(confContent, privateKey) {
  if (/^\s*PrivateKey\s*=/m.test(confContent)) {
    return confContent; // уже есть
  }

  // Вставляем PrivateKey сразу после строки [Interface]
  const lines  = confContent.split('\n');
  const result = [];
  let inserted = false;

  for (const line of lines) {
    result.push(line);
    if (!inserted && /^\[Interface\]/i.test(line.trim())) {
      result.push(`PrivateKey = ${privateKey}`);
      inserted = true;
    }
  }

  return result.join('\n');
}

// ── Cascade API ───────────────────────────────────────────────────────────────

async function cascadeLogin(baseUrl, username, password) {
  const res = await fetch(`${baseUrl}/api/session`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ username, password }),
  });
  const body = await res.json();
  if (!res.ok || !body.authenticated) {
    throw new Error(`Cascade login failed: ${body.message || body.error || res.status}`);
  }
  // Получаем session cookie для последующего запроса createToken
  const setCookie = res.headers.get('set-cookie') || '';
  return setCookie ? setCookie.split(';')[0].trim() : '';
}

async function cascadeCreateToken(baseUrl, cookie) {
  const headers = { 'Content-Type': 'application/json' };
  if (cookie) headers['Cookie'] = cookie;

  const res = await fetch(`${baseUrl}/api/tokens`, {
    method: 'POST',
    headers,
    body:   JSON.stringify({ name: `migrate-${Date.now()}` }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(`Create token failed: ${body.message || body.error || res.status}`);
  // API возвращает { token: {id,name,...}, raw_token: "ws_..." }
  // raw_token — строка с реальным токеном (показывается только один раз)
  return body.raw_token || body.token?.id;
}

async function cascadeLogout(baseUrl, cookie) {
  try {
    const headers = {};
    if (cookie) headers['Cookie'] = cookie;
    await fetch(`${baseUrl}/api/session`, { method: 'DELETE', headers });
  } catch { /* non-critical */ }
}

async function cascadeImportServer(baseUrl, token, name, confContent, opts = {}) {
  // import-conf-server читает ListenPort из секции [Interface] самого .conf.
  // Если нужно переопределить порт — патчим строку ListenPort в конфиге.
  let conf = confContent;
  if (opts.listenPort) {
    conf = conf.replace(/^ListenPort\s*=\s*\d+/m, `ListenPort = ${opts.listenPort}`);
  }
  const body = { name, conf };

  const res = await fetch(`${baseUrl}/api/tunnel-interfaces/import-conf-server`, {
    method:  'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
  const result = await res.json();
  if (!res.ok) {
    throw new Error(`import-conf-server failed: ${result.message || result.error || JSON.stringify(result)}`);
  }
  return result;
}

/**
 * Загружает клиентские .conf файлы из outputDir в Cascade через import-client-configs.
 * Это восстанавливает privateKey у пиров, которые были мигрированы без него,
 * и делает QR-код и скачивание конфига доступными.
 *
 * @param {string} baseUrl
 * @param {string} token
 * @param {string} ifaceId   - ID интерфейса в Cascade
 * @param {string} outputDir - папка с клиентскими .conf файлами
 * @param {string} versionTag - 'v1' | 'v2' — фильтр по имени файла
 */
async function cascadeImportClientConfigs(baseUrl, token, ifaceId, outputDir, versionTag) {
  if (!fs.existsSync(outputDir)) {
    console.log(`   ℹ️  Папка output не найдена (${outputDir}), пропускаем импорт клиентских ключей`);
    return;
  }

  // Ищем .conf файлы соответствующей версии (AWGv1 или AWGv2), исключаем _RESENT
  const tag = versionTag === 'v2' ? 'AWGv2' : 'AWGv1';
  const files = fs.readdirSync(outputDir)
    .filter(f => f.endsWith('.conf') && f.includes(tag) && !f.includes('_RESENT'))
    .map(f => path.join(outputDir, f));

  if (files.length === 0) {
    console.log(`   ℹ️  Клиентские конфиги ${tag} в ${outputDir} не найдены`);
    return;
  }

  console.log(`   📂 Найдено клиентских конфигов ${tag}: ${files.length}`);

  // Формируем multipart/form-data вручную (Node.js fetch поддерживает FormData)
  const FormData = (await import('node:buffer')).Blob ? globalThis.FormData : null;
  // Node 18+ имеет встроенный FormData
  const form = new globalThis.FormData();
  for (const f of files) {
    const content = fs.readFileSync(f);
    const blob = new Blob([content], { type: 'text/plain' });
    form.append('configs', blob, path.basename(f));
  }

  const res = await fetch(
    `${baseUrl}/api/tunnel-interfaces/${ifaceId}/peers/import-client-configs`,
    {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` },
      body: form,
    }
  );

  const result = await res.json();
  if (!res.ok) {
    console.warn(`   ⚠️  import-client-configs вернул ошибку: ${result.message || result.error}`);
    return;
  }

  console.log(`   ✅ Приватные ключи восстановлены: совпало ${result.matched} из ${files.length}`);
  if (result.unmatched?.length > 0) {
    console.log(`   ℹ️  Не совпало: ${result.unmatched.join(', ')}`);
  }
}

async function cascadeHealth(baseUrl) {
  try {
    const res = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(5000) });
    return res.ok;
  } catch { return false; }
}

// ── Main ───────────────────────────────────────────────────────────────────────

async function main() {
  const opts = parseArgs();

  // ── Читаем config.yaml если URL/token не переданы явно ────────────────────
  const yamlCascade = readConfigYaml();
  const primaryConf = yamlCascade?.primary || Object.values(yamlCascade || {})[0] || {};

  if (!opts.cascadeUrl) {
    opts.cascadeUrl = primaryConf.url || '';
  }
  if (!opts.token) {
    opts.token = primaryConf.token || '';
  }
  if (!opts.password) {
    opts.password = primaryConf.password || '';
  }
  if (primaryConf.username) {
    opts.username = primaryConf.username;
  }

  if (!opts.cascadeUrl) {
    console.error('❌ Не указан URL Cascade-сервера.');
    console.error('   Вариант 1: Задайте в config.yaml: cascade.primary.url: "http://localhost:51821"');
    console.error('   Вариант 2: Передайте аргумент: --cascade-url http://localhost:51821');
    process.exit(1);
  }

  const baseUrl = opts.cascadeUrl.replace(/\/$/, '');

  // ── Проверяем доступность Cascade ─────────────────────────────────────────
  console.log(`\n🔍 Проверяем доступность Cascade: ${baseUrl}`);
  const alive = await cascadeHealth(baseUrl);
  if (!alive) {
    console.error(`❌ Cascade недоступен по адресу ${baseUrl}`);
    console.error('   Убедись что Cascade запущен:');
    console.error('   docker compose -f docker-compose.cascade.yml up -d');
    process.exit(1);
  }
  console.log('✅ Cascade доступен');

  // ── Получаем токен ─────────────────────────────────────────────────────────
  let token = opts.token;
  if (!token) {
    if (!opts.password) {
      console.error('❌ Не указан ни токен, ни пароль для получения токена.');
      console.error('   Вариант 1: Задайте в config.yaml: cascade.primary.token: "ws_..."');
      console.error('   Вариант 2: --token TOKEN');
      console.error('   Вариант 3: --password YOURPASSWORD');
      process.exit(1);
    }
    console.log(`\n🔑 Получаем токен через login (${opts.username})...`);
    const cookie = await cascadeLogin(baseUrl, opts.username, opts.password);
    token = await cascadeCreateToken(baseUrl, cookie);
    await cascadeLogout(baseUrl, cookie);
    console.log('✅ Токен получен');

    // Сохраняем токен в config.yaml для последующего использования
    if (writeTokenToConfig(token)) {
      console.log(`💾 Токен записан в config.yaml`);
    }
  }

  // ── Контейнеры для миграции ────────────────────────────────────────────────
  const targets = [];
  if (!opts.v2Only) targets.push({ name: 'amnezia-awg',  label: 'AWG v1', listenPort: opts.listenPort });
  if (!opts.v1Only) targets.push({ name: 'amnezia-awg2', label: 'AWG v2', listenPort: opts.listenPort2 });

  let migrated = 0;
  let skipped  = 0;

  for (const target of targets) {
    console.log(`\n──────────────────────────────────────────`);
    console.log(`📦 Контейнер: ${target.name} (${target.label})`);

    if (!await containerExists(target.name)) {
      console.log(`⏭  Контейнер ${target.name} не найден, пропускаем`);
      skipped++;
      continue;
    }

    if (!await containerRunning(target.name)) {
      console.error(`❌ Контейнер ${target.name} не запущен`);
      console.error(`   Запусти: docker start ${target.name}`);
      skipped++;
      continue;
    }

    console.log(`✅ Контейнер запущен`);

    // Читаем конфиг и ключи
    let containerData;
    try {
      console.log('📄 Читаем конфиг и ключи...');
      containerData = await readContainerConfig(target.name);
    } catch (err) {
      console.error(`❌ Не удалось прочитать конфиг: ${err.message}`);
      skipped++;
      continue;
    }

    const { confContent, confPath, privateKey, publicKey, presharedKey } = containerData;

    if (!privateKey) {
      console.error('❌ Серверный приватный ключ не найден (wireguard_server_private_key.key)');
      skipped++;
      continue;
    }

    // Подсчитываем пиры
    const peerCount = (confContent.match(/^\[Peer\]/gm) || []).length;
    const portMatch = confContent.match(/ListenPort\s*=\s*(\d+)/);
    const port      = portMatch ? portMatch[1] : 'unknown';
    const isAWGv2   = /^\s*(Jc|Jmin|Jmax|S1|S2|S3|S4|H1|H2|H3|H4|I1)\s*=/m.test(confContent);

    console.log(`📋 Найдено пиров: ${peerCount}`);
    console.log(`🔌 Порт: ${port}`);
    console.log(`🔐 Протокол: ${isAWGv2 ? 'AmneziaWG 2.0' : 'WireGuard 1.0'}`);
    console.log(`📁 Конфиг: ${confPath}`);

    if (opts.dryRun) {
      console.log('\n🔶 DRY RUN — реальный импорт не выполняется');
      migrated++;
      continue;
    }

    // Строим серверный .conf с PrivateKey
    const serverConf  = buildServerConf(confContent, privateKey);
    const importName  = `${target.label.replace(' ', '-')}-migrated`;
    console.log(`\n⬆️  Импортируем в Cascade как "${importName}"...`);

    let result;
    try {
      result = await cascadeImportServer(baseUrl, token, importName, serverConf, {
        listenPort: target.listenPort || parseInt(port) || undefined,
      });
    } catch (err) {
      console.error(`❌ Ошибка импорта: ${err.message}`);
      if (err.message.includes('port') && err.message.includes('already')) {
        const alt = target.name.includes('2') ? '--listen-port2' : '--listen-port';
        console.error(`   Подсказка: порт ${port} уже используется в Cascade.`);
        console.error(`   Укажи другой: ${alt} XXXX`);
      }
      skipped++;
      continue;
    }

    const created  = result.peersCreated  || 0;
    const failed   = (result.peersFailed  || []).length;
    const started  = result.started;
    const ifaceId  = result.interface?.id || '?';

    console.log(`✅ Импортировано успешно!`);
    console.log(`   Interface ID: ${ifaceId}`);
    console.log(`   Пиров создано: ${created}${failed ? ` (ошибок: ${failed})` : ''}`);
    console.log(`   Интерфейс запущен: ${started ? 'да' : 'нет (запусти вручную в UI)'}`);

    if (result.startError) {
      console.warn(`   ⚠️  Ошибка запуска: ${result.startError}`);
    }
    if (failed > 0) {
      console.warn(`   ⚠️  Не удалось перенести: ${result.peersFailed.join(', ')}`);
    }

    // Восстанавливаем приватные ключи из output/ → включает QR и скачивание конфига
    if (ifaceId !== '?') {
      const outputDir = path.join(process.cwd(), 'output');
      const versionTag = target.name.includes('2') ? 'v2' : 'v1';
      console.log(`\n🔑 Восстанавливаем приватные ключи клиентов (QR-коды)...`);
      await cascadeImportClientConfigs(baseUrl, token, ifaceId, outputDir, versionTag);
    }

    migrated++;
  }

  // ── Итог ───────────────────────────────────────────────────────────────────
  console.log(`\n══════════════════════════════════════════`);
  if (opts.dryRun) {
    console.log(`🔶 DRY RUN завершён. Найдено для миграции: ${migrated}`);
    console.log(`   Запусти без --dry-run для реальной миграции`);
  } else {
    console.log(`✅ Миграция завершена: перенесено ${migrated}, пропущено ${skipped}`);
    if (migrated > 0) {
      console.log(`\n📌 Следующие шаги:`);
      console.log(`   1. Открой Cascade UI: ${baseUrl}`);
      console.log(`   2. Проверь что интерфейсы запущены (зелёный статус)`);
      console.log(`   3. ВАЖНО: останови старые Amnezia-контейнеры (конфликт UDP-портов!):`);
      console.log(`      docker stop amnezia-awg amnezia-awg2`);
      console.log(`   4. Убедись что в config.yaml есть секция cascade:`);
      console.log(`      cascade:`);
      console.log(`        primary:`);
      console.log(`          url: "${baseUrl}"`);
      console.log(`          token: "${token}"`);
      console.log(`   5. Перезапусти бот:`);
      console.log(`      docker compose -f docker-compose.awgbot.yml restart`);
      console.log(`\n   💡 Multi-VPS: добавь удалённые серверы в Cascade UI:`);
      console.log(`      Settings → Remote Servers → Add`);
      console.log(`      Бот автоматически увидит все интерфейсы через primary.`);
    }
  }
}

main().catch(err => {
  console.error('\n💥 Критическая ошибка:', err.message);
  process.exit(1);
});
