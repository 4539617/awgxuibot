# config.py - Multi-Server Configuration Manager
import os
from dotenv import load_dotenv
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import sqlite3
import yaml
import aiohttp
import asyncio
from pathlib import Path
import logging

# Загружаем .env для обратной совместимости (если config.yaml не найден)
load_dotenv()

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
    xui_version: str
    xui_url: str
    xui_username: str
    xui_password: str
    xui_api_token: str
    inbound_id: int
    xui_db_path: str
    server_address: str
    server_ip: str
    transport: str
    security: str
    tls_sni: str = ""
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
        """Загрузить конфигурацию из YAML файла"""
        logger.info(f"🔍 Загрузка конфигурации из: {self.config_path.absolute()}")
        
        if not self.config_path.exists():
            logger.warning(f"⚠️ Файл {self.config_path} не найден, используем .env")
            self._load_from_env()
            return
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data:
                logger.error("❌ config.yaml пустой")
                self._load_from_env()
                return
            
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
            self._load_from_env()
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки config.yaml: {e}", exc_info=True)
            self._load_from_env()
    
    def _load_from_env(self):
        """Загрузить конфигурацию из .env (fallback)"""
        logger.info("📄 Загрузка конфигурации из .env")
        
        # Загружаем общие параметры из .env
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
        
        self.common = CommonConfig(
            xui_bot_token=os.getenv("XUI_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", ""),
            awg_bot_token=os.getenv("AWG_BOT_TOKEN", ""),
            admin_ids=admin_ids,
            server_port=int(os.getenv("SERVER_PORT", "443")),
            api_timeout=int(os.getenv("API_TIMEOUT", "30")),
            xhttp_mode=os.getenv("XHTTP_MODE", "auto"),
            tls_fingerprint=os.getenv("TLS_FINGERPRINT", "edge"),
            tls_alpn=os.getenv("TLS_ALPN", "http/1.1"),
            max_traffic_gb=int(os.getenv("MAX_TRAFFIC_GB", "1000")),
            max_days=int(os.getenv("MAX_DAYS", "3650")),
            min_days=int(os.getenv("MIN_DAYS", "1")),
            default_traffic_gb=int(os.getenv("DEFAULT_TRAFFIC_GB", "100")),
            default_days=int(os.getenv("DEFAULT_DAYS", "30")),
            db_path=os.getenv("DB_PATH", "/app/data/bot_users.db"),
            db_backup_enabled=os.getenv("DB_BACKUP_ENABLED", "true").lower() == "true",
            db_backup_interval=int(os.getenv("DB_BACKUP_INTERVAL", "24")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file_enabled=os.getenv("LOG_FILE_ENABLED", "true").lower() == "true",
            log_file_path=os.getenv("LOG_FILE_PATH", "/app/logs/bot.log"),
            log_max_size_mb=int(os.getenv("LOG_MAX_SIZE_MB", "10")),
            log_backup_count=int(os.getenv("LOG_BACKUP_COUNT", "5")),
            allow_user_dns_queries=os.getenv("ALLOW_USER_DNS_QUERIES", "false").lower() == "true"
        )
        
        # Создаем одну панель из .env
        panel_id = "default"
        self.panels[panel_id] = PanelConfig(
            panel_id=panel_id,
            alias="Default Panel",
            enabled=True,
            xui_version=os.getenv("XUI_VERSION", "latest"),
            xui_url=os.getenv("XUI_URL", "http://localhost:2053"),
            xui_username=os.getenv("XUI_USERNAME", "admin"),
            xui_password=os.getenv("XUI_PASSWORD", ""),
            xui_api_token=os.getenv("XUI_API_TOKEN", ""),
            inbound_id=int(os.getenv("INBOUND_ID", "1")),
            xui_db_path=os.getenv("XUI_DB_PATH", "/etc/x-ui/x-ui.db"),
            server_address=os.getenv("SERVER_ADDRESS", ""),
            server_ip=os.getenv("SERVER_IP", ""),
            transport=os.getenv("TRANSPORT", "tcp"),
            security=os.getenv("SECURITY", "tls"),
            tls_sni=os.getenv("TLS_SNI", ""),
            reality_sni=os.getenv("REALITY_SNI", ""),
            reality_fingerprint=os.getenv("REALITY_FINGERPRINT", "chrome"),
            reality_public_key=os.getenv("REALITY_PUBLIC_KEY", ""),
            reality_private_key=os.getenv("REALITY_PRIVATE_KEY", ""),
            reality_short_id=os.getenv("REALITY_SHORT_ID", "")
        )
        
        self.default_panel_id = panel_id
        logger.info("✅ Конфигурация загружена из .env")
    
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
    
    def __init__(self, db_path: str = "/app/data/bot_users.db"):
        self.db_path = db_path
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
        admin_ids_str = os.getenv("ADMIN_IDS", "0")
        try:
            return int(admin_ids_str.split(',')[0])
        except:
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
        self.users_db = UserDatabase(self.common.db_path)
        self._validate()
    
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
