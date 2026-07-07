"""
System Monitor - Мониторинг системных ресурсов (CPU, RAM, Disk)
Отслеживает использование ресурсов и отправляет уведомления при превышении порогов
"""

import asyncio
import logging
import psutil
from datetime import datetime
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)


class SystemMonitor:
    """
    Мониторинг системных ресурсов с уведомлениями
    
    Функции:
    - Проверка CPU, RAM, Disk каждые N секунд
    - Отправка уведомлений при превышении порогов (после 3 последовательных проверок)
    - Настраиваемые пороги для каждого ресурса
    - Защита от спама уведомлений
    """
    
    def __init__(self, config, bot, admin_ids: List[int]):
        """
        Инициализация монитора системы
        
        Args:
            config: Config instance
            bot: Telegram Bot instance
            admin_ids: Список ID администраторов для уведомлений
        """
        self.config = config
        self.bot = bot
        self.admin_ids = admin_ids
        
        # Параметры мониторинга
        self.check_interval = self._get_check_interval()
        self.threshold_check_count = 3  # Количество последовательных проверок перед отправкой уведомления
        
        # Контроль выполнения
        self.running = False
        self.task: Optional[asyncio.Task] = None
        
        # Защита от спама уведомлений - храним время последнего уведомления для каждого типа
        self.last_alert_time: Dict[str, Optional[datetime]] = {
            'cpu': None,
            'ram': None,
            'disk': None
        }
        self.alert_cooldown = 300  # 5 минут между повторными уведомлениями
        
        # Счетчики последовательных превышений порогов
        self.consecutive_threshold_exceeded: Dict[str, int] = {
            'cpu': 0,
            'ram': 0,
            'disk': 0
        }
        
        # Состояние превышения порогов (для отслеживания восстановления)
        self.threshold_exceeded: Dict[str, bool] = {
            'cpu': False,
            'ram': False,
            'disk': False
        }
        
        logger.info(f"🔍 SystemMonitor инициализирован:")
        logger.info(f"  - Интервал проверки: {self.check_interval}с")
        logger.info(f"  - Порог проверок для уведомления: {self.threshold_check_count}")
        logger.info(f"  - Cooldown уведомлений: {self.alert_cooldown}с")
        logger.info(f"  - Администраторы для уведомлений: {self.admin_ids} (всего: {len(self.admin_ids)})")
    
    def _get_check_interval(self) -> int:
        """Получить интервал проверки из конфигурации"""
        try:
            # Используем panel_check_interval из config.yaml
            if hasattr(self.config, 'common') and hasattr(self.config.common, 'panel_check_interval'):
                return int(self.config.common.panel_check_interval)
            else:
                logger.warning("panel_check_interval не найден в конфигурации. Используется значение по умолчанию: 30с")
                return 30
        except Exception as e:
            logger.warning(f"Ошибка получения интервала проверки: {e}. Используется значение по умолчанию: 30с")
            return 30
    
    async def start_monitoring(self):
        """Запуск фонового мониторинга"""
        if self.running:
            logger.warning("⚠️ Мониторинг системы уже запущен")
            return
        
        self.running = True
        logger.info("🚀 Запуск мониторинга системных ресурсов...")
        
        try:
            await self._monitoring_loop()
        except asyncio.CancelledError:
            logger.info("🛑 Мониторинг системы остановлен")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка в мониторинге системы: {e}", exc_info=True)
        finally:
            self.running = False
    
    async def stop_monitoring(self):
        """Остановка мониторинга"""
        if not self.running:
            return
        
        logger.info("🛑 Остановка мониторинга системы...")
        self.running = False
        
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        logger.info("✅ Мониторинг системы остановлен")
    
    async def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        logger.info(f"🔄 Цикл мониторинга системы запущен (интервал: {self.check_interval}с)")
        
        while self.running:
            try:
                # Обновляем интервал проверки (может быть изменен в настройках)
                self.check_interval = self._get_check_interval()
                
                # Получаем настройки уведомлений
                settings = self.config.users_db.get_all_notification_settings()
                cpu_alert_enabled = settings.get('cpu_alert', False)
                ram_alert_enabled = settings.get('ram_alert', False)
                disk_alert_enabled = settings.get('disk_alert', False)
                
                # Получаем пороги
                thresholds = self.config.users_db.get_all_thresholds()
                cpu_threshold = thresholds.get('cpu_threshold', 95.0)
                ram_threshold = thresholds.get('ram_threshold', 95.0)
                disk_threshold = thresholds.get('disk_threshold', 95.0)
                
                # Проверяем CPU
                if cpu_alert_enabled:
                    cpu_usage = psutil.cpu_percent(interval=1)
                    await self._check_threshold('cpu', cpu_usage, cpu_threshold, '💻 CPU')
                
                # Проверяем RAM
                if ram_alert_enabled:
                    ram = psutil.virtual_memory()
                    ram_usage = ram.percent
                    await self._check_threshold('ram', ram_usage, ram_threshold, '🧠 RAM')
                
                # Проверяем Disk
                if disk_alert_enabled:
                    disk = psutil.disk_usage('/')
                    disk_usage = disk.percent
                    await self._check_threshold('disk', disk_usage, disk_threshold, '💿 Диск')
                
            except Exception as e:
                logger.error(f"❌ Ошибка в цикле мониторинга системы: {e}", exc_info=True)
            
            # Ожидание перед следующей проверкой
            await asyncio.sleep(self.check_interval)
    
    async def _check_threshold(self, resource_type: str, current_value: float, threshold: float, display_name: str):
        """
        Проверка превышения порога для ресурса с трехкратной проверкой
        
        Args:
            resource_type: Тип ресурса ('cpu', 'ram', 'disk')
            current_value: Текущее значение использования (%)
            threshold: Пороговое значение (%)
            display_name: Отображаемое имя ресурса
        """
        exceeded = current_value >= threshold
        was_exceeded = self.threshold_exceeded.get(resource_type, False)
        consecutive_count = self.consecutive_threshold_exceeded.get(resource_type, 0)
        
        if exceeded:
            # Увеличиваем счетчик последовательных превышений
            self.consecutive_threshold_exceeded[resource_type] = consecutive_count + 1
            new_count = self.consecutive_threshold_exceeded[resource_type]
            
            if not was_exceeded:
                # Порог превышен, но еще не достигли порога проверок
                if new_count < self.threshold_check_count:
                    logger.warning(
                        f"⚠️ {display_name}: {current_value:.1f}% (порог: {threshold:.0f}%) "
                        f"[{new_count}/{self.threshold_check_count}]"
                    )
                elif new_count >= self.threshold_check_count:
                    # Достигли порога проверок - отправляем уведомление
                    self.threshold_exceeded[resource_type] = True
                    await self._send_alert(resource_type, display_name, current_value, threshold, exceeded=True)
                    logger.warning(
                        f"🚨 {display_name}: {current_value:.1f}% (порог: {threshold:.0f}%) "
                        f"[{new_count}/{self.threshold_check_count}] - УВЕДОМЛЕНИЕ ОТПРАВЛЕНО"
                    )
            else:
                # Порог все еще превышен - проверяем cooldown для повторного уведомления
                if new_count >= self.threshold_check_count:
                    await self._send_alert(resource_type, display_name, current_value, threshold, exceeded=True, repeat=True)
        else:
            # Порог не превышен - сбрасываем счетчик
            if consecutive_count > 0:
                logger.info(
                    f"✅ {display_name}: {current_value:.1f}% - счетчик сброшен "
                    f"(было {consecutive_count}/{self.threshold_check_count})"
                )
            self.consecutive_threshold_exceeded[resource_type] = 0
            
            if was_exceeded:
                # Ресурс восстановился после превышения порога
                self.threshold_exceeded[resource_type] = False
                await self._send_alert(resource_type, display_name, current_value, threshold, exceeded=False)
                logger.info(f"✅ {display_name}: восстановлен до {current_value:.1f}%")
    
    async def _send_alert(self, resource_type: str, display_name: str, current_value: float,
                         threshold: float, exceeded: bool, repeat: bool = False):
        """
        Отправка уведомления администраторам
        
        Args:
            resource_type: Тип ресурса
            display_name: Отображаемое имя
            current_value: Текущее значение
            threshold: Пороговое значение
            exceeded: True если порог превышен, False если восстановлен
            repeat: True если это повторное уведомление о превышении
        """
        # Проверка наличия администраторов
        if not self.admin_ids:
            logger.error(f"❌ Список администраторов пуст! Уведомление о {resource_type} не отправлено")
            return
        
        logger.info(f"📤 Подготовка уведомления о {resource_type} для {len(self.admin_ids)} администратор(ов): {self.admin_ids}")
        
        # Проверка cooldown
        now = datetime.now()
        last_alert = self.last_alert_time.get(resource_type)
        
        if repeat and last_alert:
            elapsed = (now - last_alert).total_seconds()
            if elapsed < self.alert_cooldown:
                # Слишком рано для повторного уведомления
                logger.debug(f"⏸️ Cooldown активен для {resource_type}: {elapsed:.0f}с / {self.alert_cooldown}с")
                return
        
        # Формируем сообщение
        if exceeded:
            emoji = "🚨" if current_value >= threshold + 5 else "⚠️"
            message = (
                f"{emoji} <b>ПРЕДУПРЕЖДЕНИЕ: Высокая загрузка</b>\n\n"
                f"{display_name}: <b>{current_value:.1f}%</b>\n"
                f"Порог: {threshold:.0f}%\n"
                f"Превышение: <b>+{current_value - threshold:.1f}%</b>\n\n"
                f"⏰ {now.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        else:
            message = (
                f"✅ <b>Ресурс восстановлен</b>\n\n"
                f"{display_name}: <b>{current_value:.1f}%</b>\n"
                f"Порог: {threshold:.0f}%\n\n"
                f"⏰ {now.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        
        # Обновляем время последнего уведомления
        self.last_alert_time[resource_type] = now
        
        # Отправляем уведомления всем администраторам
        sent_count = 0
        for admin_id in self.admin_ids:
            try:
                logger.info(f"📨 Отправка уведомления о {resource_type} администратору {admin_id}...")
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode="HTML"
                )
                sent_count += 1
                logger.info(f"✅ Уведомление о {resource_type} успешно отправлено администратору {admin_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления администратору {admin_id}: {e}", exc_info=True)
        
        if sent_count > 0:
            logger.info(f"📬 Уведомление о {resource_type} отправлено {sent_count}/{len(self.admin_ids)} администратор(ам)")
        else:
            logger.error(f"❌ Не удалось отправить уведомление о {resource_type} ни одному администратору!")
    
    def get_current_stats(self) -> Dict:
        """
        Получить текущую статистику системы
        
        Returns:
            Dict: Текущие значения CPU, RAM, Disk
        """
        try:
            cpu_usage = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return {
                'cpu': {
                    'usage': cpu_usage,
                    'cores': psutil.cpu_count()
                },
                'ram': {
                    'usage': ram.percent,
                    'total': ram.total,
                    'used': ram.used,
                    'available': ram.available
                },
                'disk': {
                    'usage': disk.percent,
                    'total': disk.total,
                    'used': disk.used,
                    'free': disk.free
                }
            }
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики системы: {e}")
            return {}
    
    def get_monitoring_status(self) -> Dict:
        """
        Получить текущий статус мониторинга
        
        Returns:
            Dict: Информация о состоянии мониторинга
        """
        settings = self.config.users_db.get_all_notification_settings()
        thresholds = self.config.users_db.get_all_thresholds()
        
        return {
            'running': self.running,
            'check_interval': self.check_interval,
            'threshold_check_count': self.threshold_check_count,
            'alert_cooldown': self.alert_cooldown,
            'settings': settings,
            'thresholds': thresholds,
            'consecutive_threshold_exceeded': self.consecutive_threshold_exceeded,
            'threshold_exceeded': self.threshold_exceeded,
            'last_alert_time': {
                k: v.isoformat() if v else None
                for k, v in self.last_alert_time.items()
            }
        }

# Made with Bob