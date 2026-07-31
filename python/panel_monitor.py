"""
Panel Monitor - Автоматический мониторинг и переключение панелей
Отслеживает доступность панелей и автоматически переключается на резервные при сбоях
"""

import asyncio
import logging
import sqlite3
from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PanelState:
    """Состояние панели для мониторинга"""
    panel_id: str
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_check: Optional[datetime] = None
    is_available: bool = True
    last_status_change: Optional[datetime] = None
    # История обрывов: список (datetime начала обрыва)
    outage_events: List[datetime] = field(default_factory=list)


class PanelMonitor:
    """
    Мониторинг доступности панелей с автоматическим failover
    
    Функции:
    - Проверка текущей панели каждые N секунд
    - Переключение после N последовательных неудач на следующую доступную панель
    - Поддержка локальных панелей (v2.x и v3.x) и удаленных панелей (только v3.x)
    - Уведомления администраторов о переключениях
    """
    
    def __init__(self, config_manager, bot, admin_ids: List[int]):
        """
        Инициализация монитора панелей
        
        Args:
            config_manager: ConfigManager instance
            bot: Telegram Bot instance
            admin_ids: Список ID администраторов для уведомлений
        """
        self.config_manager = config_manager
        self.bot = bot
        self.admin_ids = admin_ids
        self.db_path = config_manager.common.db_path
        
        # Параметры мониторинга из конфигурации
        self.enabled = config_manager.common.panel_monitoring_enabled
        self.check_interval = config_manager.common.panel_check_interval
        self.failure_threshold = config_manager.common.panel_failure_threshold
        self.check_timeout = config_manager.common.panel_check_timeout
        
        # Состояние панелей
        self.panel_states: Dict[str, PanelState] = {}
        self._initialize_panel_states()
        self._load_outages_from_db()
        
        # Контроль выполнения
        self.running = False
        self.task: Optional[asyncio.Task] = None
        
        # ID панели по умолчанию для восстановления
        self.default_panel_id = config_manager.default_panel_id
        
        # Защита от спама уведомлений
        self.last_notification_time: Optional[datetime] = None
        self.notification_cooldown = 60  # секунды
        
        logger.info(f"🔍 PanelMonitor инициализирован:")
        logger.info(f"  - Мониторинг: {'✅ Включен' if self.enabled else '❌ Отключен'}")
        logger.info(f"  - Интервал проверки: {self.check_interval}с")
        logger.info(f"  - Порог неудач: {self.failure_threshold}")
        logger.info(f"  - Поддержка: локальные панели (v2+v3), удаленные (только v3)")
    
    def _initialize_panel_states(self):
        """Инициализация состояний всех панелей"""
        panels = self.config_manager.get_all_panels()
        for panel_id in panels.keys():
            self.panel_states[panel_id] = PanelState(panel_id=panel_id)
        logger.info(f"📊 Инициализировано состояний панелей: {len(self.panel_states)}")

    def _load_outages_from_db(self):
        """Загрузка истории обрывов из БД при старте"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT panel_id, started_at FROM panel_outages ORDER BY started_at"
                ).fetchall()
            for panel_id, started_at_str in rows:
                if panel_id not in self.panel_states:
                    self.panel_states[panel_id] = PanelState(panel_id=panel_id)
                dt = None
                for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                            '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
                    try:
                        dt = datetime.strptime(started_at_str, fmt)
                        break
                    except ValueError:
                        continue
                if dt is None:
                    logger.warning(f"⚠️ Не удалось распарсить дату обрыва: {started_at_str!r}")
                    continue
                self.panel_states[panel_id].outage_events.append(dt)
            logger.info(f"📂 Загружено обрывов из БД: {len(rows)}")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось загрузить историю обрывов: {e}")

    def _save_outage_to_db(self, panel_id: str, started_at: datetime):
        """Сохранить обрыв в БД"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO panel_outages (panel_id, started_at) VALUES (?, ?)",
                    (panel_id, started_at.isoformat())
                )
        except Exception as e:
            logger.warning(f"⚠️ Не удалось сохранить обрыв в БД: {e}")
    
    async def start_monitoring(self):
        """Запуск фонового мониторинга"""
        if not self.enabled:
            logger.info("⏸️ Мониторинг панелей отключен в конфигурации")
            return
        
        if self.running:
            logger.warning("⚠️ Мониторинг уже запущен")
            return
        
        self.running = True
        logger.info("🚀 Запуск мониторинга панелей...")
        
        try:
            await self._monitoring_loop()
        except asyncio.CancelledError:
            logger.info("🛑 Мониторинг панелей остановлен")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка в мониторинге: {e}", exc_info=True)
        finally:
            self.running = False
    
    async def stop_monitoring(self):
        """Остановка мониторинга"""
        if not self.running:
            return
        
        logger.info("🛑 Остановка мониторинга панелей...")
        self.running = False
        
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        logger.info("✅ Мониторинг панелей остановлен")
    
    async def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        logger.info(f"🔄 Цикл мониторинга запущен (интервал: {self.check_interval}с)")
        
        while self.running:
            try:
                current_panel_id = self.config_manager.get_current_panel_id()
                
                if not current_panel_id:
                    logger.warning("⚠️ Текущая панель не определена")
                    await asyncio.sleep(self.check_interval)
                    continue
                
                # Проверка текущей панели
                is_available = await self._check_current_panel(current_panel_id)
                
                if is_available:
                    await self._handle_panel_available(current_panel_id)
                else:
                    await self._handle_panel_unavailable(current_panel_id)

                # Также проверяем все остальные (не текущие) панели для учёта их обрывов
                for panel_id, state in self.panel_states.items():
                    if panel_id == current_panel_id:
                        continue
                    panel_config = self.config_manager.get_panel(panel_id)
                    if not panel_config or not getattr(panel_config, 'enabled', True):
                        continue
                    try:
                        available = await self.config_manager.check_panel_status(panel_config)
                        state.last_check = datetime.now()
                    except Exception:
                        available = False
                    if available:
                        if not state.is_available:
                            logger.info(f"✅ Панель {panel_id} восстановлена")
                            state.is_available = True
                            state.last_status_change = datetime.now()
                        state.consecutive_failures = 0
                    else:
                        state.consecutive_failures += 1
                        if state.is_available:
                            state.is_available = False
                            now = datetime.now()
                            state.last_status_change = now
                            state.outage_events.append(now)
                            self._save_outage_to_db(panel_id, now)
                            logger.warning(f"⚠️ Панель {panel_id} (резервная) стала недоступна — обрыв зафиксирован")
                
            except Exception as e:
                logger.error(f"❌ Ошибка в цикле мониторинга: {e}", exc_info=True)
            
            # Ожидание перед следующей проверкой
            await asyncio.sleep(self.check_interval)
    
    async def _check_current_panel(self, panel_id: str) -> bool:
        """
        Проверка доступности текущей панели
        
        Returns:
            bool: True если панель доступна
        """
        panel_config = self.config_manager.get_panel(panel_id)
        if not panel_config:
            logger.error(f"❌ Панель {panel_id} не найдена в конфигурации")
            return False
        
        try:
            is_available = await self.config_manager.check_panel_status(panel_config)
            
            # Обновляем время последней проверки
            if panel_id in self.panel_states:
                self.panel_states[panel_id].last_check = datetime.now()
            
            return is_available
            
        except Exception as e:
            logger.error(f"❌ Ошибка проверки панели {panel_id}: {e}")
            return False
    
    async def _handle_panel_available(self, panel_id: str):
        """Обработка доступной панели"""
        if panel_id not in self.panel_states:
            return
        
        state = self.panel_states[panel_id]
        state.consecutive_failures = 0
        state.consecutive_successes += 1
        
        # Логируем только изменения статуса
        if not state.is_available:
            logger.info(f"✅ Панель {panel_id} восстановлена")
            state.is_available = True
            state.last_status_change = datetime.now()
        
        # Детальное логирование только для отладки
        if state.consecutive_successes <= 3:
            logger.debug(f"✅ Панель {panel_id}: доступна")
    
    async def _handle_panel_unavailable(self, panel_id: str):
        """Обработка недоступной панели"""
        if panel_id not in self.panel_states:
            return
        
        state = self.panel_states[panel_id]
        state.consecutive_failures += 1
        state.consecutive_successes = 0
        
        failures = state.consecutive_failures
        
        logger.warning(f"⚠️ Панель {panel_id} недоступна ({failures}/{self.failure_threshold})")
        
        # Записываем начало нового обрыва при первом сбое в новой серии (failures == 1).
        # Условие по is_available недостаточно: после failover is_available остаётся False,
        # поэтому повторные отключения никогда не фиксировались.
        if failures == 1:
            now = datetime.now()
            state.is_available = False
            state.last_status_change = now
            state.outage_events.append(now)
            self._save_outage_to_db(panel_id, now)
        
        # Проверяем достижение порога для failover
        if failures >= self.failure_threshold:
            logger.error(f"❌ Панель {panel_id} недоступна {failures} раз подряд. Инициируем failover...")
            await self._initiate_failover(panel_id)
            # Сбрасываем счетчик после попытки failover
            state.consecutive_failures = 0
    
    async def _initiate_failover(self, failed_panel_id: str):
        """
        Инициировать переключение на резервную панель
        
        Args:
            failed_panel_id: ID недоступной панели
        """
        logger.info(f"🔄 Начало процесса failover с панели {failed_panel_id}")
        
        # Получаем конфигурацию недоступной панели для отображения алиаса
        failed_panel_config = self.config_manager.get_panel(failed_panel_id)
        failed_panel_alias = failed_panel_config.alias if failed_panel_config else failed_panel_id
        
        # Получаем список панелей по приоритету (порядок в config.yaml)
        panels_by_priority = await self._get_panels_by_priority()
        
        # Ищем первую доступную панель
        for panel_id in panels_by_priority:
            if panel_id == failed_panel_id:
                continue
            
            panel_config = self.config_manager.get_panel(panel_id)
            if not panel_config or not panel_config.enabled:
                logger.debug(f"⏭️ Пропуск панели {panel_id} (отключена или не найдена)")
                continue
            
            # panel0 — локальная, всегда доступна для failover (работает через БД)
            if panel_id == "panel0":
                logger.debug(f"✅ Локальная панель {panel_id} доступна для failover")
            else:
                # Для удалённых панелей проверяем версию (нужен API v3+)
                is_v3_or_higher = panel_config.is_v3() if hasattr(panel_config, 'is_v3') else False
                
                if not is_v3_or_higher:
                    logger.debug(f"⏭️ Пропуск панели {panel_id} (v2.x, удалённая, нет API)")
                    continue
            
            # Проверяем доступность
            logger.info(f"🔍 Проверка доступности панели {panel_id}...")
            is_available = await self.config_manager.check_panel_status(panel_config)
            
            if is_available:
                # Переключаемся на доступную панель
                logger.info(f"✅ Найдена доступная панель: {panel_id}")
                success = await self._switch_to_panel(panel_id, failed_panel_id)
                
                if success:
                    await self._notify_admins(
                        f"🔄 <b>Автоматическое переключение панели</b>\n\n"
                        f"❌ Недоступна: <b>{failed_panel_alias}</b> <code>{failed_panel_id}</code>\n"
                        f"✅ Переключено на: <b>{panel_config.alias}</b> <code>{panel_id}</code>\n\n"
                        f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    return
                else:
                    logger.error(f"❌ Не удалось переключиться на панель {panel_id}")
            else:
                logger.warning(f"⚠️ Панель {panel_id} также недоступна")
        
        # Все панели недоступны
        logger.critical("🚨 ВСЕ ПАНЕЛИ НЕДОСТУПНЫ!")
        # Notification disabled by user request
        # await self._notify_admins(
        #     f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА</b>\n\n"
        #     f"❌ Все панели недоступны!\n"
        #     f"⚠️ Требуется немедленное вмешательство.\n\n"
        #     f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        # )
    
    async def _switch_to_panel(self, target_panel_id: str, from_panel_id: str) -> bool:
        """
        Переключение на указанную панель
        
        Args:
            target_panel_id: ID панели для переключения
            from_panel_id: ID текущей панели
            
        Returns:
            bool: True если переключение успешно
        """
        try:
            logger.info(f"🔄 Переключение: {from_panel_id} → {target_panel_id}")
            
            # Переключаем панель в конфигурации
            if not self.config_manager.switch_panel(target_panel_id):
                logger.error(f"❌ Не удалось переключить панель на {target_panel_id}")
                return False
            
            # Создаем новый XUI config из панели
            new_xui_config = self.config_manager.create_xui_config_from_panel(target_panel_id)
            if not new_xui_config:
                logger.error(f"❌ Не удалось создать XUI config для панели {target_panel_id}")
                return False
            
            # Обновляем конфигурацию в глобальном config объекте
            # Это необходимо для того, чтобы бот использовал новую панель
            from config import config
            config.xui = new_xui_config
            config.refresh_vpn_config()
            
            logger.info(f"✅ Успешно переключено на панель {target_panel_id}")
            
            # Сбрасываем счетчики для новой панели
            if target_panel_id in self.panel_states:
                self.panel_states[target_panel_id].consecutive_failures = 0
                self.panel_states[target_panel_id].consecutive_successes = 0
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка переключения панели: {e}", exc_info=True)
            return False
    
    async def _get_panels_by_priority(self) -> List[str]:
        """
        Получить список панелей в порядке приоритета
        Порядок определяется последовательностью в config.yaml
        
        Returns:
            List[str]: Список panel_id в порядке приоритета
        """
        panels = self.config_manager.get_all_panels()
        # Возвращаем ключи в том порядке, в котором они определены
        return list(panels.keys())
    
    async def _notify_admins(self, message: str):
        """
        Отправить уведомление всем администраторам
        
        Args:
            message: Текст уведомления (поддерживает HTML)
        """
        # Проверка cooldown для предотвращения спама
        now = datetime.now()
        if self.last_notification_time:
            elapsed = (now - self.last_notification_time).total_seconds()
            if elapsed < self.notification_cooldown:
                logger.debug(f"⏸️ Уведомление пропущено (cooldown: {elapsed:.0f}с/{self.notification_cooldown}с)")
                return
        
        self.last_notification_time = now
        
        # Отправка уведомлений всем администраторам
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode="HTML"
                )
                logger.info(f"📨 Уведомление отправлено администратору {admin_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления администратору {admin_id}: {e}")
    
    def get_monitoring_status(self) -> Dict:
        """
        Получить текущий статус мониторинга
        
        Returns:
            Dict: Информация о состоянии мониторинга
        """
        return {
            'enabled': self.enabled,
            'running': self.running,
            'check_interval': self.check_interval,
            'failure_threshold': self.failure_threshold,
            'current_panel_id': self.config_manager.get_current_panel_id(),
            'panel_states': {
                panel_id: {
                    'consecutive_failures': state.consecutive_failures,
                    'consecutive_successes': state.consecutive_successes,
                    'is_available': state.is_available,
                    'last_check': state.last_check.isoformat() if state.last_check else None,
                    'last_status_change': state.last_status_change.isoformat() if state.last_status_change else None
                }
                for panel_id, state in self.panel_states.items()
            }
        }

# Made with Bob
