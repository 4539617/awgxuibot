# backup_manager.py
# Полный бэкап системы: config.yaml, bot_users.db, apps.yaml,
# x-ui.db и inbounds JSON каждой панели.
# Вызывается только из bot.py где права администратора проверяются заранее.

import json
import logging
import shutil
import ssl
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Корень приложения внутри контейнера (/app) — два уровня вверх от python/
_APP_ROOT    = Path(__file__).resolve().parent.parent
_BACKUP_ROOT = _APP_ROOT / "backups"
_MAX_BACKUPS = 10   # максимум хранимых ZIP-архивов


# ─── HTTP-хелперы ─────────────────────────────────────────────────────────────

def _make_connector():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    return aiohttp.TCPConnector(ssl=ssl_ctx)


def _headers(panel_cfg) -> dict:
    """Bearer-заголовок для v3 с токеном; пустой — для v2 (используется сессионная кука)."""
    version = panel_cfg.xui_version or ""
    token   = (panel_cfg.xui_api_token or "").strip()
    if (version.startswith("3.") or version == "latest") and token:
        return {"Authorization": f"Bearer {token}"}
    return {}


async def _open_session(panel_cfg) -> aiohttp.ClientSession:
    session = aiohttp.ClientSession(
        connector=_make_connector(),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        timeout=aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10),
    )
    # Для v2 (без токена) нужна сессионная авторизация
    if not _headers(panel_cfg):
        base = panel_cfg.xui_url.rstrip("/")
        try:
            async with session.post(
                f"{base}/login",
                json={
                    "username": panel_cfg.xui_username,
                    "password": panel_cfg.xui_password,
                },
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[backup] {panel_cfg.alias}: login HTTP {resp.status}"
                    )
        except Exception as e:
            logger.warning(f"[backup] {panel_cfg.alias}: login error: {e}")
    return session


async def _fetch_xui_db(panel_cfg) -> Optional[bytes]:
    """Скачивает x-ui.db через /panel/api/server/getDb."""
    base = panel_cfg.xui_url.rstrip("/")
    hdrs = _headers(panel_cfg)
    session = await _open_session(panel_cfg)
    try:
        async with session.get(
            f"{base}/panel/api/server/getDb", headers=hdrs
        ) as resp:
            if resp.status == 200:
                return await resp.read()
            logger.warning(
                f"[backup] {panel_cfg.alias}: getDb → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: getDb error: {e}")
        return None
    finally:
        await session.close()


async def _fetch_inbounds(panel_cfg) -> Optional[list]:
    """Скачивает список инбаундов + клиентов."""
    base    = panel_cfg.xui_url.rstrip("/")
    hdrs    = _headers(panel_cfg)
    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    endpoint = (
        f"{base}/panel/api/inbounds/list"
        if is_v3
        else f"{base}/xui/API/inbounds/list"
    )
    session = await _open_session(panel_cfg)
    try:
        async with session.get(endpoint, headers=hdrs) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    return data.get("obj") or []
            logger.warning(
                f"[backup] {panel_cfg.alias}: inbounds/list → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: inbounds error: {e}")
        return None
    finally:
        await session.close()


def _xray_config_supported(panel_cfg) -> bool:
    """GET /panel/api/xray появился в 3x-ui ~3.5.x.
    Версии 3.3.x / 3.4.x возвращают 404 — пропускаем."""
    version = panel_cfg.xui_version or ""
    if version == "latest":
        return True
    if not version.startswith("3."):
        return False
    try:
        parts = version.split(".")
        minor = int(parts[1]) if len(parts) > 1 else 0
        return minor >= 5
    except ValueError:
        return True   # неизвестная версия — пробуем


async def _fetch_xray_config(panel_cfg) -> Optional[dict]:
    """Скачивает полный шаблон конфига Xray через POST /panel/api/xray/ (только v3 ≥ 3.5).
    Содержит: inbounds, outbounds, routing, log, policy, api, stats, metrics.
    """
    if not _xray_config_supported(panel_cfg):
        return None  # v2 или старые v3 не поддерживают этот endpoint
    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    if not is_v3:
        return None

    base = panel_cfg.xui_url.rstrip("/")
    hdrs = _headers(panel_cfg)
    session = await _open_session(panel_cfg)
    try:
        async with session.post(
            f"{base}/panel/api/xray/", headers=hdrs, json={}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    obj = data.get("obj")
                    # obj может быть:
                    # - dict {"xraySetting": "<json string>", ...} (v3 новая)
                    # - str  "<json string>" (старое поведение)
                    # - dict с уже распакованным конфигом
                    if isinstance(obj, dict) and "xraySetting" in obj:
                        raw = obj["xraySetting"]
                        try:
                            return json.loads(raw) if isinstance(raw, str) else raw or {}
                        except Exception:
                            logger.warning(
                                f"[backup] {panel_cfg.alias}: xraySetting не является JSON"
                            )
                            return None
                    elif isinstance(obj, str):
                        try:
                            return json.loads(obj) or {}
                        except Exception:
                            logger.warning(
                                f"[backup] {panel_cfg.alias}: xray obj — не JSON-строка"
                            )
                            return None
                    return obj or {}
            logger.warning(
                f"[backup] {panel_cfg.alias}: xray/ → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: xray config error: {e}")
        return None
    finally:
        await session.close()


async def _fetch_clients(panel_cfg) -> Optional[list]:
    """Скачивает экспорт клиентов через /panel/api/clients/export (только v3).
    Официальный бэкап-формат: [{client: {...}, inboundIds: [...]}] —
    round-trip совместим с /panel/api/clients/import для восстановления.
    """
    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    if not is_v3:
        return None  # v2 не поддерживает этот endpoint

    base = panel_cfg.xui_url.rstrip("/")
    hdrs = _headers(panel_cfg)
    session = await _open_session(panel_cfg)
    try:
        async with session.get(
            f"{base}/panel/api/clients/export", headers=hdrs
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    return data.get("obj") or []
            logger.warning(
                f"[backup] {panel_cfg.alias}: clients/export → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: clients/export error: {e}")
        return None
    finally:
        await session.close()


async def _fetch_hosts(panel_cfg) -> Optional[list]:
    """Скачивает список subscription hosts через /panel/api/hosts/list (только v3).
    Содержит кастомные домены/IP/порты для генерации ссылок клиентам.
    """
    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    if not is_v3:
        return None

    base = panel_cfg.xui_url.rstrip("/")
    hdrs = _headers(panel_cfg)
    session = await _open_session(panel_cfg)
    try:
        async with session.get(
            f"{base}/panel/api/hosts/list", headers=hdrs
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    return data.get("obj") or []
            logger.warning(
                f"[backup] {panel_cfg.alias}: hosts/list → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: hosts/list error: {e}")
        return None
    finally:
        await session.close()


async def _fetch_settings(panel_cfg) -> Optional[dict]:
    """Скачивает все настройки панели через POST /panel/api/setting/all (только v3).
    Содержит: web server, Telegram bot, подписки, безопасность, LDAP.
    """
    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    if not is_v3:
        return None

    base = panel_cfg.xui_url.rstrip("/")
    hdrs = _headers(panel_cfg)
    session = await _open_session(panel_cfg)
    try:
        async with session.post(
            f"{base}/panel/api/setting/all", headers=hdrs
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    return data.get("obj") or {}
            logger.warning(
                f"[backup] {panel_cfg.alias}: setting/all → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: settings error: {e}")
        return None
    finally:
        await session.close()


async def _fetch_outbound_subs(panel_cfg) -> Optional[list]:
    """Скачивает список outbound-подписок через /panel/api/xray/outbound-subs (только v3).
    Содержит удалённые URL которые поставляют дополнительные outbounds в xray конфиг.
    """
    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    if not is_v3:
        return None

    base = panel_cfg.xui_url.rstrip("/")
    hdrs = _headers(panel_cfg)
    session = await _open_session(panel_cfg)
    try:
        async with session.get(
            f"{base}/panel/api/xray/outbound-subs", headers=hdrs
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    return data.get("obj") or []
            logger.warning(
                f"[backup] {panel_cfg.alias}: outbound-subs → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: outbound-subs error: {e}")
        return None
    finally:
        await session.close()


async def _fetch_outbounds(panel_cfg) -> Optional[list]:
    """Скачивает список outbounds через GET /panel/api/xray/outbound/list (только v3).
    Панель хранит outbounds отдельно от xray-шаблона — шаблон их не содержит.
    """
    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    if not is_v3:
        return None

    base = panel_cfg.xui_url.rstrip("/")
    hdrs = _headers(panel_cfg)
    session = await _open_session(panel_cfg)
    try:
        async with session.get(
            f"{base}/panel/api/xray/outbound/list", headers=hdrs
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    return data.get("obj") or []
            logger.warning(
                f"[backup] {panel_cfg.alias}: xray/outbound/list → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: outbounds error: {e}")
        return None
    finally:
        await session.close()


async def _fetch_routing(panel_cfg) -> Optional[dict]:
    """Скачивает routing-конфиг через GET /panel/api/routing/list (только v3).
    Панель хранит routing отдельно от xray-шаблона — шаблон его не содержит.
    Возвращает dict с полями: rules (list), domainStrategy (str), и т.д.
    """
    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    if not is_v3:
        return None

    base = panel_cfg.xui_url.rstrip("/")
    hdrs = _headers(panel_cfg)
    session = await _open_session(panel_cfg)
    try:
        async with session.get(
            f"{base}/panel/api/routing/list", headers=hdrs
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    return data.get("obj") or {}
            logger.warning(
                f"[backup] {panel_cfg.alias}: routing/list → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: routing error: {e}")
        return None
    finally:
        await session.close()


# ─── Cascade helpers ─────────────────────────────────────────────────────────

def _cascade_headers(cascade_cfg) -> dict:
    if cascade_cfg.api_key:
        return {"Authorization": f"Bearer {cascade_cfg.api_key}"}
    return {}


async def _cascade_session(cascade_cfg) -> aiohttp.ClientSession:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=ssl_ctx),
        timeout=aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10),
    )
    # Если только пароль — логинимся
    if not cascade_cfg.api_key and cascade_cfg.password:
        try:
            async with session.post(
                f"{cascade_cfg.url}/api/session",
                json={"username": cascade_cfg.username, "password": cascade_cfg.password},
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[backup] cascade {cascade_cfg.label}: login HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"[backup] cascade {cascade_cfg.label}: login error: {e}")
    return session


async def _cascade_get(session, cascade_cfg, path: str) -> Optional[dict | list]:
    """GET {cascade_url}{path} → распакованный obj (dict или list)."""
    hdrs = _cascade_headers(cascade_cfg)
    try:
        async with session.get(f"{cascade_cfg.url}{path}", headers=hdrs) as resp:
            if resp.status == 200:
                data = await resp.json()
                # Cascade возвращает именованную обёртку: { peers/interfaces/... : [...] }
                # или плоский dict для settings. Возвращаем как есть.
                return data
            logger.warning(f"[backup] cascade {cascade_cfg.label}: GET {path} → HTTP {resp.status}")
            return None
    except Exception as e:
        logger.warning(f"[backup] cascade {cascade_cfg.label}: GET {path} error: {e}")
        return None


async def _cascade_backup_binary(session, cascade_cfg) -> Optional[bytes]:
    """POST /api/system/backup → tar.gz (awg.db + ipset файлы)."""
    hdrs = _cascade_headers(cascade_cfg)
    try:
        async with session.post(
            f"{cascade_cfg.url}/api/system/backup",
            headers=hdrs,
            json={},
        ) as resp:
            if resp.status == 200:
                return await resp.read()
            logger.warning(f"[backup] cascade {cascade_cfg.label}: system/backup → HTTP {resp.status}")
            return None
    except Exception as e:
        logger.warning(f"[backup] cascade {cascade_cfg.label}: system/backup error: {e}")
        return None


async def _backup_cascade_server(cascade_cfg, out_dir: Path, summary: dict):
    """Собирает бэкап одного Cascade-сервера в out_dir (уже cascade/{label}/)."""
    label = cascade_cfg.label or cascade_cfg.cascade_id

    session = await _cascade_session(cascade_cfg)
    try:
        # 1. Системный бэкап (awg.db + ipset) — бинарный tar.gz
        db_data = await _cascade_backup_binary(session, cascade_cfg)
        if db_data:
            (out_dir / "system.tar.gz").write_bytes(db_data)
            summary[f"{label} cascade system"] = f"✅ {len(db_data) // 1024 + 1} КБ"
        else:
            summary[f"{label} cascade system"] = "⚠️ недоступен"

        # Вспомогательная функция: Cascade может вернуть голый список ИЛИ обёртку {key: [...]}
        def _unwrap(resp, key: str) -> list:
            if resp is None:
                return []
            if isinstance(resp, list):
                return resp
            if isinstance(resp, dict):
                return resp.get(key) or []
            return []

        # 2. Список интерфейсов
        ifaces_resp = await _cascade_get(session, cascade_cfg, "/api/tunnel-interfaces")
        ifaces = _unwrap(ifaces_resp, "interfaces")
        if ifaces_resp is not None:
            (out_dir / "interfaces.json").write_text(
                json.dumps(ifaces, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summary[f"{label} cascade interfaces"] = f"✅ {len(ifaces)} интерфейсов"
        else:
            summary[f"{label} cascade interfaces"] = "⚠️ не удалось получить"

        # 3. Пиры каждого интерфейса — подпапка peers/
        peers_dir = out_dir / "peers"
        peers_dir.mkdir(exist_ok=True)
        total_peers = 0
        for iface in ifaces:
            iface_id   = iface.get("id") or iface.get("name", "unknown")
            iface_name = (iface.get("name") or iface_id).replace("/", "_")
            peers_resp = await _cascade_get(
                session, cascade_cfg, f"/api/tunnel-interfaces/{iface_id}/peers"
            )
            peers = _unwrap(peers_resp, "peers")
            (peers_dir / f"{iface_name}_peers.json").write_text(
                json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            total_peers += len(peers)
        if ifaces:
            summary[f"{label} cascade peers"] = f"✅ {total_peers} пиров в {len(ifaces)} интерфейсах"

        # 4. Настройки (возвращает dict напрямую)
        settings = await _cascade_get(session, cascade_cfg, "/api/settings")
        if settings is not None:
            (out_dir / "settings.json").write_text(
                json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summary[f"{label} cascade settings"] = "✅"
        else:
            summary[f"{label} cascade settings"] = "⚠️ не удалось получить"

    finally:
        await session.close()


# ─── Ротация старых бэкапов ───────────────────────────────────────────────────

def _prune_old_backups():
    if not _BACKUP_ROOT.exists():
        return
    zips = sorted(
        [p for p in _BACKUP_ROOT.iterdir() if p.suffix == ".zip"],
        key=lambda p: p.stat().st_mtime,
    )
    for old in zips[: max(0, len(zips) - _MAX_BACKUPS)]:
        try:
            old.unlink()
            logger.info(f"[backup] удалён старый бэкап: {old.name}")
        except Exception as e:
            logger.warning(f"[backup] не удалось удалить {old}: {e}")


# ─── Главная функция ──────────────────────────────────────────────────────────

async def run_backup(panel_manager, common) -> tuple[Path, dict]:
    """
    Собирает полный бэкап всех данных.

    Параметры:
        panel_manager — config.panel_manager (ConfigManager)
        common        — config.common (CommonConfig)

    Возвращает:
        zip_path — Path к готовому .zip-файлу
        summary  — dict {метка: строка-статус} для отчёта в Telegram
    """
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = _BACKUP_ROOT / f"_tmp_{ts}"

    # Подпапки по группам
    dir_system  = work_dir / "system"
    dir_panels  = work_dir / "panels"
    dir_cascade = work_dir / "cascade"
    for d in (dir_system, dir_panels, dir_cascade):
        d.mkdir(parents=True, exist_ok=True)

    summary = {}

    # 1. Системные файлы → system/
    local_files = {
        "config.yaml":  _APP_ROOT / "config.yaml",
        "bot_users.db": Path(common.db_path),
        "apps.yaml":    _APP_ROOT / "data" / "apps.yaml",
    }
    for name, src in local_files.items():
        if src.exists():
            shutil.copy2(src, dir_system / name)
            size_kb = src.stat().st_size // 1024 + 1
            summary[name] = f"✅ {size_kb} КБ"
        else:
            summary[name] = "⚠️ не найден"

    # 2. Данные каждой панели → panels/{alias}/
    panels = panel_manager.get_all_panels()
    for panel_id, panel_cfg in panels.items():
        if not panel_cfg.enabled:
            continue
        alias = (panel_cfg.alias or panel_id).strip()
        safe  = alias.replace("/", "_").replace("\\", "_").replace(" ", "_")
        panel_dir = dir_panels / safe
        panel_dir.mkdir(exist_ok=True)
        p     = f"{safe}_3xui"   # префикс имён файлов внутри папки

        # x-ui.db
        db_data = await _fetch_xui_db(panel_cfg)
        if db_data:
            (panel_dir / f"{p}_xui.db").write_bytes(db_data)
            summary[f"{alias} x-ui.db"] = f"✅ {len(db_data) // 1024 + 1} КБ"
        else:
            summary[f"{alias} x-ui.db"] = "⚠️ недоступна"

        # inbounds + clients JSON
        inbounds = await _fetch_inbounds(panel_cfg)
        if inbounds is not None:
            (panel_dir / f"{p}_inbounds.json").write_text(
                json.dumps(inbounds, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            def _count_clients(ib: dict) -> int:
                if ib.get("clientStats"):
                    return len(ib["clientStats"])
                raw = ib.get("settings") or "{}"
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        raw = {}
                return len(raw.get("clients") or [])

            total_clients = sum(_count_clients(ib) for ib in inbounds)
            summary[f"{alias} inbounds"] = (
                f"✅ {len(inbounds)} инбаундов, {total_clients} клиентов"
            )
        else:
            summary[f"{alias} inbounds"] = "⚠️ не удалось получить"

        # clients export
        clients = await _fetch_clients(panel_cfg)
        if clients is not None:
            (panel_dir / f"{p}_clients.json").write_text(
                json.dumps(clients, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary[f"{alias} clients"] = f"✅ {len(clients)} клиентов"
        else:
            version = panel_cfg.xui_version or ""
            if version.startswith("3.") or version == "latest":
                summary[f"{alias} clients"] = "⚠️ не удалось получить"

        # xray конфиг (шаблон — без outbounds и routing, они хранятся отдельно)
        xray_cfg = await _fetch_xray_config(panel_cfg)
        if xray_cfg is not None:
            (panel_dir / f"{p}_xray_config.json").write_text(
                json.dumps(xray_cfg, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary[f"{alias} xray_config"] = "✅"
        else:
            version = panel_cfg.xui_version or ""
            if version.startswith("3.") or version == "latest":
                summary[f"{alias} xray_config"] = "⚠️ не удалось получить"

        # outbounds (хранятся отдельно от xray-шаблона)
        outbounds = await _fetch_outbounds(panel_cfg)
        if outbounds is not None:
            (panel_dir / f"{p}_outbounds.json").write_text(
                json.dumps(outbounds, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary[f"{alias} outbounds"] = f"✅ {len(outbounds)} outbounds"
        else:
            version = panel_cfg.xui_version or ""
            if version.startswith("3.") or version == "latest":
                summary[f"{alias} outbounds"] = "⚠️ не удалось получить"

        # routing (хранится отдельно от xray-шаблона)
        routing = await _fetch_routing(panel_cfg)
        if routing is not None:
            routing_rules = routing.get("rules") or []
            (panel_dir / f"{p}_routing.json").write_text(
                json.dumps(routing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary[f"{alias} routing"] = (
                f"✅ {len(routing_rules)} правил, "
                f"strategy: {routing.get('domainStrategy', '—')}"
            )
        else:
            version = panel_cfg.xui_version or ""
            if version.startswith("3.") or version == "latest":
                summary[f"{alias} routing"] = "⚠️ не удалось получить"

        # настройки панели
        settings = await _fetch_settings(panel_cfg)
        if settings is not None:
            (panel_dir / f"{p}_settings.json").write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary[f"{alias} settings"] = "✅"
        else:
            version = panel_cfg.xui_version or ""
            if version.startswith("3.") or version == "latest":
                summary[f"{alias} settings"] = "⚠️ не удалось получить"

        # outbound-подписки
        ob_subs = await _fetch_outbound_subs(panel_cfg)
        if ob_subs is not None:
            (panel_dir / f"{p}_outbound_subs.json").write_text(
                json.dumps(ob_subs, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary[f"{alias} outbound_subs"] = f"✅ {len(ob_subs)} подписок"
        else:
            version = panel_cfg.xui_version or ""
            if version.startswith("3.") or version == "latest":
                summary[f"{alias} outbound_subs"] = "⚠️ не удалось получить"

    # 3. Cascade-серверы → cascade/{label}/
    cascade_servers = panel_manager.get_cascade_servers()
    for cascade_cfg in cascade_servers:
        if not cascade_cfg.enabled:
            continue
        label_safe = (cascade_cfg.label or cascade_cfg.cascade_id).replace(
            "/", "_").replace("\\", "_").replace(" ", "_")
        cascade_dir = dir_cascade / label_safe
        cascade_dir.mkdir(exist_ok=True)
        await _backup_cascade_server(cascade_cfg, cascade_dir, summary)

    # 4. Упаковка в ZIP с сохранением структуры папок
    _BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    zip_path = _BACKUP_ROOT / f"awgxuibot_backup_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in work_dir.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(work_dir))

    # Удаляем временную папку — ZIP достаточно
    shutil.rmtree(work_dir, ignore_errors=True)

    # 4. Ротация: оставляем последние _MAX_BACKUPS архивов
    _prune_old_backups()

    size_kb = zip_path.stat().st_size // 1024 + 1
    logger.info(f"[backup] готов: {zip_path.name} ({size_kb} КБ)")
    return zip_path, summary
