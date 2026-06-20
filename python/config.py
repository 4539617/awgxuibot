# config.py
import os
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Optional, List
import sqlite3
import yaml
import aiohttp
import asyncio
from pathlib import Path

load_dotenv()


@dataclass
class BotConfig:
    token: str
    admin_ids: List[int]
    admin_username: Optional[str] = None

    @classmethod
    def from_env(cls):
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
        # Поддержка обоих токенов: XUI_BOT_TOKEN (новый) и TELEGRAM_BOT_TOKEN (legacy)
        token = os.getenv("XUI_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        return cls(
            token=token,
            admin_ids=admin_ids,
            admin_username=os.getenv("ADMIN_USERNAME")
        )


@dataclass
class XUIConfig:
    url: str
    username: str
    password: str
    inbound_id: int
    db_path: str
    api_timeout: int = 30
    version: str = "latest"
    api_token: Optional[str] = None

    @classmethod
    def from_env(cls):
        return cls(
            url=os.getenv("XUI_URL", "http://localhost:2053"),
            username=os.getenv("XUI_USERNAME", "admin"),
            password=os.getenv("XUI_PASSWORD", ""),
            inbound_id=int(os.getenv("INBOUND_ID", "1")),
            db_path=os.getenv("XUI_DB_PATH", "/etc/x-ui/x-ui.db"),
            api_timeout=int(os.getenv("API_TIMEOUT", "30")),
            version=os.getenv("XUI_VERSION", "latest"),
            api_token=os.getenv("XUI_API_TOKEN")
        )
    
    def is_v2(self) -> bool:
        """Проверка является ли версия 2.x (смотрим только на первую цифру)"""
        return self.version.startswith("2.")
    
    def is_v3(self) -> bool:
        """Проверка является ли версия 3.x или latest (смотрим только на первую цифру)"""
        return self.version.startswith("3.") or self.version == "latest"
    
    def is_v3_new_api(self) -> bool:
        """Проверка использует ли версия новый API v3 (любая версия 3.x)"""
        # Для v3 всегда используем новый API, независимо от подверсии
        if self.version == "latest":
            return True
        # Смотрим только на первую цифру
        if self.version.startswith("3."):
            return True
        return False


@dataclass
class VPNConfig:
    server_address: str
    server_port: int
    xui_db_path: str = "/etc/x-ui/x-ui.db"
    transport: str = "tcp"  # Допустимые значения: tcp, xhttp, ws, grpc, httpupgrade, splithttp
    security: str = "tls"   # Допустимые значения: tls, reality
    tls_sni: str = ""
    tls_fingerprint: str = "chrome"
    tls_alpn: str = "http/1.1"
    reality_sni: str = ""
    reality_fingerprint: str = "chrome"
    reality_public_key: str = ""
    reality_short_id: str = ""
    xhttp_mode: str = "auto"

    @classmethod
    def from_env(cls):
        return cls(
            server_address=os.getenv("SERVER_ADDRESS", ""),
            server_port=int(os.getenv("SERVER_PORT", "443")),
            xui_db_path=os.getenv("XUI_DB_PATH", "/etc/x-ui/x-ui.db"),
            transport=os.getenv("TRANSPORT", "tcp"),  # ВАЖНО: Установите в .env файле!
            security=os.getenv("SECURITY", "tls"),    # ВАЖНО: Установите в .env файле!
            tls_sni=os.getenv("TLS_SNI", ""),
            tls_fingerprint=os.getenv("TLS_FINGERPRINT", "chrome"),
            tls_alpn=os.getenv("TLS_ALPN", "http/1.1"),
            reality_sni=os.getenv("REALITY_SNI", ""),
            reality_fingerprint=os.getenv("REALITY_FINGERPRINT", "chrome"),
            reality_public_key=os.getenv("REALITY_PUBLIC_KEY", ""),
            reality_short_id=os.getenv("REALITY_SHORT_ID", ""),
            xhttp_mode=os.getenv("XHTTP_MODE", "auto")
        )
    
    def get_sni(self) -> str:
        if self.security == "tls":
            return self.tls_sni
        return self.reality_sni
    
    def get_fingerprint(self) -> str:
        if self.security == "tls":
            return self.tls_fingerprint
        return self.reality_fingerprint


@dataclass
class LimitsConfig:
    max_traffic_gb: int
    max_days: int
    min_days: int
    default_traffic_gb: int
    default_days: int

    @classmethod
    def from_env(cls):
        return cls(
            max_traffic_gb=int(os.getenv("MAX_TRAFFIC_GB", "1000")),
            max_days=int(os.getenv("MAX_DAYS", "3650")),
            min_days=int(os.getenv("MIN_DAYS", "1")),
            default_traffic_gb=int(os.getenv("DEFAULT_TRAFFIC_GB", "1000")),
            default_days=int(os.getenv("DEFAULT_DAYS", "30"))
        )


@dataclass
class DatabaseConfig:
    path: str
    backup_enabled: bool
    backup_interval_hours: int

    @classmethod
    def from_env(cls):
        return cls(
            path=os.getenv("DB_PATH", "bot_data.db"),
            backup_enabled=os.getenv("DB_BACKUP_ENABLED", "true").lower() == "true",
            backup_interval_hours=int(os.getenv("DB_BACKUP_INTERVAL", "24"))
        )


@dataclass
class LoggingConfig:
    level: str
    file_enabled: bool
    file_path: str
    max_size_mb: int
    backup_count: int

    @classmethod
    def from_env(cls):
        return cls(
            level=os.getenv("LOG_LEVEL", "INFO"),
            file_enabled=os.getenv("LOG_FILE_ENABLED", "true").lower() == "true",
            file_path=os.getenv("LOG_FILE_PATH", "bot.log"),
            max_size_mb=int(os.getenv("LOG_MAX_SIZE_MB", "10")),
            backup_count=int(os.getenv("LOG_BACKUP_COUNT", "5"))
        )


class UserDatabase:
    def __init__(self, db_path: str = "/app/data/bot_users.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS allowed_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT,
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    client_email TEXT,
                    client_uuid TEXT,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
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
            # Инициализируем настройки уведомлений по умолчанию
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
            main_admin = self.get_main_admin()
            conn.execute("""
                INSERT OR IGNORE INTO allowed_users (user_id, username, added_by) 
                VALUES (?, 'main_admin', ?)
            """, (main_admin, main_admin))

    def get_main_admin(self) -> int:
        return int(os.getenv("ADMIN_IDS", "0").split(',')[0])

    def is_allowed(self, user_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None

    def was_user_registered(self, user_id: int) -> bool:
        """Проверка был ли пользователь ранее зарегистрирован в системе"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM user_history WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None
    
    def add_user(self, user_id: int, username: str = None, added_by: int = None) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)",
                    (user_id, username, added_by or self.get_main_admin())
                )
                # Добавляем в историю пользователей
                conn.execute(
                    "INSERT OR IGNORE INTO user_history (user_id) VALUES (?)",
                    (user_id,)
                )
                # Обновляем last_seen
                conn.execute(
                    "UPDATE user_history SET last_seen = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (user_id,)
                )
            return True
        except Exception as e:
            print(f"Ошибка добавления пользователя: {e}")
            return False

    def remove_user(self, user_id: int) -> bool:
        if user_id == self.get_main_admin():
            return False
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
            return True
        except Exception as e:
            print(f"Ошибка удаления пользователя: {e}")
            return False

    def list_users(self) -> list:
        main_admin = self.get_main_admin()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT user_id, username, added_at FROM allowed_users WHERE user_id != ? ORDER BY added_at DESC",
                (main_admin,)
            )
            return cursor.fetchall()

    def add_user_client(self, user_id: int, client_email: str, client_uuid: str, comment: str = None) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO user_clients (user_id, client_email, client_uuid, comment) VALUES (?, ?, ?, ?)",
                    (user_id, client_email, client_uuid, comment)
                )
            return True
        except Exception as e:
            print(f"Ошибка сохранения клиента: {e}")
            return False

    def delete_user_client(self, client_id: int) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM user_clients WHERE id = ?", (client_id,))
            return True
        except Exception as e:
            print(f"Ошибка удаления клиента: {e}")
            return False

    def get_user_clients(self, user_id: int) -> list:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, client_email, client_uuid, comment, created_at FROM user_clients WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            return cursor.fetchall()

    def get_all_users_clients(self) -> list:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT uc.id, uc.user_id, u.username, uc.client_email, uc.client_uuid, uc.comment, uc.created_at 
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
            print(f"Ошибка блокировки: {e}")
            return False

    def unblock_user(self, user_id: int) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
            return True
        except Exception as e:
            print(f"Ошибка разблокировки: {e}")
            return False
    
    def get_notification_setting(self, setting_name: str) -> bool:
        """Получить настройку уведомления"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT enabled FROM notification_settings WHERE setting_name = ?",
                (setting_name,)
            )
            result = cursor.fetchone()
            return bool(result[0]) if result else False
    
    def set_notification_setting(self, setting_name: str, enabled: bool) -> bool:
        """Установить настройку уведомления"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO notification_settings (setting_name, enabled) VALUES (?, ?)",
                    (setting_name, 1 if enabled else 0)
                )
            return True
        except Exception as e:
            print(f"Ошибка сохранения настройки уведомления: {e}")
            return False
    
    def get_all_notification_settings(self) -> dict:
        """Получить все настройки уведомлений"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT setting_name, enabled FROM notification_settings")
            return {row[0]: bool(row[1]) for row in cursor.fetchall()}

    def is_blocked_by_admin(self, user_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM blocked_users WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None

class PanelManager:
    """Менеджер для управления несколькими панелями 3x-ui"""
    
    def __init__(self, config_path: str = "panels.yaml"):
        self.config_path = Path(config_path)
        self.panels = {}
        self.current_panel_id = None
        self._load_panels()
    
    def _load_panels(self):
        """Загрузить конфигурацию панелей из YAML файла"""
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"🔍 Попытка загрузки панелей из: {self.config_path.absolute()}")
        
        if not self.config_path.exists():
            logger.warning(f"⚠️ Файл {self.config_path} не найден")
            self.panels = {}
            self.current_panel_id = None
            return
        
        try:
            logger.info(f"📂 Файл найден, размер: {self.config_path.stat().st_size} байт")
            
            with open(self.config_path, 'r', encoding='utf-8') as f:
                content = f.read()
                logger.info(f"📄 Содержимое файла прочитано: {len(content)} символов")
                
                data = yaml.safe_load(content)
                logger.info(f"✅ YAML распарсен: {data}")
                
                if data:
                    self.panels = data.get('panels', {})
                    self.current_panel_id = data.get('current_panel')
                    logger.info(f"✅ Загружено панелей: {len(self.panels)}")
                    logger.info(f"✅ Текущая панель: {self.current_panel_id}")
                    
                    for panel_id, panel_config in self.panels.items():
                        logger.info(f"  📋 {panel_id}: {panel_config.get('alias', 'без имени')}")
                else:
                    logger.warning("⚠️ YAML файл пустой или содержит null")
                    self.panels = {}
                    self.current_panel_id = None
                    
        except yaml.YAMLError as e:
            logger.error(f"❌ Ошибка парсинга YAML: {e}")
            self.panels = {}
            self.current_panel_id = None
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки panels.yaml: {e}", exc_info=True)
            self.panels = {}
            self.current_panel_id = None
    
    def _save_panels(self):
        """Сохранить конфигурацию панелей в YAML файл"""
        try:
            data = {
                'current_panel': self.current_panel_id,
                'panels': self.panels
            }
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            return True
        except Exception as e:
            print(f"Ошибка сохранения panels.yaml: {e}")
            return False
    
    def get_current_panel(self) -> Optional[dict]:
        """Получить конфигурацию текущей активной панели"""
        if self.current_panel_id and self.current_panel_id in self.panels:
            return self.panels[self.current_panel_id]
        return None
    
    def get_current_panel_id(self) -> Optional[str]:
        """Получить ID текущей активной панели"""
        return self.current_panel_id
    
    def get_all_panels(self) -> dict:
        """Получить все панели"""
        return self.panels
    
    def get_panel(self, panel_id: str) -> Optional[dict]:
        """Получить конфигурацию конкретной панели"""
        return self.panels.get(panel_id)
    
    def add_panel(self, panel_id: str, panel_data: dict) -> bool:
        """Добавить новую панель"""
        try:
            self.panels[panel_id] = panel_data
            if not self.current_panel_id:
                self.current_panel_id = panel_id
            return self._save_panels()
        except Exception as e:
            print(f"Ошибка добавления панели: {e}")
            return False
    
    def remove_panel(self, panel_id: str) -> bool:
        """Удалить панель"""
        if panel_id not in self.panels:
            return False
        
        try:
            del self.panels[panel_id]
            # Если удаляем текущую панель, сбрасываем current_panel_id
            if self.current_panel_id == panel_id:
                # Выбираем первую доступную панель или None
                self.current_panel_id = next(iter(self.panels.keys()), None)
            return self._save_panels()
        except Exception as e:
            print(f"Ошибка удаления панели: {e}")
            return False
    
    def switch_panel(self, panel_id: str) -> bool:
        """Переключиться на другую панель"""
        if panel_id not in self.panels:
            return False
        
        try:
            self.current_panel_id = panel_id
            return self._save_panels()
        except Exception as e:
            print(f"Ошибка переключения панели: {e}")
            return False
    
    async def check_panel_status(self, panel_config: dict) -> bool:
        """
        Проверить доступность панели по сети
        Возвращает True если панель доступна, False если нет
        """
        try:
            url = panel_config.get('url', '')
            username = panel_config.get('username', '')
            password = panel_config.get('password', '')
            
            if not url:
                return False
            
            # Формируем URL для проверки (endpoint /login)
            login_url = f"{url.rstrip('/')}/login"
            
            # Создаем сессию с таймаутом
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Пытаемся выполнить POST запрос к /login
                async with session.post(
                    login_url,
                    json={'username': username, 'password': password},
                    ssl=False
                ) as response:
                    # Если получили ответ (любой), панель доступна
                    return response.status in [200, 401, 403]  # 200 - успех, 401/403 - неверные данные, но панель работает
        except asyncio.TimeoutError:
            return False
        except aiohttp.ClientError:
            return False
        except Exception as e:
            print(f"Ошибка проверки панели: {e}")
            return False
    
    async def check_all_panels_status(self) -> dict:
        """
        Проверить статус всех панелей
        Возвращает словарь {panel_id: status}
        """
        statuses = {}
        for panel_id, panel_config in self.panels.items():
            status = await self.check_panel_status(panel_config)
            statuses[panel_id] = status
        return statuses
    
    def create_xui_config_from_panel(self, panel_id: str) -> Optional[XUIConfig]:
        """Создать XUIConfig из конфигурации панели"""
        panel = self.get_panel(panel_id)
        if not panel:
            return None
        
        try:
            return XUIConfig(
                url=panel.get('url', ''),
                username=panel.get('username', ''),
                password=panel.get('password', ''),
                inbound_id=panel.get('inbound_id', 1),
                db_path=panel.get('db_path', '/etc/x-ui/x-ui.db'),
                api_timeout=panel.get('api_timeout', 30),
                version=panel.get('version', 'latest'),
                api_token=panel.get('api_token')
            )
        except Exception as e:
            print(f"Ошибка создания XUIConfig: {e}")
            return None
    
    def save_current_panel_to_env(self) -> bool:
        """
        Сохранить текущую панель в переменные окружения
        (для совместимости с существующим кодом)
        """
        panel = self.get_current_panel()
        if not panel:
            return False
        
        try:
            os.environ['XUI_URL'] = panel.get('url', '')
            os.environ['XUI_USERNAME'] = panel.get('username', '')
            os.environ['XUI_PASSWORD'] = panel.get('password', '')
            os.environ['XUI_VERSION'] = panel.get('version', 'latest')
            os.environ['INBOUND_ID'] = str(panel.get('inbound_id', 1))
            os.environ['XUI_DB_PATH'] = panel.get('db_path', '/etc/x-ui/x-ui.db')
            
            api_token = panel.get('api_token')
            if api_token:
                os.environ['XUI_API_TOKEN'] = api_token
            
            return True
        except Exception as e:
            print(f"Ошибка сохранения в env: {e}")
            return False



class Config:
    def __init__(self):
        self.bot = BotConfig.from_env()
        self.xui = XUIConfig.from_env()
        self.vpn = VPNConfig.from_env()
        self.limits = LimitsConfig.from_env()
        self.database = DatabaseConfig.from_env()
        self.logging = LoggingConfig.from_env()
        self.users_db = UserDatabase()
        self.panel_manager = PanelManager()
        self._validate()

    def _validate(self):
        if not self.bot.token or self.bot.token == "YOUR_BOT_TOKEN_HERE":
            raise ValueError("XUI_BOT_TOKEN или TELEGRAM_BOT_TOKEN не указан")
        if not self.xui.password or self.xui.password == "your_password_here":
            raise ValueError("XUI_PASSWORD не указан")
        if self.vpn.security == "reality":
            if not self.vpn.reality_public_key:
                print("⚠️ REALITY_PUBLIC_KEY не указан, проверьте настройки")
            if not self.vpn.reality_short_id:
                print("⚠️ REALITY_SHORT_ID не указан, проверьте настройки")

    def is_admin(self, user_id: int) -> bool:
        return user_id == self.users_db.get_main_admin()

    def is_allowed(self, user_id: int) -> bool:
        return self.users_db.is_allowed(user_id)

    def display(self) -> str:
        user_count = self.users_db.get_user_count()
        return f"""
📋 <b>Конфигурация бота:</b>

<b>Telegram Bot:</b>
• Admin ID: {self.users_db.get_main_admin()}
• Admin Username: {self.bot.admin_username}
• Разрешено пользователей: {user_count}

<b>X-UI Panel:</b>
• URL: {self.xui.url}
• Inbound ID: {self.xui.inbound_id}

<b>VPN Settings:</b>
• Server: {self.vpn.server_address}:{self.vpn.server_port}
• Security: {self.vpn.security}
• Transport: {self.vpn.transport}
• SNI: {self.vpn.get_sni()}
• Fingerprint: {self.vpn.get_fingerprint()}

<b>Limits:</b>
• Max Traffic: {self.limits.max_traffic_gb} GB
• Max Days: {self.limits.max_days}
• Default Traffic: {self.limits.default_traffic_gb} GB
• Default Days: {self.limits.default_days}
"""


config = Config()