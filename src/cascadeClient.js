/**
 * Cascade API Client
 * HTTP-клиент для работы с Cascade (cascade-0.9.2) REST API.
 *
 * Аутентификация (ref: internal/api/auth.go):
 *   1. POST /api/session  { username, password }  → Set-Cookie: session_id=...
 *   2. POST /api/tokens   Cookie: session_id=...  → { token: "ws_..." }
 *   3. DELETE /api/session                         — logout
 *   4. Все дальнейшие запросы: X-Api-Token: ws_...  ИЛИ  Authorization: Bearer ws_...
 *
 * Поддерживает несколько серверов (каскад):
 *   - primary   — основной сервер
 *   - remotes   — удалённые, проксируются через primary:
 *                 /api/remotes/:id/proxy/*
 */

import { logger } from './logger.js';

// ── HTTP helpers ───────────────────────────────────────────────────────────────

/**
 * Базовый fetch с таймаутом. Возвращает { body, headers, status }.
 * При HTTP ≥ 400 бросает Error с полем .status.
 */
async function rawFetch(url, options = {}) {
  const timeout = options.timeout || 12_000;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);

  try {
    const res = await fetch(url, {
      ...options,
      signal: ctrl.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });
    clearTimeout(timer);

    const text = await res.text();
    let body;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { message: text };
    }

    if (!res.ok) {
      const msg = body?.message || body?.error || `HTTP ${res.status}`;
      const err = new Error(msg);
      err.status  = res.status;
      err.body    = body;
      err.headers = res.headers;
      throw err;
    }

    return { body, headers: res.headers, status: res.status };
  } catch (err) {
    clearTimeout(timer);
    if (err.name === 'AbortError') {
      throw new Error(`Cascade API timeout (${timeout}ms): ${url}`);
    }
    throw err;
  }
}

/** Обёртка — возвращает только body для обратной совместимости. */
async function apiFetch(url, options = {}) {
  const { body } = await rawFetch(url, options);
  return body;
}

// ── CascadeClient ──────────────────────────────────────────────────────────────

export class CascadeClient {
  /**
   * @param {Object} opts
   * @param {string}  opts.url       Base URL, e.g. "http://10.0.0.1:51821"
   * @param {string}  [opts.token]   Pre-existing API token (skip login)
   * @param {string}  [opts.username]
   * @param {string}  [opts.password]
   * @param {string}  [opts.label]   Human label for logs
   */
  constructor(opts) {
    this.url      = (opts.url || '').replace(/\/$/, '');
    this.token    = opts.token    || null;
    this.username = opts.username || 'admin';
    this.password = opts.password || '';
    this.label    = opts.label    || this.url;
    this._cookie  = null; // session cookie для login → createToken flow
  }

  // ── Auth ─────────────────────────────────────────────────────────────────────

  /**
   * Получить API-токен: login → (сохранить cookie) → createToken → logout.
   * Если токен уже есть — ничего не делаем.
   *
   * ИСПРАВЛЕНИЕ: fetch() в Node.js не хранит cookies автоматически.
   * Нужно вручную парсить Set-Cookie из ответа POST /api/session
   * и передавать Cookie при POST /api/tokens.
   */
  async ensureToken() {
    if (this.token) return;

    logger.info(`[Cascade:${this.label}] Получаем API-токен (login → token → logout)...`);

    // ── Шаг 1: Login ──────────────────────────────────────────────────────────
    let sessionCookie = '';
    try {
      const { body, headers } = await rawFetch(`${this.url}/api/session`, {
        method: 'POST',
        body:   JSON.stringify({ username: this.username, password: this.password }),
      });

      if (!body?.authenticated) {
        throw new Error(`Login rejected — check credentials for ${this.label}`);
      }

      // Извлекаем Set-Cookie из ответа вручную
      // (Node.js fetch не хранит cookies автоматически)
      const setCookie = headers.get('set-cookie') || '';
      if (setCookie) {
        // Берём только первое значение до первой `;`
        sessionCookie = setCookie.split(';')[0].trim();
        logger.debug(`[Cascade:${this.label}] Session cookie obtained`);
      } else {
        // Open mode или сервер не вернул cookie
        logger.warn(`[Cascade:${this.label}] No session cookie returned — Cascade may be in open mode`);
      }
    } catch (err) {
      throw new Error(`[Cascade:${this.label}] Login failed: ${err.message}`);
    }

    // ── Шаг 2: Create API token ────────────────────────────────────────────────
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (sessionCookie) headers['Cookie'] = sessionCookie;

      const tokenRes = await apiFetch(`${this.url}/api/tokens`, {
        method: 'POST',
        headers,
        body:   JSON.stringify({ name: `awgbot-${Date.now()}` }),
      });

      // Cascade возвращает { token: "ws_...", id: "...", ... }
      this.token = tokenRes?.token || tokenRes?.id;
      if (!this.token) {
        throw new Error('Token creation returned empty token');
      }
    } catch (err) {
      throw new Error(`[Cascade:${this.label}] Token creation failed: ${err.message}`);
    }

    // ── Шаг 3: Logout (cleanup session) ───────────────────────────────────────
    try {
      const headers = {};
      if (sessionCookie) headers['Cookie'] = sessionCookie;
      await apiFetch(`${this.url}/api/session`, { method: 'DELETE', headers });
    } catch { /* non-critical */ }

    logger.info(`[Cascade:${this.label}] API-токен получен успешно`);
  }

  /**
   * Базовый HTTP-запрос с автоматическим добавлением X-Api-Token.
   * При 401 — сбрасывает токен и пробует один раз повторно.
   */
  async _req(method, path, body = null, opts = {}) {
    await this.ensureToken();

    const options = {
      method,
      headers: { 'X-Api-Token': this.token },
      timeout: opts.timeout || 15_000,
    };
    if (body !== null) {
      options.body = JSON.stringify(body);
    }

    try {
      const { body: resBody } = await rawFetch(`${this.url}/api${path}`, options);
      return resBody;
    } catch (err) {
      // Токен истёк / отозван → сбросить и повторить один раз
      if (err.status === 401 && !opts._retried) {
        logger.warn(`[Cascade:${this.label}] Token rejected (401), re-authenticating...`);
        this.token = null;
        await this.ensureToken();
        return this._req(method, path, body, { ...opts, _retried: true });
      }
      throw err;
    }
  }

  get  = (path, opts)        => this._req('GET',    path, null,  opts);
  post = (path, body, opts)  => this._req('POST',   path, body,  opts);
  patch= (path, body, opts)  => this._req('PATCH',  path, body,  opts);
  del  = (path, opts)        => this._req('DELETE', path, null,  opts);
  put  = (path, body, opts)  => this._req('PUT',    path, body,  opts);

  // ── Interfaces ────────────────────────────────────────────────────────────────

  /** GET /api/tunnel-interfaces → { interfaces: [...] } */
  async listInterfaces() {
    const data = await this.get('/tunnel-interfaces');
    return data?.interfaces || [];
  }

  /** GET /api/tunnel-interfaces/:id */
  async getInterface(id) {
    return this.get(`/tunnel-interfaces/${id}`);
  }

  /** POST /api/tunnel-interfaces/quick-create */
  async quickCreateInterface(name, protocol = 'amneziawg-2.0') {
    return this.post('/tunnel-interfaces/quick-create', { name, protocol });
  }

  /** POST /api/tunnel-interfaces/:id/start */
  async startInterface(id) {
    return this.post(`/tunnel-interfaces/${id}/start`);
  }

  /** POST /api/tunnel-interfaces/:id/stop */
  async stopInterface(id) {
    return this.post(`/tunnel-interfaces/${id}/stop`);
  }

  /** POST /api/tunnel-interfaces/:id/restart */
  async restartInterface(id) {
    return this.post(`/tunnel-interfaces/${id}/restart`);
  }

  /**
   * POST /api/tunnel-interfaces/import-conf-server
   * Импортирует серверный .conf с [Peer] секциями.
   * Ref: internal/api/interfaces.go:importConfServerInterface
   * Body: { name, conf }
   */
  async importConfServer(name, conf) {
    return this.post('/tunnel-interfaces/import-conf-server', { name, conf });
  }

  /**
   * POST /api/tunnel-interfaces/import-backup
   * Импортирует AWG-Easy JSON backup.
   * Body: { json: "<raw JSON string>", listenPort: N }
   */
  async importBackup(rawJson, listenPort) {
    return this.post('/tunnel-interfaces/import-backup', { json: rawJson, listenPort });
  }

  /**
   * GET /api/tunnel-interfaces/:id/backup
   * Скачивает резервную копию интерфейса + пиров в JSON.
   */
  async backupInterface(id) {
    return this.get(`/tunnel-interfaces/${id}/backup`);
  }

  /**
   * GET /api/tunnel-interfaces/:id/export-obfuscation
   * Возвращает AWG2 параметры обфускации.
   */
  async exportObfuscation(id) {
    return this.get(`/tunnel-interfaces/${id}/export-obfuscation`);
  }

  // ── Peers ─────────────────────────────────────────────────────────────────────

  /** GET /api/tunnel-interfaces/:ifaceId/peers → { peers: [...] } */
  async listPeers(ifaceId) {
    const data = await this.get(`/tunnel-interfaces/${ifaceId}/peers`);
    return data?.peers || [];
  }

  /**
   * POST /api/tunnel-interfaces/:ifaceId/peers
   * Body: { name, generateKeys: true, autoAllocateIP: true, ... }
   */
  async createPeer(ifaceId, name, opts = {}) {
    const body = {
      name,
      peerType: 'client',
      ...opts,
    };
    const data = await this.post(`/tunnel-interfaces/${ifaceId}/peers`, body);
    return data?.peer || data;
  }

  /** DELETE /api/tunnel-interfaces/:ifaceId/peers/:peerId */
  async deletePeer(ifaceId, peerId) {
    return this.del(`/tunnel-interfaces/${ifaceId}/peers/${peerId}`);
  }

  /** PATCH /api/tunnel-interfaces/:ifaceId/peers/:peerId */
  async updatePeer(ifaceId, peerId, updates) {
    return this.patch(`/tunnel-interfaces/${ifaceId}/peers/${peerId}`, updates);
  }

  /** POST /api/tunnel-interfaces/:ifaceId/peers/:peerId/enable */
  async enablePeer(ifaceId, peerId) {
    return this.post(`/tunnel-interfaces/${ifaceId}/peers/${peerId}/enable`);
  }

  /** POST /api/tunnel-interfaces/:ifaceId/peers/:peerId/disable */
  async disablePeer(ifaceId, peerId) {
    return this.post(`/tunnel-interfaces/${ifaceId}/peers/${peerId}/disable`);
  }

  /**
   * PUT /api/tunnel-interfaces/:ifaceId/peers/:peerId/name
   * Ref: api/peers.go — ожидает { name: string }
   */
  async renamePeer(ifaceId, peerId, newName) {
    return this.put(`/tunnel-interfaces/${ifaceId}/peers/${peerId}/name`, { name: newName });
  }

  /**
   * GET /api/tunnel-interfaces/:ifaceId/peers/:peerId/config
   * → plain text WireGuard .conf
   */
  async getPeerConfig(ifaceId, peerId) {
    await this.ensureToken();
    const res = await fetch(
      `${this.url}/api/tunnel-interfaces/${ifaceId}/peers/${peerId}/config`,
      { headers: { 'X-Api-Token': this.token } }
    );
    if (!res.ok) throw new Error(`getPeerConfig: HTTP ${res.status}`);
    return res.text();
  }

  /**
   * GET /api/tunnel-interfaces/:ifaceId/peers/:peerId/qrcode.svg
   * → SVG string
   */
  async getPeerQRCode(ifaceId, peerId) {
    await this.ensureToken();
    const res = await fetch(
      `${this.url}/api/tunnel-interfaces/${ifaceId}/peers/${peerId}/qrcode.svg`,
      { headers: { 'X-Api-Token': this.token } }
    );
    if (!res.ok) throw new Error(`getPeerQRCode: HTTP ${res.status}`);
    return res.text();
  }

  // ── Remotes (Multi-VPS) ───────────────────────────────────────────────────────

  /**
   * GET /api/remotes → { remotes: [...] }
   * Список зарегистрированных удалённых Cascade-серверов.
   * Ref: internal/api/remotes.go
   */
  async listRemotes() {
    const data = await this.get('/remotes');
    return data?.remotes || [];
  }

  /**
   * POST /api/remotes
   * Добавить удалённый Cascade-сервер.
   * Режим с токеном: { name, url, token }
   * Режим login: { name, url, username, password }
   * @param {string} name
   * @param {string} url
   * @param {string} token  — API-токен удалённого сервера
   * @param {Object} [opts] — { skipTlsVerify: false }
   */
  async addRemote(name, url, token, opts = {}) {
    return this.post('/remotes', {
      name,
      url,
      token,
      skipTlsVerify: opts.skipTlsVerify || false,
    });
  }

  /**
   * POST /api/remotes — режим login (username + password)
   */
  async addRemoteLogin(name, url, username, password, opts = {}) {
    return this.post('/remotes', {
      name,
      url,
      username,
      password,
      skipTlsVerify: opts.skipTlsVerify || false,
    });
  }

  /** DELETE /api/remotes/:id */
  async deleteRemote(id) {
    return this.del(`/remotes/${id}`);
  }

  /** POST /api/remotes/:id/test → { ok: true } */
  async testRemote(id) {
    return this.post(`/remotes/${id}/test`);
  }

  // ── Health ────────────────────────────────────────────────────────────────────

  /** GET /api/health → { status, version, host } */
  async health() {
    return apiFetch(`${this.url}/api/health`, { timeout: 5_000 });
  }

  /** Ping — returns true if server is reachable */
  async ping() {
    try {
      await this.health();
      return true;
    } catch {
      return false;
    }
  }

  // ── Settings ──────────────────────────────────────────────────────────────────

  /** GET /api/settings */
  async getSettings() {
    return this.get('/settings');
  }

  /** PATCH /api/settings */
  async updateSettings(updates) {
    return this.patch('/settings', updates);
  }
}
