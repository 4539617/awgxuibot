# config.py - Multi-Server Configuration Manager
import os
import sys
import shutil
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import sqlite3
import yaml
import aiohttp
import asyncio
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class CommonConfig:
    """Общие параметры для всех панелей"""
    xui_bot_token: str
    awg_bot_token: str = ""
    admin_ids: List[int] = field(default_factory=list)
    server_port: int = 443
    api_timeout: int = 30
    xhttp_mode: str = "auto"
    tls_fingerprint: str = "edge"
    tls_alpn: str = "http/1.1"
    max_traffic_gb: int = 1000
    max_days: int = 3650
    min_days: int = 1
    default_traffic_gb: int = 100
    default_days: int = 30
    db_path: str = "/app/data/bot_users.db"
    db_backup_enabled: bool = True
    db_backup_interval: int = 24
    log_level: str = "INFO"
    log_file_enabled: bool = True
    log_file_path: str = "/app/logs/bot.log"
    log_max_size_mb: int = 10
    log_backup_count: int = 5
    allow_user_dns_queries: bool = False


@dataclass
class PanelConfig:
    """Конфигурация конкретной панели"""
    panel_id: str
    alias: str
    enabled: bool
    is_local: bool = False
    xui_version: str = "latest"
    xui_url: str = ""
    xui_username: str = ""
    xui_password: str = ""
    xui_api_token: str = ""
    inbound_id: int = 1
    xui_db_path: str = "/etc/x-ui/x-ui.db"
    server_address: str = ""
    server_ip: str = ""
    transport: str = "tcp"
    security: str = "tls"
    tls_sni: str = ""
    tls_fingerprint: str = "chrome"
    reality_sni: str = ""
    reality_fingerprint: str = "chrome"
    reality_public_key: str = ""
    reality_private_key: str = ""
    reality_short_id: str = ""
    
    def is_v2(self) -> bool:
        """Проверка является ли версия 2.x"""
        return self.xui_version.startswith("2.")
    
    def is_v3(self) -> bool:
        """Проверка является ли версия 3.x или latest"""
        return self.xui_version.startswith("3.") or self.xui_version == "latest"
    
    def is_v3_new_api(self) -> bool:
        """Проверка использует ли версия новый API v3"""
        return self.xui_version == "latest" or self.xui_version.startswith("3.")
    
    def get_sni(self) -> str:
        """Получить SNI в зависимости от типа безопасности"""
        return self.tls_sni if self.security == "tls" else self.reality_sni
    
    def get_fingerprint(self) -> str:
        """Получить fingerprint в зависимости от типа безопасности"""
        return self.tls_fingerprint if self.security == "tls" else self.reality_fingerprint


class ConfigManager:
    """Менеджер конфигурации из config.yaml"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.common: Optional[CommonConfig] = None
        self.panels: Dict[str, PanelConfig] = {}
        self.default_panel_id: Optional[str] = None
        self._load_config()
    
    def _load_config(self):
        """Загрузить конфигурацию из YAML файла с автоматической миграцией"""
        logger.info(f"🔍 Загрузка конфигурации из: {self.config_path.absolute()}")
        
        # Проверяем наличие config.yaml
        if not self.config_path.exists():
            logger.warning(f"⚠️ Файл {self.config_path} не найден")
            
            # Проверяем наличие .env для автоматической миграции
            env_path = Path('.env')
            if env_path.exists():
                logger.info("🔄 Обнаружен .env файл, запуск автоматической миграции...")
                
                # Выполняем миграцию
                if self._migrate_env_to_yaml():
                    logger.info("✅ Миграция завершена успешно!")
                    logger.info(f"📄 Создан файл: {self.config_path}")
                    # Загружаем созданный config.yaml
                    return self._load_config()
                else:
                    logger.error("❌ Ошибка миграции")
                    raise FileNotFoundError(
                        "❌ Не удалось выполнить миграцию .env → config.yaml\n"
                        "💡 Проверьте .env файл или создайте config.yaml вручную из config.yaml.example"
                    )
            else:
                # Нет ни config.yaml, ни .env
                raise FileNotFoundError(
                    "❌ Не найден config.yaml\n"
                    "💡 Создайте config.yaml из config.yaml.example\n"
                    "💡 Или поместите .env файл для автоматической миграции"
                )
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data:
                logger.error("❌ config.yaml пустой")
                raise ValueError("config.yaml пустой или содержит некорректные данные")
            
            # Загружаем общие параметры
            common_data = data.get('common', {})
            self.common = CommonConfig(
                xui_bot_token=common_data.get('xui_bot_token', ''),
                awg_bot_token=common_data.get('awg_bot_token', ''),
                admin_ids=common_data.get('admin_ids', []),
                server_port=common_data.get('server_port', 443),
                api_timeout=common_data.get('api_timeout', 30),
                xhttp_mode=common_data.get('xhttp_mode', 'auto'),
                tls_fingerprint=common_data.get('tls_fingerprint', 'edge'),
                tls_alpn=common_data.get('tls_alpn', 'http/1.1'),
                max_traffic_gb=common_data.get('max_traffic_gb', 1000),
                max_days=common_data.get('max_days', 3650),
                min_days=common_data.get('min_days', 1),
                default_traffic_gb=common_data.get('default_traffic_gb', 100),
                default_days=common_data.get('default_days', 30),
                db_path=common_data.get('db_path', '/app/data/bot_users.db'),
                db_backup_enabled=common_data.get('db_backup_enabled', True),
                db_backup_interval=common_data.get('db_backup_interval', 24),
                log_level=common_data.get('log_level', 'INFO'),
                log_file_enabled=common_data.get('log_file_enabled', True),
                log_file_path=common_data.get('log_file_path', '/app/logs/bot.log'),
                log_max_size_mb=common_data.get('log_max_size_mb', 10),
                log_backup_count=common_data.get('log_backup_count', 5),
                allow_user_dns_queries=common_data.get('allow_user_dns_queries', False)
            )
            
            # Загружаем панели
            panels_data = data.get('panels', {})
            for panel_id, panel_data in panels_data.items():
                self.panels[panel_id] = PanelConfig(
                    panel_id=panel_id,
                    alias=panel_data.get('alias', panel_id),
                    enabled=panel_data.get('enabled', True),
                    xui_version=panel_data.get('xui_version', '3.3.1'),
                    xui_url=panel_data.get('xui_url', ''),
                    xui_username=panel_data.get('xui_username', ''),
                    xui_password=panel_data.get('xui_password', ''),
                    xui_api_token=panel_data.get('xui_api_token', ''),
                    inbound_id=panel_data.get('inbound_id', 1),
                    xui_db_path=panel_data.get('xui_db_path', '/etc/x-ui/x-ui.db'),
                    server_address=panel_data.get('server_address', ''),
                    server_ip=panel_data.get('server_ip', ''),
                    transport=panel_data.get('transport', 'xhttp'),
                    security=panel_data.get('security', 'reality'),
                    tls_sni=panel_data.get('tls_sni', ''),
                    reality_sni=panel_data.get('reality_sni', ''),
                    reality_fingerprint=panel_data.get('reality_fingerprint', 'chrome'),
                    reality_public_key=panel_data.get('reality_public_key', ''),
                    reality_private_key=panel_data.get('reality_private_key', ''),
                    reality_short_id=panel_data.get('reality_short_id', '')
                )
            
            # Загружаем панель по умолчанию
            self.default_panel_id = data.get('default_panel')
            
            logger.info(f"✅ Загружено панелей: {len(self.panels)}")
            logger.info(f"✅ Панель по умолчанию: {self.default_panel_id}")
            
        except yaml.YAMLError as e:
            logger.error(f"❌ Ошибка парсинга YAML: {e}")
            raise ValueError(f"Ошибка парсинга config.yaml: {e}")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки config.yaml: {e}", exc_info=True)
            raise
    
    def _load_from_env(self):
        """Загрузить конфигурацию из .env (НЕ ИСПОЛЬЗУЕТСЯ - только для миграции)"""
        logger.error("❌ Прямая загрузка из .env больше не поддерживается")
        logger.error("💡 Используйте config.yaml или запустите автоматическую миграцию")
        raise RuntimeError("Загрузка из .env не поддерживается. Используйте config.yaml")
    
    def _migrate_env_to_yaml(self) -> bool:
        """
        Автоматическая миграция .env → config.yaml
        Использует функцию из migrate_env_to_yaml.py
        """
        try:
            # Импортируем функцию миграции
            sys.path.insert(0, str(Path(__file__).parent))
            from migrate_env_to_yaml import migrate_env_to_yaml
            
            env_path = Path('.env')
            
            # Выполняем миграцию
            success = migrate_env_to_yaml(env_path, self.config_path)
            
            return success
            
        except ImportError as e:
            logger.error(f"❌ Не удалось импортировать модуль миграции: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка миграции: {e}")
            return False
    
    def get_panel(self, panel_id: str) -> Optional[PanelConfig]:
        """Получить конфигурацию панели по ID"""
        return self.panels.get(panel_id)
    
    def get_default_panel(self) -> Optional[PanelConfig]:
        """Получить панель по умолчанию"""
        if self.default_panel_id:
            return self.panels.get(self.default_panel_id)
        return None
    
    def get_all_panels(self) -> Dict[str, PanelConfig]:
        """Получить все панели"""
        return self.panels
    
    def get_current_panel_id(self) -> Optional[str]:
        """Получить ID текущей активной панели"""
        return self.default_panel_id
    
    def switch_default_panel(self, panel_id: str) -> bool:
        """Переключить панель по умолчанию"""
        if panel_id not in self.panels:
            return False
        
        self.default_panel_id = panel_id
        
        # Сохраняем в config.yaml
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                
                data['default_panel'] = panel_id
                
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
                
                logger.info(f"✅ Панель по умолчанию изменена на: {panel_id}")
                return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения: {e}")
            return False
        
        return True
    
    def switch_panel(self, panel_id: str) -> bool:
        """Алиас для switch_default_panel (обратная совместимость)"""
        return self.switch_default_panel(panel_id)
    
    def create_xui_config_from_panel(self, panel_id: str):
        """Создает XUICompat объект из конфигурации панели"""
        from dataclasses import dataclass
        
        panel = self.get_panel(panel_id)
        if not panel:
            return None
        
        @dataclass
        class XUICompat:
            url: str
            username: str
            password: str
            inbound_id: int
            db_path: str
            api_timeout: int
            version: str
            api_token: str = ""
            
            def is_v2(self) -> bool:
                """Проверка является ли версия 2.x"""
                return self.version.startswith("2.")
            
            def is_v3(self) -> bool:
                """Проверка является ли версия 3.x или latest"""
                return self.version.startswith("3.") or self.version == "latest"
            
            def is_v3_new_api(self) -> bool:
                """Проверка использует ли версия новый API v3"""
                return self.version == "latest" or self.version.startswith("3.")
        
        return XUICompat(
            url=panel.xui_url,
            username=panel.xui_username,
            password=panel.xui_password,
            inbound_id=panel.inbound_id,
            db_path=panel.xui_db_path,
            api_timeout=self.common.api_timeout,
            version=panel.xui_version,
            api_token=panel.xui_api_token
        )
    
    async def fetch_and_update_panel_settings(self, panel_id: str, xui_client) -> bool:
        """Извлекает параметры из панели (transport, security, Reality) и обновляет конфигурацию"""
        try:
            import subprocess
            import json
            
            panel = self.get_panel(panel_id)
            if not panel:
                return False
            
            # Извлекаем stream_settings из базы данных панели
            sql_get = f"""sqlite3 {panel.xui_db_path} "SELECT stream_settings FROM inbounds WHERE id={panel.inbound_id};" """
            result = subprocess.run(sql_get, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0 or not result.stdout:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Не удалось получить stream_settings для inbound id={panel.inbound_id}")
                return False
            
            stream_settings = json.loads(result.stdout.strip())
            
            # Извлекаем transport (network)
            transport = stream_settings.get('network', 'tcp')
            if transport:
                panel.transport = transport
            
            # Извлекаем security
            security = stream_settings.get('security', 'none')
            if security:
                panel.security = security
            
            # Если security == reality, извлекаем параметры Reality
            if security == 'reality':
                reality_config = stream_settings.get('realitySettings', {})
                
                # SNI из serverNames
                sni = reality_config.get('serverNames', [''])[0] if reality_config.get('serverNames') else ''
                if sni:
                    panel.reality_sni = sni
                
                # Fingerprint
                settings_obj = reality_config.get('settings', {})
                if isinstance(settings_obj, dict):
                    fingerprint = settings_obj.get('fingerprint', reality_config.get('fingerprint', 'chrome'))
                    public_key = settings_obj.get('publicKey', reality_config.get('publicKey', ''))
                else:
                    fingerprint = reality_config.get('fingerprint', 'chrome')
                    public_key = reality_config.get('publicKey', '')
                
                if fingerprint:
                    panel.reality_fingerprint = fingerprint
                if public_key:
                    panel.reality_public_key = public_key
                
                # Short ID из shortIds
                short_id = reality_config.get('shortIds', [''])[0] if reality_config.get('shortIds') else ''
                if short_id:
                    panel.reality_short_id = short_id
            
            # Сохраняем обновленную конфигурацию в файл
            self._save_config()
            
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"✅ Параметры панели {panel_id} обновлены: transport={transport}, security={security}")
            
            return True
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка при извлечении параметров панели: {e}")
            return False
    
    def _save_config(self):
        """Сохраняет текущую конфигурацию в файл"""
        try:
            # Пересобираем словарь из объектов
            config_dict = {
                'common': {
                    'xui_bot_token': self.common.xui_bot_token,
                    'awg_bot_token': self.common.awg_bot_token,
                    'admin_ids': self.common.admin_ids,
                    'server_port': self.common.server_port,
                    'api_timeout': self.common.api_timeout,
                    'xhttp_mode': self.common.xhttp_mode,
                    'tls_fingerprint': self.common.tls_fingerprint,
                    'tls_alpn': self.common.tls_alpn,
                    'max_traffic_gb': self.common.max_traffic_gb,
                    'max_days': self.common.max_days,
                    'min_days': self.common.min_days,
                    'default_traffic_gb': self.common.default_traffic_gb,
                    'default_days': self.common.default_days,
                    'db_path': self.common.db_path,
                    'db_backup_enabled': self.common.db_backup_enabled,
                    'db_backup_interval': self.common.db_backup_interval,
                    'log_level': self.common.log_level,
                    'log_file_enabled': self.common.log_file_enabled,
                    'log_file_path': self.common.log_file_path,
                    'log_max_size_mb': self.common.log_max_size_mb,
                    'log_backup_count': self.common.log_backup_count,
                    'allow_user_dns_queries': self.common.allow_user_dns_queries
                },
                'default_panel': self.default_panel_id,
                'panels': {}
            }
            
            # Добавляем панели
            for panel_id, panel in self.panels.items():
                config_dict['panels'][panel_id] = {
                    'alias': panel.alias,
                    'enabled': panel.enabled,
                    'is_local': panel.is_local,
                    'xui_version': panel.xui_version,
                    'xui_url': panel.xui_url,
                    'xui_username': panel.xui_username,
                    'xui_password': panel.xui_password,
                    'xui_api_token': panel.xui_api_token,
                    'xui_db_path': panel.xui_db_path,
                    'inbound_id': panel.inbound_id,
                    'server_address': panel.server_address,
                    'server_ip': panel.server_ip,
                    'transport': panel.transport,
                    'security': panel.security,
                    'tls_sni': panel.tls_sni,
                    'tls_fingerprint': panel.tls_fingerprint,
                    'reality_sni': panel.reality_sni,
                    'reality_fingerprint': panel.reality_fingerprint,
                    'reality_public_key': panel.reality_public_key,
                    'reality_private_key': panel.reality_private_key,
                    'reality_short_id': panel.reality_short_id
                }
            
            # Сохраняем в файл
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка при сохранении конфигурации: {e}")
    
    async def check_panel_status(self, panel_config: PanelConfig) -> bool:
        """Проверить доступность панели"""
        try:
            login_url = f"{panel_config.xui_url.rstrip('/')}/login"
            timeout = aiohttp.ClientTimeout(total=5)
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    login_url,
                    json={'username': panel_config.xui_username, 'password': panel_config.xui_password},
                    ssl=False
                ) as response:
                    return response.status in [200, 401, 403]
        except:
            return False
    
    async def check_all_panels_status(self) -> Dict[str, bool]:
        """Проверить статус всех панелей"""
        statuses = {}
        for panel_id, panel_config in self.panels.items():
            statuses[panel_id] = await self.check_panel_status(panel_config)
        return statuses


class UserDatabase:
    """База данных пользователей с поддержкой мультипанелей"""
    
    def __init__(self, db_path: str = "/app/data/bot_users.db", admin_ids: List[int] = None):
        self.db_path = db_path
        self.admin_ids = admin_ids or []
        self._init_db()
    
    def _init_db(self):
        """Инициализация базы данных"""
        with sqlite3.connect(self.db_path) as conn:
            # Таблица разрешенных пользователей
            conn.execute("""
                CREATE TABLE IF NOT EXISTS allowed_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT,
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица клиентов пользователей с поддержкой panel_id
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    client_email TEXT,
                    client_uuid TEXT,
                    comment TEXT,
                    panel_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Создаем индексы если их нет
            try:
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_user_clients_panel_id 
                    ON user_clients(panel_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_user_clients_user_panel 
                    ON user_clients(user_id, panel_id)
                """)
            except sqlite3.OperationalError:
                pass  # Индексы уже существуют
            
            # Остальные таблицы
            conn.execute("""
                CREATE TABLE IF NOT EXISTS admin_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notification_settings (
                    setting_name TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 0
                )
            """)
            
            conn.execute("""
                INSERT OR IGNORE INTO notification_settings (setting_name, enabled)
                VALUES ('cpu_alert', 0), ('disk_alert', 0), ('ram_alert', 0)
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    blocked_by INTEGER
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_history (
                    user_id INTEGER PRIMARY KEY,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Добавляем главного админа
            main_admin = self.get_main_admin()
            if main_admin:
                conn.execute("""
                    INSERT OR IGNORE INTO allowed_users (user_id, username, added_by) 
                    VALUES (?, 'main_admin', ?)
                """, (main_admin, main_admin))
    
    def get_main_admin(self) -> int:
        """Получить ID главного администратора"""
        if self.admin_ids and len(self.admin_ids) > 0:
            return self.admin_ids[0]
        return 0
    
    def is_allowed(self, user_id: int) -> bool:
        """Проверить разрешен ли пользователь"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None
    
    def is_blocked_by_admin(self, user_id: int) -> bool:
        """Проверить заблокирован ли пользователь (глобально)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM blocked_users WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None
    
    def add_user(self, user_id: int, username: str = None, added_by: int = None) -> bool:
        """Добавить пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)",
                    (user_id, username, added_by or self.get_main_admin())
                )
                conn.execute("INSERT OR IGNORE INTO user_history (user_id) VALUES (?)", (user_id,))
                conn.execute(
                    "UPDATE user_history SET last_seen = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (user_id,)
                )
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления пользователя: {e}")
            return False
    
    def add_user_client(self, user_id: int, client_email: str, client_uuid: str, 
                       comment: str = None, panel_id: str = None) -> bool:
        """Добавить клиента пользователя с привязкой к панели"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO user_clients (user_id, client_email, client_uuid, comment, panel_id) VALUES (?, ?, ?, ?, ?)",
                    (user_id, client_email, client_uuid, comment, panel_id)
                )
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения клиента: {e}")
            return False
    
    def get_user_clients(self, user_id: int, panel_id: str = None) -> list:
        """Получить клиентов пользователя (опционально с фильтром по панели)"""
        with sqlite3.connect(self.db_path) as conn:
            if panel_id:
                cursor = conn.execute(
                    "SELECT id, client_email, client_uuid, comment, panel_id, created_at FROM user_clients WHERE user_id = ? AND panel_id = ? ORDER BY created_at DESC",
                    (user_id, panel_id)
                )
            else:
                cursor = conn.execute(
                    "SELECT id, client_email, client_uuid, comment, panel_id, created_at FROM user_clients WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,)
                )
            return cursor.fetchall()
    
    def get_user_clients_by_panel(self, user_id: int) -> Dict[str, list]:
        """Получить клиентов пользователя, сгруппированных по панелям"""
        clients = self.get_user_clients(user_id)
        grouped = {}
        for client in clients:
            panel_id = client[4] or "default"  # panel_id is at index 4
            if panel_id not in grouped:
                grouped[panel_id] = []
            grouped[panel_id].append(client)
        return grouped
    
    # Остальные методы остаются без изменений
    def remove_user(self, user_id: int) -> bool:
        if user_id == self.get_main_admin():
            return False
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
            return True
        except Exception as e:
            logger.error(f"Ошибка удаления пользователя: {e}")
            return False
    
    def list_users(self) -> list:
        main_admin = self.get_main_admin()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT user_id, username, added_at FROM allowed_users WHERE user_id != ? ORDER BY added_at DESC",
                (main_admin,)
            )
            return cursor.fetchall()
    
    def delete_user_client(self, client_id: int) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM user_clients WHERE id = ?", (client_id,))
            return True
        except Exception as e:
            logger.error(f"Ошибка удаления клиента: {e}")
            return False
    
    def get_all_users_clients(self) -> list:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT uc.id, uc.user_id, u.username, uc.client_email, uc.client_uuid, uc.comment, uc.panel_id, uc.created_at 
                FROM user_clients uc
                LEFT JOIN allowed_users u ON uc.user_id = u.user_id
                ORDER BY uc.created_at DESC
            """)
            return cursor.fetchall()
    
    def get_user_count(self) -> int:
        main_admin = self.get_main_admin()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM allowed_users WHERE user_id != ?", (main_admin,))
            return cursor.fetchone()[0]
    
    def block_user(self, user_id: int, blocked_by: int) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO blocked_users (user_id, blocked_by) VALUES (?, ?)",
                    (user_id, blocked_by)
                )
            return True
        except Exception as e:
            logger.error(f"Ошибка блокировки: {e}")
            return False
    
    def unblock_user(self, user_id: int) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
            return True
        except Exception as e:
            logger.error(f"Ошибка разблокировки: {e}")
            return False
    
    def get_notification_setting(self, setting_name: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT enabled FROM notification_settings WHERE setting_name = ?",
                (setting_name,)
            )
            result = cursor.fetchone()
            return bool(result[0]) if result else False
    
    def set_notification_setting(self, setting_name: str, enabled: bool) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO notification_settings (setting_name, enabled) VALUES (?, ?)",
                    (setting_name, 1 if enabled else 0)
                )
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения настройки уведомления: {e}")
            return False
    
    def get_all_notification_settings(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT setting_name, enabled FROM notification_settings")
            return {row[0]: bool(row[1]) for row in cursor.fetchall()}
    
    def was_user_registered(self, user_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM user_history WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None


class Config:
    """Главный класс конфигурации с поддержкой мультипанелей"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_manager = ConfigManager(config_path)
        self.common = self.config_manager.common
        self.users_db = UserDatabase(self.common.db_path, self.common.admin_ids)
        self._validate()
        
        # Создаем свойства для обратной совместимости со старым кодом
        self._setup_legacy_properties()
    
    def _setup_legacy_properties(self):
        """Создание свойств для обратной совместимости"""
        from dataclasses import dataclass
        
        # bot property - для доступа к токену и админам
        @dataclass
        class BotCompat:
            token: str
            admin_ids: list
            admin_username: str = None
        
        self.bot = BotCompat(
            token=self.common.xui_bot_token,
            admin_ids=self.common.admin_ids,
            admin_username=None
        )
        
        # xui property - параметры текущей панели
        current_panel = self.get_current_panel()
        if current_panel:
            @dataclass
            class XUICompat:
                url: str
                username: str
                password: str
                inbound_id: int
                db_path: str
                api_timeout: int
                version: str
                api_token: str
                
                def is_v2(self) -> bool:
                    """Проверка является ли версия 2.x"""
                    return self.version.startswith("2.")
                
                def is_v3(self) -> bool:
                    """Проверка является ли версия 3.x или latest"""
                    return self.version.startswith("3.") or self.version == "latest"
                
                def is_v3_new_api(self) -> bool:
                    """Проверка использует ли версия новый API v3"""
                    return self.version == "latest" or self.version.startswith("3.")
            
            self.xui = XUICompat(
                url=current_panel.xui_url,
                username=current_panel.xui_username,
                password=current_panel.xui_password,
                inbound_id=current_panel.inbound_id,
                db_path=current_panel.xui_db_path,
                api_timeout=self.common.api_timeout,
                version=current_panel.xui_version,
                api_token=current_panel.xui_api_token
            )
        
        # vpn property - параметры VPN текущей панели
        if current_panel:
            @dataclass
            class VPNCompat:
                server_address: str
                server_port: int
                transport: str
                security: str
                tls_sni: str
                tls_fingerprint: str
                tls_alpn: str
                reality_sni: str
                reality_fingerprint: str
                reality_public_key: str
                reality_short_id: str
                xhttp_mode: str
                
                def get_sni(self) -> str:
                    return self.tls_sni if self.security == "tls" else self.reality_sni
                
                def get_fingerprint(self) -> str:
                    return self.tls_fingerprint if self.security == "tls" else self.reality_fingerprint
            
            self.vpn = VPNCompat(
                server_address=current_panel.server_address,
                server_port=self.common.server_port,
                transport=current_panel.transport,
                security=current_panel.security,
                tls_sni=current_panel.tls_sni,
                tls_fingerprint=current_panel.tls_fingerprint or self.common.tls_fingerprint,
                tls_alpn=self.common.tls_alpn,
                reality_sni=current_panel.reality_sni,
                reality_fingerprint=current_panel.reality_fingerprint,
                reality_public_key=current_panel.reality_public_key,
                reality_short_id=current_panel.reality_short_id,
                xhttp_mode=self.common.xhttp_mode
            )
        
        # limits property
        @dataclass
        class LimitsCompat:
            max_traffic_gb: int
            max_days: int
            min_days: int
            default_traffic_gb: int
            default_days: int
        
        self.limits = LimitsCompat(
            max_traffic_gb=self.common.max_traffic_gb,
            max_days=self.common.max_days,
            min_days=self.common.min_days,
            default_traffic_gb=self.common.default_traffic_gb,
            default_days=self.common.default_days
        )
        
        # logging property
        @dataclass
        class LoggingCompat:
            level: str
            file_enabled: bool
            file_path: str
            max_size_mb: int
            backup_count: int
        
        self.logging = LoggingCompat(
            level=self.common.log_level,
            file_enabled=self.common.log_file_enabled,
            file_path=self.common.log_file_path,
            max_size_mb=self.common.log_max_size_mb,
            backup_count=self.common.log_backup_count
        )
        
        # panel_manager property - прямая ссылка
        self.panel_manager = self.config_manager
    
    def _validate(self):
        """Валидация конфигурации"""
        if not self.common.xui_bot_token:
            raise ValueError("XUI_BOT_TOKEN не указан")
        
        default_panel = self.config_manager.get_default_panel()
        if not default_panel:
            raise ValueError("Не найдена панель по умолчанию")
        
        if not default_panel.xui_password:
            raise ValueError("XUI_PASSWORD не указан для панели по умолчанию")
        
        if default_panel.security == "reality":
            if not default_panel.reality_public_key:
                logger.warning("⚠️ REALITY_PUBLIC_KEY не указан")
            if not default_panel.reality_short_id:
                logger.warning("⚠️ REALITY_SHORT_ID не указан")
    
    def get_current_panel(self) -> Optional[PanelConfig]:
        """Получить текущую активную панель"""
        return self.config_manager.get_default_panel()
    
    def get_panel(self, panel_id: str) -> Optional[PanelConfig]:
        """Получить панель по ID"""
        return self.config_manager.get_panel(panel_id)
    
    def get_all_panels(self) -> Dict[str, PanelConfig]:
        """Получить все панели"""
        return self.config_manager.get_all_panels()
    
    def switch_panel(self, panel_id: str) -> bool:
        """Переключить текущую панель"""
        return self.config_manager.switch_default_panel(panel_id)
    
    def is_admin(self, user_id: int) -> bool:
        """Проверить является ли пользователь админом"""
        return user_id in self.common.admin_ids
    
    def refresh_vpn_config(self):
        """Обновляет VPN конфигурацию из текущей панели"""
        from dataclasses import dataclass
        
        current_panel = self.config_manager.get_default_panel()
        if not current_panel:
            return
        
        @dataclass
        class VPNCompat:
            server_address: str
            server_port: int
            transport: str
            security: str
            tls_sni: str
            tls_fingerprint: str
            tls_alpn: str
            reality_sni: str
            reality_fingerprint: str
            reality_public_key: str
            reality_short_id: str
            xhttp_mode: str
            
            def get_sni(self) -> str:
                return self.tls_sni if self.security == "tls" else self.reality_sni
            
            def get_fingerprint(self) -> str:
                return self.tls_fingerprint if self.security == "tls" else self.reality_fingerprint
        
        self.vpn = VPNCompat(
            server_address=current_panel.server_address,
            server_port=self.common.server_port,
            transport=current_panel.transport,
            security=current_panel.security,
            tls_sni=current_panel.tls_sni,
            tls_fingerprint=current_panel.tls_fingerprint or self.common.tls_fingerprint,
            tls_alpn=self.common.tls_alpn,
            reality_sni=current_panel.reality_sni,
            reality_fingerprint=current_panel.reality_fingerprint,
            reality_public_key=current_panel.reality_public_key,
            reality_short_id=current_panel.reality_short_id,
            xhttp_mode=self.common.xhttp_mode
        )
    
    def is_allowed(self, user_id: int) -> bool:
        """Проверить разрешен ли пользователь"""
        return self.users_db.is_allowed(user_id)
    
    def display(self) -> str:
        """Отобразить текущую конфигурацию"""
        current_panel = self.get_current_panel()
        user_count = self.users_db.get_user_count()
        
        if not current_panel:
            return "❌ Панель не настроена"
        
        return f"""
📋 <b>Конфигурация бота:</b>

<b>Telegram Bot:</b>
• Admin IDs: {', '.join(map(str, self.common.admin_ids))}
• Разрешено пользователей: {user_count}

<b>Текущая панель: {current_panel.alias}</b>
• URL: {current_panel.xui_url}
• Inbound ID: {current_panel.inbound_id}
• Версия: {current_panel.xui_version}

<b>VPN Settings:</b>
• Server: {current_panel.server_address}:{self.common.server_port}
• Security: {current_panel.security}
• Transport: {current_panel.transport}
• SNI: {current_panel.get_sni()}
• Fingerprint: {current_panel.get_fingerprint()}

<b>Limits:</b>
• Max Traffic: {self.common.max_traffic_gb} GB
• Max Days: {self.common.max_days}
• Default Traffic: {self.common.default_traffic_gb} GB
• Default Days: {self.common.default_days}

<b>Всего панелей:</b> {len(self.config_manager.panels)}
"""


# Создаем глобальный экземпляр конфигурации
config = Config()

# Made with Bob
