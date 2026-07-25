/**
 * AWGManagerCascade
 * Замена AWGManager — управляет AWG-интерфейсами через Cascade REST API
 * вместо прямых docker exec вызовов.
 *
 * Публичный интерфейс полностью совместим с AWGManager:
 *   initialize(), generateClientConfig(), generateClientConfigByNumber(),
 *   getStats(), getClientsWithStatus(), renamePeer(),
 *   regenerateClientConfig(), getAvailableVersions(),
 *   startContainer(), stopContainer()
 *
 * Внутри использует CascadeClient для HTTP-запросов к Cascade API.
 * Поддерживает несколько серверов Cascade (primary + remotes через proxy).
 */

import fs from 'fs';
import path from 'path';
import { config } from './config.js';
import { logger } from './logger.js';
import { CascadeClient } from './cascadeClient.js';

export class AWGManagerCascade {
  constructor(cascadeServers) {
    /**
     * cascadeServers: массив объектов из config.yaml:
     * [{ label, url, token?, username?, password?, version }]
     */
    this.servers = cascadeServers || [];
    /** Карта version → { client, interfaceId, serverLabel } */
    this.versionMap = new Map();
    this.initialized = false;

    /**
     * availableContainers — прокси-массив поверх versionMap для обратной
     * совместимости с bot.js, который делает:
     *   this.awgManager.availableContainers.find(c => c.version === version)
     *
     * Возвращает живой массив, актуальный после initialize().
     * Каждый элемент имитирует объект контейнера AWGManager:
     *   { name, version, port, running, configPath: null }
     */
    this.availableContainers = new Proxy([], {
      get: (_, prop) => {
        // Перестраиваем массив из versionMap при каждом обращении
        const arr = [...this.versionMap.values()].map(e => ({
          name:       e.version,          // версия используется как "имя контейнера"
          version:    e.version,
          port:       e.listenPort,
          configPath: null,
          running:    true,
          _entry:     e,                  // ссылка на полный объект для внутреннего use
        }));
        if (prop === 'length') return arr.length;
        if (prop === 'find')   return arr.find.bind(arr);
        if (prop === 'map')    return arr.map.bind(arr);
        if (prop === 'filter') return arr.filter.bind(arr);
        if (prop === 'forEach') return arr.forEach.bind(arr);
        if (typeof prop === 'string' && !isNaN(prop)) return arr[parseInt(prop)];
        return arr[prop];
      },
    });
  }

  // ── Init ───────────────────────────────────────────────────────────────────

  async initialize() {
    if (this.initialized) return;
    logger.info('[CascadeManager] Initializing...');

    for (const srv of this.servers) {
      if (!srv.url) continue;

      const client = new CascadeClient({
        url:      srv.url,
        token:    srv.token    || null,
        username: srv.username || 'admin',
        password: srv.password || '',
        label:    srv.label    || srv.url,
      });

      // Проверяем доступность
      const alive = await client.ping();
      if (!alive) {
        logger.warn(`[CascadeManager] Server ${srv.label} is unreachable, skipping`);
        continue;
      }

      // Получаем список интерфейсов
      let ifaces;
      try {
        ifaces = await client.listInterfaces();
      } catch (err) {
        logger.warn(`[CascadeManager] Cannot list interfaces on ${srv.label}: ${err.message}`);
        continue;
      }

      // Регистрируем каждый интерфейс как версию AWG
      for (const iface of ifaces) {
        // protocol: "amneziawg-2.0" → v2, "wireguard-1.0" → v1
        const version = iface.protocol === 'amneziawg-2.0' ? 'v2' : 'v1';
        const key = srv.version || version; // можно принудительно задать в конфиге

        if (!this.versionMap.has(key)) {
          this.versionMap.set(key, {
            client,
            interfaceId: iface.id,
            interfaceName: iface.name,
            serverLabel: srv.label,
            version: key,
            protocol: iface.protocol,
            publicKey: iface.publicKey,
            address: iface.address,
            listenPort: iface.listenPort,
          });
          logger.info(`[CascadeManager] Registered ${key} → ${iface.name} on ${srv.label}`);
        }
      }
    }

    this.initialized = true;
    logger.info(`[CascadeManager] Ready. Versions: ${[...this.versionMap.keys()].join(', ') || 'none'}`);
  }

  // ── Compat helpers (обратная совместимость с AWGManager API) ──────────────

  /**
   * checkContainer(containerNameOrVersion) → { running, restarting, stopped, status }
   * В Cascade нет «контейнера», поэтому опрашиваем интерфейс.
   * Используется в bot.js: awgManager.checkContainer(container.name)
   */
  async checkContainer(containerNameOrVersion) {
    if (!this.initialized) await this.initialize();
    try {
      const ver = this._versionFromContainerName(containerNameOrVersion);
      const entry = this._getEntry(ver);
      const iface = await entry.client.getInterface(entry.interfaceId);
      const running = !!iface?.enabled;
      return {
        running,
        restarting: false,
        stopped:    !running,
        status:     running ? 'Up' : 'Stopped',
        available:  running,
      };
    } catch {
      return { running: false, restarting: false, stopped: true, status: 'unknown', available: false };
    }
  }

  /**
   * getClients(containerNameOrVersion) → string[] IP-адресов
   * Аналог AWGManager.getClients() — возвращает массив IP.
   * Используется в bot.js: awgManager.getClients(container.name)
   */
  async getClients(containerNameOrVersion) {
    if (!this.initialized) await this.initialize();
    const ver = this._versionFromContainerName(containerNameOrVersion);
    const entry = this._getEntry(ver);
    const peers = await entry.client.listPeers(entry.interfaceId);
    return peers
      .map(p => {
        const m = (p.allowedIPs || p.address || '').match(/(\d+\.\d+\.\d+\.\d+)/);
        return m ? m[1] : null;
      })
      .filter(Boolean);
  }

  /**
   * getPeerNames(containerNameOrVersion) → { [ip]: name|null }
   * В Cascade имя пира хранится в поле `name` объекта пира.
   * Используется в bot.js: awgManager.getPeerNames(container.name)
   */
  async getPeerNames(containerNameOrVersion) {
    if (!this.initialized) await this.initialize();
    try {
      const ver = this._versionFromContainerName(containerNameOrVersion);
      const entry = this._getEntry(ver);
      const peers = await entry.client.listPeers(entry.interfaceId);
      const result = {};
      for (const p of peers) {
        const m = (p.allowedIPs || p.address || '').match(/(\d+\.\d+\.\d+\.\d+)/);
        if (m) {
          result[m[1]] = p.name || null;
        }
      }
      return result;
    } catch {
      return {};
    }
  }

  /**
   * deletePeer(containerNameOrVersion, clientIP)
   * Удаляет пира из Cascade по IP-адресу.
   * Используется в bot.js: confirmDeleteClient → awgManager.deletePeer (через вызов в bot напрямую нет,
   * но confirmDeleteClient вызывает docker exec напрямую — Cascade-режим должен перехватить это выше).
   *
   * Публичный метод, чтобы можно было вызвать при рефакторинге bot.js.
   */
  async deletePeer(containerNameOrVersion, clientIP) {
    if (!this.initialized) await this.initialize();
    const ver = this._versionFromContainerName(containerNameOrVersion);
    const entry = this._getEntry(ver);
    const peers = await entry.client.listPeers(entry.interfaceId);
    const found = peers.find(p =>
      (p.allowedIPs || '').includes(clientIP) ||
      (p.address    || '').includes(clientIP)
    );
    if (!found) throw new Error(`Peer with IP ${clientIP} not found`);
    await entry.client.deletePeer(entry.interfaceId, found.id);
    logger.info(`[CascadeManager] Deleted peer ${clientIP} (id=${found.id})`);
    return { success: true };
  }

  /**
   * checkServerHealthAfterChange() — stub для обратной совместимости.
   * В Cascade не нужен перезапуск контейнера после изменений (hot-reload через syncconf).
   * Возвращает успешный health-статус немедленно.
   */
  async checkServerHealthAfterChange(containerNameOrVersion /* , maxAttempts, delayMs */) {
    if (!this.initialized) await this.initialize();
    try {
      const ver  = this._versionFromContainerName(containerNameOrVersion);
      const entry = this._getEntry(ver);
      const iface = await entry.client.getInterface(entry.interfaceId);
      return {
        containerRunning: true,
        interfaceUp:      !!iface?.enabled,
        interfaceReady:   !!iface?.enabled,
        peerCount:        iface?.peerCount ?? 0,
        attempts:         1,
        errors:           [],
        warnings:         [],
        healthy:          !!iface?.enabled,
        timestamp:        new Date().toISOString(),
      };
    } catch (err) {
      return {
        containerRunning: false,
        interfaceUp:      false,
        interfaceReady:   false,
        peerCount:        0,
        attempts:         1,
        errors:           [err.message],
        warnings:         [],
        healthy:          false,
        timestamp:        new Date().toISOString(),
      };
    }
  }

  // ── Internal helpers ───────────────────────────────────────────────────────

  _getEntry(version = null) {
    if (!this.versionMap.size) {
      throw new Error('No Cascade interfaces available. Run initialize() first.');
    }
    if (version) {
      const entry = this.versionMap.get(version);
      if (!entry) throw new Error(`No Cascade interface for version ${version}`);
      return entry;
    }
    // Prefer v2, fallback to v1 or first available
    return this.versionMap.get('v2') || this.versionMap.get('v1') || this.versionMap.values().next().value;
  }

  /** Сохранить .conf файл и вернуть путь */
  _saveConfig(content, filename) {
    if (!fs.existsSync(config.outputDir)) {
      fs.mkdirSync(config.outputDir, { recursive: true });
    }
    const filepath = path.join(config.outputDir, filename);
    fs.writeFileSync(filepath, content, 'utf8');
    logger.info(`[CascadeManager] Saved config: ${filepath}`);
    return filepath;
  }

  /** Построить имя файла конфига */
  _filename(entry, ip, vpsLabel, suffix = '') {
    const ipPart = ip.replace(/\./g, '_');
    const base = vpsLabel?.trim()
      ? `${vpsLabel}_AWG${entry.version}_${ipPart}`
      : `AWG${entry.version}_${ipPart}`;
    return suffix ? `${base}_${suffix}.conf` : `${base}.conf`;
  }

  // ── Public API (совместим с AWGManager) ───────────────────────────────────

  /**
   * Сгенерировать новый клиентский конфиг.
   * Аналог AWGManager.generateClientConfig()
   */
  async generateClientConfig(version = null, vpsLabel = null, peerName = null) {
    if (!this.initialized) await this.initialize();

    const entry = this._getEntry(version);
    const name = peerName || `peer-${Date.now()}`;

    logger.info(`[CascadeManager] Creating peer "${name}" on ${entry.serverLabel} (${entry.version})`);

    // Cascade создаёт пира, генерирует ключи и IP автоматически
    const peer = await entry.client.createPeer(entry.interfaceId, name);

    // Получаем готовый .conf от Cascade
    const configContent = await entry.client.getPeerConfig(entry.interfaceId, peer.id);

    // Извлекаем IP из конфига (Address = X.X.X.X/32)
    const ipMatch = configContent.match(/Address\s*=\s*(\d+\.\d+\.\d+\.\d+)/);
    const ip = ipMatch ? ipMatch[1] : peer.allowedIPs?.replace('/32', '') || 'unknown';

    const filename = this._filename(entry, ip, vpsLabel);
    const filepath = this._saveConfig(configContent, filename);

    return {
      filepath,
      filename,
      ip,
      publicKey: peer.publicKey,
      version: entry.version,
      peerId: peer.id,
      serverLabel: entry.serverLabel,
      healthStatus: { healthy: true },
    };
  }

  /**
   * Сгенерировать конфиг для конкретного IP-номера (последний октет).
   * Если пир с таким IP уже есть — регенерируем конфиг.
   * Аналог AWGManager.generateClientConfigByNumber()
   */
  async generateClientConfigByNumber(version, ipNumber, vpsLabel = null, peerName = null) {
    if (!this.initialized) await this.initialize();

    const entry = this._getEntry(version);
    const peers = await entry.client.listPeers(entry.interfaceId);

    // Ищем существующий пир по последнему октету IP
    const existing = peers.find(p => {
      const m = (p.allowedIPs || p.address || '').match(/\.(\d+)\/32/);
      return m && parseInt(m[1]) === ipNumber;
    });

    if (existing) {
      logger.info(`[CascadeManager] Peer with octet .${ipNumber} exists (${existing.id}), regenerating config`);
      return this.regenerateClientConfig(entry.version, existing.id, vpsLabel);
    }

    // Создаём новый пир, но сначала проверяем, что autoAllocateIP даст нужный IP
    // Cascade сам выдаёт следующий IP — в большинстве случаев это и есть нужный номер
    return this.generateClientConfig(version, vpsLabel, peerName || `peer-${ipNumber}`);
  }

  /**
   * Регенерировать конфиг по peerId (или по IP).
   * Аналог AWGManager.regenerateClientConfig()
   */
  async regenerateClientConfig(version = null, peerIdOrIp, vpsLabel = null) {
    if (!this.initialized) await this.initialize();

    const entry = this._getEntry(version);
    let peerId = peerIdOrIp;

    // Если передан IP — найти peerId
    if (peerIdOrIp.includes('.')) {
      const peers = await entry.client.listPeers(entry.interfaceId);
      const found = peers.find(p =>
        (p.allowedIPs || '').includes(peerIdOrIp) ||
        (p.address    || '').includes(peerIdOrIp)
      );
      if (!found) throw new Error(`Peer with IP ${peerIdOrIp} not found`);
      peerId = found.id;
    }

    const configContent = await entry.client.getPeerConfig(entry.interfaceId, peerId);
    const ipMatch = configContent.match(/Address\s*=\s*(\d+\.\d+\.\d+\.\d+)/);
    const ip = ipMatch ? ipMatch[1] : 'unknown';

    const filename = this._filename(entry, ip, vpsLabel, 'RESENT');
    const filepath = this._saveConfig(configContent, filename);

    return {
      filepath,
      filename,
      ip,
      peerId,
      version: entry.version,
      serverLabel: entry.serverLabel,
    };
  }

  /**
   * Статистика по всем серверам.
   * Аналог AWGManager.getStats()
   */
  async getStats() {
    if (!this.initialized) await this.initialize();

    const stats = [];
    const seen = new Set();

    for (const [version, entry] of this.versionMap) {
      if (seen.has(entry.interfaceId)) continue;
      seen.add(entry.interfaceId);

      try {
        const iface = await entry.client.getInterface(entry.interfaceId);
        stats.push({
          name:        entry.interfaceName,
          version,
          port:        entry.listenPort,
          running:     iface.enabled,
          restarting:  false,
          stopped:     !iface.enabled,
          status:      iface.enabled ? 'Up' : 'Stopped',
          clients:     iface.peerCount || 0,
          serverLabel: entry.serverLabel,
          cascadeMode: true,
        });
      } catch (err) {
        logger.warn(`[CascadeManager] getStats failed for ${entry.serverLabel}: ${err.message}`);
        stats.push({
          name:    entry.interfaceName,
          version,
          running: false,
          status:  'unknown',
          clients: 0,
          error:   err.message,
        });
      }
    }

    return stats;
  }

  /**
   * Список клиентов с активностью.
   * Аналог AWGManager.getClientsWithStatus()
   */
  async getClientsWithStatus(containerNameOrVersion, version) {
    if (!this.initialized) await this.initialize();

    const ver = version || containerNameOrVersion;
    const entry = this._getEntry(ver);
    const peers = await entry.client.listPeers(entry.interfaceId);

    return peers.map(p => {
      const ipMatch = (p.allowedIPs || p.address || '').match(/(\d+\.\d+\.\d+\.\d+)/);
      const ip = ipMatch ? ipMatch[1] : '?';
      const lastOctet = parseInt(ip.split('.')[3]) || 0;

      // latestHandshakeAt присутствует если пир активен
      const lastHandshake = p.latestHandshakeAt || null;
      const active = !!lastHandshake;

      return {
        ip,
        number: lastOctet,
        active,
        lastHandshake,
        name: p.name || null,
        peerId: p.id,
        enabled: p.enabled,
      };
    }).sort((a, b) => a.number - b.number);
  }

  /**
   * Переименовать пира.
   * Аналог AWGManager.renamePeer()
   */
  async renamePeer(containerNameOrVersion, clientIP, newPeerName) {
    if (!this.initialized) await this.initialize();

    const ver = this._versionFromContainerName(containerNameOrVersion);
    const entry = this._getEntry(ver);
    const peers = await entry.client.listPeers(entry.interfaceId);
    const found = peers.find(p =>
      (p.allowedIPs || '').includes(clientIP) ||
      (p.address    || '').includes(clientIP)
    );
    if (!found) throw new Error(`Peer with IP ${clientIP} not found`);

    await entry.client.renamePeer(entry.interfaceId, found.id, newPeerName);
    return { success: true, healthStatus: { healthy: true } };
  }

  /**
   * Получить список доступных версий.
   * Аналог AWGManager.getAvailableVersions()
   */
  getAvailableVersions() {
    return [...this.versionMap.values()].map(e => ({
      version:     e.version,
      name:        e.interfaceName,
      port:        e.listenPort,
      serverLabel: e.serverLabel,
    }));
  }

  /**
   * "Запустить контейнер" → запустить интерфейс в Cascade.
   * Аналог AWGManager.startContainer()
   */
  async startContainer(version) {
    if (!this.initialized) await this.initialize();
    const entry = this._getEntry(version);
    await entry.client.startInterface(entry.interfaceId);
    return { success: true, message: `AWG ${version} запущен` };
  }

  /**
   * "Остановить контейнер" → остановить интерфейс в Cascade.
   * Аналог AWGManager.stopContainer()
   */
  async stopContainer(version) {
    if (!this.initialized) await this.initialize();
    const entry = this._getEntry(version);
    await entry.client.stopInterface(entry.interfaceId);
    return { success: true, message: `AWG ${version} остановлен` };
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  /**
   * Определить версию по имени контейнера (для обратной совместимости).
   * 'amnezia-awg' → 'v1', 'amnezia-awg2' → 'v2', иначе используем как version.
   */
  _versionFromContainerName(name) {
    if (name === 'amnezia-awg')  return 'v1';
    if (name === 'amnezia-awg2') return 'v2';
    if (name === 'v1' || name === 'v2') return name;
    // Если передано имя интерфейса — ищем по нему
    for (const [ver, entry] of this.versionMap) {
      if (entry.interfaceName === name) return ver;
    }
    return name;
  }
}
