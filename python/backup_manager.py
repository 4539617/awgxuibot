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


async def _fetch_outbounds(panel_cfg) -> Optional[list]:
    """Скачивает список исходящих (outbounds) через GET /panel/api/xray/ (только v3)."""
    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    if not is_v3:
        return None  # v2 не поддерживает этот endpoint

    base = panel_cfg.xui_url.rstrip("/")
    hdrs = _headers(panel_cfg)
    session = await _open_session(panel_cfg)
    try:
        async with session.get(
            f"{base}/panel/api/xray/", headers=hdrs
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    xray_config = data.get("obj") or {}
                    return xray_config.get("outbounds") or []
            logger.warning(
                f"[backup] {panel_cfg.alias}: xray/ → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: outbounds error: {e}")
        return None
    finally:
        await session.close()


async def _fetch_clients(panel_cfg) -> Optional[list]:
    """Скачивает полный список клиентов через /panel/api/clients/list (только v3).
    Возвращает [{client: {...}, inboundIds: [...]}, ...] — содержит поля
    password, resetDay, limitHwid, inboundIds которых нет внутри inbound.settings.
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
            f"{base}/panel/api/clients/list", headers=hdrs
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    return data.get("obj") or []
            logger.warning(
                f"[backup] {panel_cfg.alias}: clients/list → HTTP {resp.status}"
            )
            return None
    except Exception as e:
        logger.warning(f"[backup] {panel_cfg.alias}: clients/list error: {e}")
        return None
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
    work_dir.mkdir(parents=True, exist_ok=True)
    summary = {}

    # 1. Локальные файлы
    local_files = {
        "config.yaml":  _APP_ROOT / "config.yaml",
        "bot_users.db": Path(common.db_path),
        "apps.yaml":    _APP_ROOT / "data" / "apps.yaml",
    }
    for name, src in local_files.items():
        if src.exists():
            shutil.copy2(src, work_dir / name)
            size_kb = src.stat().st_size // 1024 + 1
            summary[name] = f"✅ {size_kb} КБ"
        else:
            summary[name] = "⚠️ не найден"

    # 2. Данные каждой панели
    panels = panel_manager.get_all_panels()
    for panel_id, panel_cfg in panels.items():
        if not panel_cfg.enabled:
            continue
        alias = (panel_cfg.alias or panel_id).strip()
        safe  = alias.replace("/", "_").replace("\\", "_").replace(" ", "_")

        # x-ui.db
        db_data = await _fetch_xui_db(panel_cfg)
        if db_data:
            (work_dir / f"{safe}_xui.db").write_bytes(db_data)
            summary[f"{alias} x-ui.db"] = f"✅ {len(db_data) // 1024 + 1} КБ"
        else:
            summary[f"{alias} x-ui.db"] = "⚠️ недоступна"

        # inbounds + clients JSON
        inbounds = await _fetch_inbounds(panel_cfg)
        if inbounds is not None:
            (work_dir / f"{safe}_inbounds.json").write_text(
                json.dumps(inbounds, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            def _count_clients(ib: dict) -> int:
                # clientStats есть в некоторых версиях API
                if ib.get("clientStats"):
                    return len(ib["clientStats"])
                # settings — строка JSON или уже dict
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

        # clients list — отдельный файл (только v3: содержит password, inboundIds, limitHwid)
        clients = await _fetch_clients(panel_cfg)
        if clients is not None:
            (work_dir / f"{safe}_clients.json").write_text(
                json.dumps(clients, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary[f"{alias} clients"] = f"✅ {len(clients)} клиентов"
        else:
            version = panel_cfg.xui_version or ""
            if version.startswith("3.") or version == "latest":
                summary[f"{alias} clients"] = "⚠️ не удалось получить"
            # v2 — молчим, endpoint не поддерживается

        # outbounds — исходящие подключения (только v3)
        outbounds = await _fetch_outbounds(panel_cfg)
        if outbounds is not None:
            (work_dir / f"{safe}_outbounds.json").write_text(
                json.dumps(outbounds, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary[f"{alias} outbounds"] = f"✅ {len(outbounds)} исходящих"
        else:
            version = panel_cfg.xui_version or ""
            if version.startswith("3.") or version == "latest":
                summary[f"{alias} outbounds"] = "⚠️ не удалось получить"
            # v2 — молчим

    # 3. Упаковка в ZIP
    _BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    zip_path = _BACKUP_ROOT / f"backup_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(work_dir.iterdir()):
            zf.write(f, arcname=f.name)

    # Удаляем временную папку — ZIP достаточно
    shutil.rmtree(work_dir, ignore_errors=True)

    # 4. Ротация: оставляем последние _MAX_BACKUPS архивов
    _prune_old_backups()

    size_kb = zip_path.stat().st_size // 1024 + 1
    logger.info(f"[backup] готов: {zip_path.name} ({size_kb} КБ)")
    return zip_path, summary
