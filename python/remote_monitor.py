"""
RemoteMonitor — мониторинг ресурсов всех панелей.
- panel0  : читает ресурсы локально через psutil
- panel1+ : опрашивает /panel/api/server/status (XUI API v3+)
Кеширует last_stats для UI и отправляет алерты по порогам из БД.
"""

import asyncio
import logging
import aiohttp
import ssl
from datetime import datetime
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)


def _fetch_local_stats() -> Optional[dict]:
    """
    Читает CPU/RAM/Disk локального сервера через psutil (для panel0).
    Возвращает {'cpu', 'ram', 'disk'} или None если psutil недоступен.
    """
    try:
        import psutil
        cpu  = psutil.cpu_percent(interval=0.5)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        return {
            'cpu':  round(cpu, 1),
            'ram':  round(ram.percent, 1),
            'disk': round(disk.percent, 1),
        }
    except Exception as e:
        logger.debug(f"RemoteMonitor: ошибка чтения локальных ресурсов: {e}")
        return None


async def _fetch_server_status(xui_url: str, api_token: str, timeout: int = 8) -> Optional[dict]:
    """
    GET /panel/api/server/status
    Возвращает {'cpu': float, 'ram': float, 'disk': float} или None при ошибке.
    """
    url = f"{xui_url.rstrip('/')}/panel/api/server/status"
    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    try:
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get('success'):
                    return None
                obj = data.get('obj', {})
                cpu = float(obj.get('cpu', 0))
                mem = obj.get('mem', {})
                disk_obj = obj.get('disk', {})
                mem_total   = mem.get('total', 0) or 1
                mem_used    = mem.get('current', 0)
                mem_percent = round(mem_used / mem_total * 100, 1)
                disk_total  = disk_obj.get('total', 0) or 1
                disk_used   = disk_obj.get('used', disk_obj.get('current', 0))
                disk_percent = round(disk_used / disk_total * 100, 1)
                return {
                    'cpu':  round(cpu, 1),
                    'ram':  mem_percent,
                    'disk': disk_percent,
                }
    except Exception as e:
        logger.debug(f"RemoteMonitor: ошибка запроса {url}: {e}")
        return None


class RemoteMonitor:
    """
    Мониторинг ресурсов удалённых панелей v3+ через XUI API.
    Один экземпляр опрашивает все подходящие панели поочерёдно.
    """

    def __init__(self, config, bot, admin_ids: List[int]):
        self.config     = config
        self.bot        = bot
        self.admin_ids  = admin_ids
        self.running    = False
        self.alert_cooldown       = 300   # сек между повторными уведомлениями
        self.threshold_check_count = 3    # сколько раз подряд нужно превысить

        # Состояние per-(panel_id, resource)
        self.last_alert_time:    Dict[str, Dict[str, Optional[datetime]]] = {}
        self.threshold_exceeded: Dict[str, Dict[str, bool]]               = {}
        self.consecutive_count:  Dict[str, Dict[str, int]]                = {}

        # Счётчик недоступности и флаг "панель была недоступна"
        self.unavail_count:    Dict[str, int]  = {}   # сколько раз подряд stats is None
        self.unavail_alerted:  Dict[str, bool] = {}   # уведомление о недоступности уже отправлено

        # Кеш последних значений для UI: {panel_id: {'cpu', 'ram', 'disk', 'updated_at'}}
        self.last_stats: Dict[str, dict] = {}

    # ── вспомогательные ─────────────────────────────────────────────────────

    def _get_all_monitored_panels(self) -> list:
        """
        Возвращает все панели у которых включён хотя бы один алерт.
        panel0  — локальная (psutil), включается всегда если есть хоть один алерт.
        panel1+ — только v3+ с заполненным api_token.
        """
        panels = self.config.panel_manager.get_all_panels()
        result = []
        for panel_id, cfg in panels.items():
            settings = self.config.users_db.get_panel_notification_settings(panel_id)
            has_alert = any([
                settings.get('cpu_alert',          False),
                settings.get('ram_alert',          False),
                settings.get('disk_alert',         False),
                settings.get('availability_alert', False),
            ])
            if not has_alert:
                continue
            if panel_id == "panel0":
                result.append((panel_id, cfg))
                continue
            if not getattr(cfg, 'xui_api_token', ''):
                logger.debug(f"RemoteMonitor: {panel_id} пропущен — нет api_token")
                continue
            if not (cfg.is_v3() if hasattr(cfg, 'is_v3') else False):
                logger.debug(f"RemoteMonitor: {panel_id} пропущен — не v3+")
                continue
            result.append((panel_id, cfg))
        return result

    def _get_remote_panels(self) -> list:
        """Устаревший метод — оставлен для обратной совместимости."""
        panels = self.config.panel_manager.get_all_panels()
        result = []
        for panel_id, cfg in panels.items():
            if panel_id == "panel0":
                continue
            if not getattr(cfg, 'xui_api_token', ''):
                continue
            if not (cfg.is_v3() if hasattr(cfg, 'is_v3') else False):
                continue
            result.append((panel_id, cfg))
        return result

    def _init_panel_state(self, panel_id: str):
        if panel_id not in self.last_alert_time:
            self.last_alert_time[panel_id]    = {'cpu': None, 'ram': None, 'disk': None}
            self.threshold_exceeded[panel_id] = {'cpu': False, 'ram': False, 'disk': False}
            self.consecutive_count[panel_id]  = {'cpu': 0, 'ram': 0, 'disk': 0}
            self.unavail_count[panel_id]      = 0
            self.unavail_alerted[panel_id]    = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start_monitoring(self):
        self.running = True
        logger.info("🚀 RemoteMonitor: запуск мониторинга удалённых панелей")
        try:
            await self._loop()
        except asyncio.CancelledError:
            logger.info("🛑 RemoteMonitor остановлен")
        finally:
            self.running = False

    async def stop_monitoring(self):
        self.running = False

    # ── основной цикл ────────────────────────────────────────────────────────

    async def _loop(self):
        while self.running:
            check_interval = getattr(self.config.common, 'panel_check_interval', 30)
            panels = self._get_all_monitored_panels()

            for panel_id, cfg in panels:
                self._init_panel_state(panel_id)

                # Сбор статистики: panel0 — psutil, остальные — XUI API
                if panel_id == "panel0":
                    stats = _fetch_local_stats()
                else:
                    stats = await _fetch_server_status(cfg.xui_url, cfg.xui_api_token)

                if stats is None:
                    logger.debug(f"RemoteMonitor: нет данных от {panel_id}")
                    # Доступность отслеживаем только для сетевых панелей
                    if panel_id != "panel0":
                        await self._check_availability(panel_id, cfg, available=False)
                    continue

                # Панель ответила — сбрасываем счётчик недоступности (только сетевые)
                if panel_id != "panel0":
                    await self._check_availability(panel_id, cfg, available=True)

                # Кешируем для UI
                self.last_stats[panel_id] = {
                    'cpu':        stats['cpu'],
                    'ram':        stats['ram'],
                    'disk':       stats['disk'],
                    'updated_at': datetime.now().strftime('%H:%M:%S'),
                }

                settings   = self.config.users_db.get_panel_notification_settings(panel_id)
                thresholds = self.config.users_db.get_panel_thresholds(panel_id)
                alias      = getattr(cfg, 'alias', panel_id) or panel_id

                checks = [
                    ('cpu',  stats['cpu'],  thresholds.get('cpu_threshold',  95.0), settings.get('cpu_alert',  False), '💻 CPU'),
                    ('ram',  stats['ram'],  thresholds.get('ram_threshold',  95.0), settings.get('ram_alert',  False), '🧠 RAM'),
                    ('disk', stats['disk'], thresholds.get('disk_threshold', 95.0), settings.get('disk_alert', False), '💿 Диск'),
                ]

                for resource, value, threshold, enabled, label in checks:
                    if not enabled:
                        self.consecutive_count[panel_id][resource] = 0
                        continue
                    await self._check(panel_id, alias, resource, value, threshold, label)

            await asyncio.sleep(check_interval)

    # ── логика доступности ──────────────────────────────────────────────────

    async def _check_availability(self, panel_id: str, cfg, available: bool):
        """Отслеживает недоступность панели и отправляет уведомление после threshold_check_count неудач."""
        self._init_panel_state(panel_id)
        settings = self.config.users_db.get_panel_notification_settings(panel_id)
        if not settings.get('availability_alert', False):
            # Алерт выключен — просто сбрасываем счётчик при восстановлении
            if available:
                self.unavail_count[panel_id] = 0
            return

        alias = getattr(cfg, 'alias', panel_id) or panel_id

        if not available:
            self.unavail_count[panel_id] += 1
            count = self.unavail_count[panel_id]
            logger.debug(f"RemoteMonitor: {panel_id} недоступна ({count}/{self.threshold_check_count})")
            if count >= self.threshold_check_count and not self.unavail_alerted[panel_id]:
                self.unavail_alerted[panel_id] = True
                await self._send_availability_alert(panel_id, alias, available=False)
        else:
            if self.unavail_alerted[panel_id]:
                # Панель восстановилась — шлём уведомление о восстановлении
                self.unavail_alerted[panel_id] = False
                await self._send_availability_alert(panel_id, alias, available=True)
            self.unavail_count[panel_id] = 0

    async def _send_availability_alert(self, panel_id: str, alias: str, available: bool):
        """Отправляет уведомление о доступности панели."""
        now = datetime.now()
        if available:
            text = (
                f"✅ <b>Панель восстановлена</b>\n\n"
                f"🖥️ <b>{panel_id} · {alias}</b>\n\n"
                f"⏰ {now.strftime('%H:%M:%S')}"
            )
        else:
            text = (
                f"🔴 <b>ПАНЕЛЬ НЕДОСТУПНА</b>\n\n"
                f"🖥️ <b>{panel_id} · {alias}</b>\n\n"
                f"Не отвечает {self.threshold_check_count} раза подряд\n"
                f"⏰ {now.strftime('%H:%M:%S')}"
            )
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"RemoteMonitor: ошибка отправки availability алерта {admin_id}: {e}")

    # ── логика превышения порогов ────────────────────────────────────────────

    async def _check(self, panel_id: str, alias: str, resource: str,
                     value: float, threshold: float, label: str):
        exceeded     = value >= threshold
        was_exceeded = self.threshold_exceeded[panel_id][resource]
        count        = self.consecutive_count[panel_id][resource]

        if exceeded:
            self.consecutive_count[panel_id][resource] = count + 1
            new_count = self.consecutive_count[panel_id][resource]
            if not was_exceeded and new_count >= self.threshold_check_count:
                self.threshold_exceeded[panel_id][resource] = True
                await self._send_alert(panel_id, alias, resource, label, value, threshold, exceeded=True)
            elif was_exceeded:
                await self._send_alert(panel_id, alias, resource, label, value, threshold, exceeded=True, repeat=True)
        else:
            self.consecutive_count[panel_id][resource] = 0
            if was_exceeded:
                self.threshold_exceeded[panel_id][resource] = False
                await self._send_alert(panel_id, alias, resource, label, value, threshold, exceeded=False)

    # ── отправка уведомлений ─────────────────────────────────────────────────

    async def _send_alert(self, panel_id: str, alias: str, resource: str, label: str,
                          value: float, threshold: float, exceeded: bool, repeat: bool = False):
        now  = datetime.now()
        last = self.last_alert_time[panel_id][resource]
        if repeat and last and (now - last).total_seconds() < self.alert_cooldown:
            return

        self.last_alert_time[panel_id][resource] = now

        if exceeded:
            emoji = "🚨" if value >= threshold + 5 else "⚠️"
            text = (
                f"{emoji} <b>ВЫСОКАЯ ЗАГРУЗКА {label.split()[1]}</b>\n\n"
                f"🖥️ <b>{panel_id} · {alias}</b>\n\n"
                f"{label}: <b>{value:.1f}%</b>\n"
                f"Порог: {threshold:.0f}%  ·  Превышение: <b>+{value - threshold:.1f}%</b>\n\n"
                f"⏰ {now.strftime('%H:%M:%S')}"
            )
        else:
            text = (
                f"✅ <b>{label.split()[1]} восстановлена</b>\n\n"
                f"🖥️ <b>{panel_id} · {alias}</b>\n\n"
                f"{label}: <b>{value:.1f}%</b>\n"
                f"Порог: {threshold:.0f}%\n\n"
                f"⏰ {now.strftime('%H:%M:%S')}"
            )

        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"RemoteMonitor: ошибка отправки алерта {admin_id}: {e}")
