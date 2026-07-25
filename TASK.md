# Задача: Интеграция Cascade API в AWGXUIBot

> **Документ:** постановка задачи, статус выполнения, что осталось сделать.
> **Ветка:** `dev`
> **Дата составления:** текущая

---

## 1. Контекст и цель

### Что такое AWGXUIBot
Telegram-бот для управления двумя вещами:
1. **XUI-панели** (3x-ui) — управление VLESS/XHTTP/Reality пользователями
2. **AmneziaWG/WireGuard VPN** — генерация клиентских `.conf` файлов, статистика, переименование пиров

Бот работает с несколькими серверами: `jons`, `web`, `rus`, `yun` (см. `294/config.yaml`).

### Проблема (исходная)
AWG-часть бота управляла VPN напрямую через `docker exec` внутрь контейнеров `amnezia-awg` / `amnezia-awg2`:
- Читала `.conf` файлы через `docker exec cat`
- Добавляла пиров через `docker exec` + `wg syncconf`
- Генерировала ключи через `docker exec wg genkey`

**Ограничения:**
- Жёсткая завязка на Docker и конкретные пути внутри контейнеров Amnezia
- Нет удобного веб-интерфейса для просмотра/управления
- Сложно масштабировать на несколько VPS серверов

### Решение: Cascade
**Cascade** — веб-UI (Go, порт 51821) с полноценным REST API для управления WireGuard/AmneziaWG интерфейсами и пирами.
- Репозиторий: `294/cascade-0.9.2/`
- API-документация: `294/cascade-0.9.2/docs/API.md`
- Заменяет прямые `docker exec` вызовы на HTTP-запросы

**Цель задачи:** Переключить AWG-часть бота с прямого `docker exec` на управление через Cascade REST API, сохранив полную обратную совместимость интерфейса бота для пользователя.

---

## 2. Архитектура решения

```
Telegram User
     │
     ▼
 bot.js (RouteBot)
     │
     ├─ config.cascadeEnabled == false ──► AWGManager (docker exec — старый режим)
     │
     └─ config.cascadeEnabled == true  ──► AWGManagerCascade
                                               │
                                               ▼
                                          CascadeClient (HTTP)
                                               │
                                               ▼
                                     Cascade REST API (:51821)
                                               │
                                    ┌──────────┴──────────┐
                                    ▼                     ▼
                              AWG Interface v1      AWG Interface v2
                              (wireguard-1.0)    (amneziawg-2.0)
```

### Переключение режимов
В `config.yaml` — секция `cascade:`. Если хотя бы один сервер указан с `url` — бот автоматически использует `AWGManagerCascade`:
```yaml
cascade:
  primary:
    url: "http://localhost:51821"
    token: "ws_..."        # токен ИЛИ username/password ниже
    username: "admin"
    password: "..."
```

---

## 3. Что сделано ✅

### 3.1 `src/cascadeClient.js` — HTTP-клиент для Cascade API
- Класс `CascadeClient`
- **Аутентификация:** `POST /api/session` → ручной парсинг `Set-Cookie` (Node.js fetch не хранит cookies) → `POST /api/tokens` → `DELETE /api/session`
- Авто-повтор при HTTP 401 (токен истёк → сброс → переаутентификация)
- Базовые методы: `get`, `post`, `patch`, `put`, `del`
- **Интерфейсы:** `listInterfaces()`, `getInterface()`, `quickCreateInterface()`, `startInterface()`, `stopInterface()`, `restartInterface()`, `importConfServer()`, `importBackup()`, `backupInterface()`, `exportObfuscation()`
- **Пиры:** `listPeers()`, `createPeer()`, `deletePeer()`, `updatePeer()`, `enablePeer()`, `disablePeer()`, `renamePeer()`, `getPeerConfig()` (plain text), `getPeerQRCode()` (SVG)
- **Multi-VPS (remotes):** `listRemotes()`, `addRemote()`, `addRemoteLogin()`, `deleteRemote()`
- **ping()** через `GET /api/health`

### 3.2 `src/awgManagerCascade.js` — замена AWGManager
Публичный интерфейс **полностью совместим** с `AWGManager` — бот не знает с каким менеджером работает.

| Метод | Что делает |
|-------|-----------|
| `initialize()` | Пингует все сконфигурированные серверы, получает список интерфейсов, строит `versionMap: v1/v2 → { client, interfaceId, ... }` |
| `generateClientConfig(version, vpsLabel, peerName)` | Создаёт пира в Cascade, получает `.conf`, сохраняет в `./output/` |
| `generateClientConfigByNumber(version, ipNumber, ...)` | Ищет пира по последнему октету IP, при наличии — регенерирует конфиг, иначе создаёт нового |
| `regenerateClientConfig(version, peerIdOrIp, ...)` | Скачивает актуальный `.conf` пира по peerId или IP |
| `getStats()` | Возвращает статус всех интерфейсов (enabled, peerCount, port, ...) |
| `getClientsWithStatus(containerOrVersion, version)` | Список пиров с last handshake (active/inactive) |
| `renamePeer(containerOrVersion, clientIP, newName)` | Переименовывает пира через `PUT /peers/:id/name` |
| `getAvailableVersions()` | Список доступных версий (v1/v2) с именами интерфейсов |
| `startContainer(version)` | `POST /tunnel-interfaces/:id/start` |
| `stopContainer(version)` | `POST /tunnel-interfaces/:id/stop` |
| `_versionFromContainerName()` | `amnezia-awg` → `v1`, `amnezia-awg2` → `v2` (обратная совместимость) |

Маппинг протоколов:
- `amneziawg-2.0` → версия `v2`
- `wireguard-1.0` → версия `v1`

### 3.3 `src/config.js` — поддержка секции `cascade`
- Читает `data.cascade` из `config.yaml`
- Преобразует в массив `cascadeServers: [{ label, url, token, username, password, version }]`
- Выставляет `cascadeEnabled: true` если хотя бы один сервер с `url` найден

### 3.4 `src/bot.js` — переключение менеджера
```js
if (config.cascadeEnabled) {
  this.awgManager = new AWGManagerCascade(config.cascadeServers);
} else {
  this.awgManager = new AWGManager();
}
```

### 3.5 `config.yaml.example` — документированная секция `cascade`
- Два примера конфигурации (один сервер / два сервера)
- Описание обоих методов аутентификации (token / username+password)
- `cascade: {}` по умолчанию (Cascade отключён)

### 3.6 `docker-compose.cascade.yml` — деплой Cascade
- `network_mode: host` (обязательно для WireGuard/AWG)
- `cap_add: [NET_ADMIN, SYS_MODULE]`
- Image: `ghcr.io/johnnyvbut/cascade:latest`
- Env: `PORT=51821`, `WG_HOST`, `WG_PORT=51820`, `PASSWORD_HASH`
- Подробные комментарии: первый запуск, миграция, multi-VPS

### 3.7 `migrate-to-cascade.js` — скрипт миграции
- Читает `config.yaml` (секция `cascade`) или принимает `--cascade-url` / `--token`
- Читает серверные `.conf` из `amnezia-awg` / `amnezia-awg2` через `docker exec`
- Импортирует через `POST /api/tunnel-interfaces/import-conf-server`
- Все пиры переносятся **с теми же ключами** — существующие клиентские конфиги остаются валидными
- Флаги: `--dry-run`, `--v1-only`, `--v2-only`, `--listen-port`, `--listen-port2`

---

## 4. Что нужно сделать ❌

### 4.1 КРИТИЧНО: `createPeer` — неверный формат тела запроса

**Проблема:** В `cascadeClient.js` метод `createPeer()` передаёт `generateKeys: true` и `autoAllocateIP: true`, но эти поля **не существуют** в API Cascade.

Согласно `294/cascade-0.9.2/docs/API.md` и `294/cascade-0.9.2/internal/api/peers.go`:
```
POST /api/tunnel-interfaces/:id/peers
Body: { name, peerType ("client"/"interconnect"), clientAllowedIPs?, persistentKeepalive?, expiredAt? }
```
Cascade **сам генерирует ключи** и **сам назначает IP** — поля `generateKeys` и `autoAllocateIP` не нужны. Нужно убрать их из тела запроса.

**Исправление в `src/cascadeClient.js`:**
```js
async createPeer(ifaceId, name, opts = {}) {
  const body = {
    name,
    peerType: 'client',
    ...opts,
  };
  const data = await this.post(`/tunnel-interfaces/${ifaceId}/peers`, body);
  return data?.peer || data;
}
```

### 4.2 КРИТИЧНО: `getInterface()` — неверная структура ответа

**Проблема:** В `awgManagerCascade.js` метод `getStats()` вызывает `client.getInterface(id)` и читает `iface.enabled` и `iface.peerCount`.

Cascade API возвращает интерфейс **не обёрнутым** в отдельное поле. Нужно проверить реальную структуру ответа `GET /api/tunnel-interfaces/:id` и убедиться что поля `enabled` и `peerCount` существуют (или найти правильные названия полей).

По документации API и Go-коду интерфейс содержит поля: `id`, `name`, `address`, `listenPort`, `protocol`, `publicKey`, `enabled`, `natDisabled`, `peerCount` — поля `enabled` и `peerCount` существуют, но `getInterface()` в клиенте возвращает сырой ответ который **является самим объектом**, без обёртки. Нужно убедиться что код работает корректно.

### 4.3 ВАЖНО: `deletePeer` — не реализован в `AWGManagerCascade`

**Проблема:** В `bot.js` есть обработчики `delete_` и `confirm_delete_` которые вызывают `deleteClientConfig()` → `awgManager.deletePeer()`. В `AWGManager` метод реализован. В `AWGManagerCascade` метода `deletePeer()` **нет**.

**Нужно добавить в `src/awgManagerCascade.js`:**
```js
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
  return { success: true };
}
```

### 4.4 ВАЖНО: Отсутствует обработка `import-conf-server` в `cascadeClient.js`

**Проблема:** Метод `importConfServer()` есть, но в API Cascade эндпоинт называется `POST /api/tunnel-interfaces/import-conf-server` и ожидает `{ name: string, conf: string }`.

Текущий код в `cascadeClient.js`:
```js
async importConfServer(name, conf) {
  return this.post('/tunnel-interfaces/import-conf-server', { name, conf });
}
```
Это **правильно** — проверить что `migrate-to-cascade.js` передаёт именно `{ name, conf }`.

### 4.5 ВАЖНО: `getStats()` — поле `peerCount` может не существовать

По документации `GET /api/tunnel-interfaces/:id` возвращает объект интерфейса. Поле `peerCount` упоминается в описании `POST /peers` (ответ содержит `totalRx`/`totalTx`), но не явно в описании `GET /api/tunnel-interfaces/:id`. Нужно либо получать количество пиров через `listPeers()` и считать `length`, либо убедиться что поле есть.

### 4.6 ЖЕЛАТЕЛЬНО: Поддержка QR-кода при генерации конфига

При генерации нового пира можно дополнительно запрашивать QR-код через `getPeerQRCode()` и отправлять его пользователю как изображение в Telegram. Сейчас QR-код не используется.

### 4.7 ЖЕЛАТЕЛЬНО: Тестирование интеграции

Перед продакшн-запуском нужно проверить:
1. Запустить Cascade через `docker compose -f docker-compose.cascade.yml up -d`
2. Настроить `config.yaml` секцию `cascade:`
3. Убедиться что `initialize()` корректно определяет интерфейсы v1/v2
4. Проверить генерацию нового конфига (создание пира)
5. Проверить `getStats()` и `getClientsWithStatus()`
6. Проверить переименование и удаление пира
7. Запустить `node migrate-to-cascade.js --dry-run` для проверки миграции

---

## 5. Файлы проекта

| Файл | Статус | Описание |
|------|--------|---------|
| `src/cascadeClient.js` | ✅ Создан | HTTP-клиент Cascade API |
| `src/awgManagerCascade.js` | ⚠️ Создан, нужны правки | Менеджер через Cascade API |
| `src/config.js` | ✅ Изменён | Поддержка секции `cascade:` |
| `src/bot.js` | ✅ Изменён | Переключение менеджера по конфигу |
| `src/awgManager.js` | ✅ Без изменений | Старый режим docker exec |
| `src/awgConverter.js` | ✅ Без изменений | Конвертация конфигов v1↔v2 |
| `config.yaml.example` | ✅ Изменён | Документированная секция cascade |
| `docker-compose.cascade.yml` | ✅ Создан | Деплой Cascade |
| `migrate-to-cascade.js` | ✅ Создан | Скрипт миграции |
| `install.sh` | ✅ Изменён | Обновлён под новую инфраструктуру |

---

## 6. Референсные материалы

| Путь | Описание |
|------|---------|
| `294/cascade-0.9.2/docs/API.md` | Полная API-документация Cascade (русский) |
| `294/cascade-0.9.2/docs/API.en.md` | То же, английский |
| `294/cascade-0.9.2/internal/api/peers.go` | Go-обработчики для Peer CRUD |
| `294/cascade-0.9.2/internal/api/interfaces.go` | Go-обработчики для Interface CRUD |
| `294/cascade-0.9.2/internal/api/auth.go` | Go-обработчики аутентификации |
| `294/cascade-0.9.2/docs/context/PROJECT_CONTEXT.md` | Контекст разработки Cascade |
| `294/cascade-0.9.2/docs/context/Peer.js` | Структура объекта Peer (референс) |
| `294/cascade-0.9.2/docs/context/tunnel-interfaces.js` | Референс API маршрутов |
| `294/config.yaml` | Рабочий конфиг (с реальными серверами) |
| `294/api.txt` | OpenAPI-спецификация 3x-ui |

---

## 7. Ключевые API-эндпоинты Cascade

```
Auth:
  POST   /api/session          { username, password }  → Set-Cookie
  POST   /api/tokens           Cookie: ...             → { token: "ws_..." }
  DELETE /api/session          (logout)

Interfaces:
  GET    /api/tunnel-interfaces
  POST   /api/tunnel-interfaces/quick-create
  POST   /api/tunnel-interfaces/import-conf-server   { name, conf }
  GET    /api/tunnel-interfaces/:id
  POST   /api/tunnel-interfaces/:id/start
  POST   /api/tunnel-interfaces/:id/stop

Peers:
  GET    /api/tunnel-interfaces/:id/peers
  POST   /api/tunnel-interfaces/:id/peers           { name, peerType }
  GET    /api/tunnel-interfaces/:id/peers/:peerId
  PATCH  /api/tunnel-interfaces/:id/peers/:peerId
  DELETE /api/tunnel-interfaces/:id/peers/:peerId
  GET    /api/tunnel-interfaces/:id/peers/:peerId/config     → text/plain .conf
  GET    /api/tunnel-interfaces/:id/peers/:peerId/qrcode.svg
  PUT    /api/tunnel-interfaces/:id/peers/:peerId/name       { name }
  POST   /api/tunnel-interfaces/:id/peers/:peerId/enable
  POST   /api/tunnel-interfaces/:id/peers/:peerId/disable

Health:
  GET    /api/health   → { status: "ok", version, host }
```

---

## 8. Порядок доработки (приоритет)

1. **[КРИТ]** Исправить `createPeer()` в `cascadeClient.js` — убрать `generateKeys`/`autoAllocateIP`
2. **[КРИТ]** Добавить `deletePeer()` в `awgManagerCascade.js`
3. **[ВАЖНО]** Проверить поля ответа `getInterface()` — `enabled`, `peerCount`
4. **[ВАЖНО]** Сквозное тестирование на реальном Cascade-сервере
5. **[ЖЕЛАТ]** Добавить отправку QR-кода при генерации конфига
