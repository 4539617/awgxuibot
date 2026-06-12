# utils.py
import aiohttp
import asyncio
import logging
import subprocess
import json
import uuid
import time
import ssl
import re
import tempfile
import os
from typing import Dict
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)


def validate_uuid(uuid_string: str) -> bool:
    """Валидация UUID для защиты от injection"""
    uuid_pattern = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', re.IGNORECASE)
    return bool(uuid_pattern.match(uuid_string))


def validate_email(email: str) -> bool:
    """Валидация email для защиты от injection"""
    # Разрешаем буквы, цифры, подчеркивание, дефис, точку
    email_pattern = re.compile(r'^[a-zA-Z0-9_.-]+$')
    return bool(email_pattern.match(email)) and len(email) <= 100


def sanitize_path(path: str, allowed_prefix: str = '/etc/x-ui/') -> str:
    """Валидация пути к файлу для защиты от path traversal"""
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(allowed_prefix):
        raise ValueError(f"Invalid path: {path}")
    return abs_path

def get_inbound_reality_settings(db_path: str, inbound_id: int) -> Dict:
    """
    Извлекает реальные параметры Reality из настроек inbound в БД
    Возвращает словарь с параметрами: sni, fingerprint, public_key, short_id
    """
    try:
        # Получаем streamSettings из inbound
        sql_get = f"""sqlite3 {db_path} "SELECT stream_settings FROM inbounds WHERE id={inbound_id};" """
        result = subprocess.run(sql_get, shell=True, capture_output=True, text=True)
        
        if result.returncode != 0 or not result.stdout:
            logger.warning(f"Не удалось получить stream_settings для inbound id={inbound_id}")
            return {}
        
        stream_settings = json.loads(result.stdout.strip())
        
        # Извлекаем параметры Reality
        reality_settings = {}
        
        # Проверяем security type
        security = stream_settings.get('security', '')
        if security == 'reality':
            reality_config = stream_settings.get('realitySettings', {})
            
            # Извлекаем параметры
            # SNI из serverNames
            reality_settings['sni'] = reality_config.get('serverNames', [''])[0] if reality_config.get('serverNames') else ''
            
            # Fingerprint может быть в разных местах
            # 1. В realitySettings.settings.fingerprint (новый формат)
            # 2. В realitySettings.fingerprint (старый формат)
            settings_obj = reality_config.get('settings', {})
            if isinstance(settings_obj, dict):
                reality_settings['fingerprint'] = settings_obj.get('fingerprint', reality_config.get('fingerprint', 'chrome'))
                reality_settings['public_key'] = settings_obj.get('publicKey', reality_config.get('publicKey', ''))
            else:
                reality_settings['fingerprint'] = reality_config.get('fingerprint', 'chrome')
                reality_settings['public_key'] = reality_config.get('publicKey', '')
            
            # Short ID из shortIds
            reality_settings['short_id'] = reality_config.get('shortIds', [''])[0] if reality_config.get('shortIds') else ''
            
            logger.info(f"Извлечены параметры Reality из inbound {inbound_id}: SNI={reality_settings['sni']}, FP={reality_settings['fingerprint']}, PBK={reality_settings['public_key'][:20]}..., SID={reality_settings['short_id']}")
        
        return reality_settings
        
    except Exception as e:
        logger.error(f"Ошибка при извлечении параметров Reality: {e}")
        return {}



class XUIClient:
    def __init__(self, config):
        self.config = config
        self.session = None
        self.cookies = None
    
    async def _get_session(self):
        """Создание сессии с SSL контекстом и cookie jar"""
        if self.session is None:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            
            # Создаём cookie jar с unsafe=True для работы с IP адресами
            cookie_jar = aiohttp.CookieJar(unsafe=True)
            
            self.session = aiohttp.ClientSession(
                connector=connector,
                cookie_jar=cookie_jar,
                timeout=aiohttp.ClientTimeout(total=self.config.xui.api_timeout)
            )
        return self.session
    
    async def login(self) -> bool:
        """Авторизация в панели 3x-ui с автоматическим переключением HTTPS/HTTP"""
        await self._get_session()
        
        login_data = {
            "username": self.config.xui.username,
            "password": self.config.xui.password
        }
        
        # Пробуем подключиться по URL из конфига
        login_url = f"{self.config.xui.url}/login"
        
        try:
            async with self.session.post(login_url, json=login_data) as resp:
                if resp.status == 200:
                    self.cookies = self.session.cookie_jar
                    logger.info(f"Успешная авторизация в 3x-ui ({self.config.xui.url})")
                    return True
                else:
                    text = await resp.text()
                    logger.warning(f"Ошибка авторизации по {self.config.xui.url}: {resp.status} - {text[:200]}")
        except Exception as e:
            logger.warning(f"Ошибка подключения к {self.config.xui.url}: {e}")
        
        # Если не удалось подключиться, пробуем альтернативный протокол
        if self.config.xui.url.startswith("https://"):
            # Пробуем HTTP
            alt_url = self.config.xui.url.replace("https://", "http://")
            logger.info(f"Пробуем подключиться по HTTP: {alt_url}")
        elif self.config.xui.url.startswith("http://"):
            # Пробуем HTTPS
            alt_url = self.config.xui.url.replace("http://", "https://")
            logger.info(f"Пробуем подключиться по HTTPS: {alt_url}")
        else:
            logger.error("Некорректный URL в конфигурации")
            return False
        
        try:
            alt_login_url = f"{alt_url}/login"
            async with self.session.post(alt_login_url, json=login_data) as resp:
                if resp.status == 200:
                    self.cookies = self.session.cookie_jar
                    # Обновляем URL в конфиге для последующих запросов
                    self.config.xui.url = alt_url
                    logger.info(f"✅ Успешная авторизация в 3x-ui ({alt_url})")
                    logger.info(f"ℹ️  URL обновлён на {alt_url}")
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"Ошибка авторизации по {alt_url}: {resp.status} - {text[:200]}")
                    return False
        except Exception as e:
            logger.error(f"Ошибка подключения к {alt_url}: {e}")
            return False
    async def _restart_xui_service(self) -> bool:
        """
        Перезапуск Xray сервиса через API панели X-UI
        """
        logger.info("🔄 Перезапуск Xray через API панели X-UI...")
        
        # Основной рабочий endpoint
        restart_endpoint = "/panel/api/server/restartXrayService"
        
        # Заголовки для API запроса
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest'
        }
        
        try:
            restart_url = f"{self.config.xui.url}{restart_endpoint}"
            logger.info(f"Отправка запроса: {restart_url}")
            
            async with self.session.post(restart_url, headers=headers) as resp:
                if resp.status == 200:
                    try:
                        result = await resp.json()
                        if result.get('success'):
                            logger.info(f"✅ Xray успешно перезапущен через API")
                            await asyncio.sleep(3)  # Даём время на перезапуск
                            return True
                        else:
                            logger.warning(f"API вернул success=false: {result}")
                            return False
                    except Exception as e:
                        logger.error(f"Ошибка парсинга ответа: {e}")
                        return False
                else:
                    text = await resp.text()
                    logger.error(f"Ошибка перезапуска: статус {resp.status}, ответ: {text[:200]}")
                    return False
        except Exception as e:
            logger.error(f"Ошибка при перезапуске через API: {e}")
            return False


    async def add_client(self, email: str, total_gb: int, expiry_days: float, comment: str = None) -> Dict:
        """Создание нового клиента через API 3x-ui с комментарием"""
        if not self.session:
            if not await self.login():
                return {"success": False, "error": "Не удалось авторизоваться"}

        client_uuid = str(uuid.uuid4())
        expiry_time = int((time.time() + expiry_days * 86400) * 1000)
        total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0

        client_comment = comment if comment else f"Created by bot {time.strftime('%Y-%m-%d %H:%M:%S')}"

        # Определяем flow в зависимости от транспорта и безопасности
        # Flow используется ТОЛЬКО для TCP с Reality или TLS
        # Для xhttp, ws, grpc, httpupgrade flow ВСЕГДА пустой
        if self.config.vpn.transport == "tcp" and self.config.vpn.security in ["reality", "tls"]:
            flow = "xtls-rprx-vision"
        else:
            flow = ""

        client_data = {
            "id": self.config.xui.inbound_id,
            "settings": json.dumps({
                "clients": [{
                    "id": client_uuid,
                    "email": email,
                    "limitIp": 0,
                    "totalGB": total_bytes,
                    "expiryTime": expiry_time,
                    "enable": True,
                    "flow": flow,
                    "tgId": "",
                    "subId": "",
                    "comment": client_comment
                }]
            })
        }

        base_url = self.config.xui.url.rstrip('/')
        endpoints = [
            f"{base_url}/panel/api/inbounds/addClient",
            f"{base_url}/xui/API/inbounds/addClient",
            f"{base_url}/panel/inbound/addClient",
        ]
        
        for endpoint in endpoints:
            try:
                logger.info(f"Пробуем endpoint: {endpoint}")
                async with self.session.post(endpoint, json=client_data) as resp:
                    response_text = await resp.text()
                    logger.info(f"Ответ: {resp.status} - {response_text[:200]}")
                    
                    if resp.status == 200:
                        try:
                            result = json.loads(response_text)
                            if result.get('success'):
                                logger.info(f"Клиент {email} создан через {endpoint}")
                                return {"success": True, "uuid": client_uuid}
                            else:
                                logger.warning(f"API вернул ошибку: {result.get('msg', 'Unknown error')}")
                                continue
                        except:
                            pass
                    elif resp.status in [301, 302]:
                        continue
            except Exception as e:
                logger.error(f"Ошибка на {endpoint}: {e}")
                continue
        
        logger.warning("API не работает, пробуем добавить через SQL")
        return await self.add_client_via_sql(email, total_gb, expiry_days, client_uuid, client_comment)
    
    async def add_client_via_sql(self, email: str, total_gb: int, expiry_days: float, client_uuid: str, client_comment: str) -> Dict:
        """Добавление клиента напрямую в БД через обновление JSON в поле settings"""
        try:
            expiry_time = int((time.time() + expiry_days * 86400) * 1000)
            total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0
            
            # Определяем flow в зависимости от транспорта и безопасности
            # Flow используется ТОЛЬКО для TCP с Reality или TLS
            if self.config.vpn.transport == "tcp" and self.config.vpn.security in ["reality", "tls"]:
                flow = "xtls-rprx-vision"
            else:
                flow = ""
            
            # Валидация пути к БД
            db_path = sanitize_path(self.config.xui.db_path)
            
            # Получаем текущие настройки inbound
            sql_get = f"""sqlite3 {db_path} "SELECT settings FROM inbounds WHERE id={self.config.xui.inbound_id};" """
            result = subprocess.run(sql_get, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0 or not result.stdout:
                logger.error(f"Не удалось получить настройки inbound id={self.config.xui.inbound_id}")
                return {"success": False, "error": "Inbound не найден в базе данных"}
            
            # Парсим JSON
            settings = json.loads(result.stdout.strip())
            clients = settings.get('clients', [])
            
            # Создаем нового клиента
            new_client = {
                "id": client_uuid,
                "email": email,
                "limitIp": 0,
                "totalGB": total_bytes,
                "expiryTime": expiry_time,
                "enable": True,
                "flow": flow,
                "tgId": "",
                "subId": "",
                "comment": client_comment,
                "reset": 0,
                "created_at": int(time.time() * 1000),
                "updated_at": int(time.time() * 1000)
            }
            
            # Добавляем клиента в список
            clients.append(new_client)
            settings['clients'] = clients
            
            # Сохраняем обновленные настройки через временный файл
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
                json.dump(settings, f, ensure_ascii=False)
                temp_file = f.name
            
            try:
                # Обновляем настройки в БД
                sql_update = f"""sqlite3 {db_path} "UPDATE inbounds SET settings=readfile('{temp_file}') WHERE id={self.config.xui.inbound_id};" """
                result = subprocess.run(sql_update, shell=True, capture_output=True, text=True)
                
                if result.returncode == 0:
                    logger.info(f"Клиент {email} успешно добавлен в inbound id={self.config.xui.inbound_id} через SQL")
                    
                    # Также добавляем запись в client_traffics для отслеживания трафика
                    sql_traffic = f"""sqlite3 {db_path} "INSERT OR IGNORE INTO client_traffics (inbound_id, enable, email, up, down, all_time, expiry_time, total, reset) VALUES ({self.config.xui.inbound_id}, 1, '{email}', 0, 0, 0, {expiry_time}, {total_bytes}, 0);" """
                    subprocess.run(sql_traffic, shell=True, capture_output=True, text=True)
                    
                    # Перезапускаем X-UI для применения изменений
                    logger.info("Перезапускаем X-UI для применения изменений...")
                    
                    # Проверяем авторизацию перед перезапуском
                    if not self.session or not self.cookies:
                        logger.info("Повторная авторизация перед перезапуском...")
                        if not await self.login():
                            logger.error("Не удалось авторизоваться для перезапуска")
                            return {"success": True, "uuid": client_uuid, "restart": False}
                    
                    restart_success = await self._restart_xui_service()
                    
                    if not restart_success:
                        logger.warning("⚠️ Не удалось перезапустить автоматически. Требуется ручной перезапуск: systemctl restart x-ui")
                    
                    return {"success": True, "uuid": client_uuid}
                else:
                    logger.error(f"Ошибка обновления настроек inbound: {result.stderr}")
                    return {"success": False, "error": "Не удалось обновить настройки inbound"}
            finally:
                # Удаляем временный файл
                try:
                    os.unlink(temp_file)
                except Exception as e:
                    logger.warning(f"Не удалось удалить временный файл: {e}")
                    
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON настроек inbound: {e}")
            return {"success": False, "error": "Некорректный формат настроек inbound"}
        except Exception as e:
            logger.error(f"Ошибка добавления клиента через SQL: {e}")
            return {"success": False, "error": f"Ошибка: {str(e)}"}

    async def delete_client(self, client_uuid: str, email: str = None) -> bool:
        """Удаление клиента через API или SQL"""
        if not self.session:
            if not await self.login():
                return False

        # Пробуем удалить через API
        base_url = self.config.xui.url.rstrip('/')
        endpoints = [
            f"{base_url}/xui/API/inbounds/{self.config.xui.inbound_id}/delClient/{client_uuid}",
            f"{base_url}/panel/api/inbounds/delClient/{client_uuid}",
        ]
        
        for endpoint in endpoints:
            try:
                async with self.session.post(endpoint) as resp:
                    if resp.status == 200:
                        response_text = await resp.text()
                        try:
                            result = json.loads(response_text)
                            if result.get('success'):
                                logger.info(f"Клиент {email or client_uuid} удален через API")
                                return True
                        except:
                            pass
            except Exception as e:
                logger.error(f"Ошибка удаления через API: {e}")
                continue
        
        # Если API не сработал, удаляем через SQL
        logger.info(f"Удаляем клиента {email or client_uuid} через SQL")
        return await self.delete_client_via_sql(client_uuid, email)
    
    async def delete_client_via_sql(self, client_uuid: str, email: str = None) -> bool:
        """Удаление клиента напрямую из БД"""
        try:
            # Валидация UUID
            if not validate_uuid(client_uuid):
                logger.error(f"Invalid UUID format: {client_uuid}")
                return False
            
            # Валидация email если передан
            if email and not validate_email(email):
                logger.error(f"Invalid email format: {email}")
                return False
            
            # Валидация пути к БД
            db_path = sanitize_path(self.config.xui.db_path)
            
            # Получаем текущие настройки inbound
            sql_get = f"""sqlite3 {db_path} "SELECT settings FROM inbounds WHERE id={self.config.xui.inbound_id};" """
            result = subprocess.run(sql_get, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0 or not result.stdout:
                logger.error(f"Не удалось получить настройки inbound")
                return False
            
            # Парсим JSON
            settings = json.loads(result.stdout.strip())
            clients = settings.get('clients', [])
            
            # Удаляем клиента из списка
            original_count = len(clients)
            clients = [c for c in clients if c.get('id') != client_uuid]
            
            if len(clients) == original_count:
                logger.warning(f"Клиент {email or client_uuid} не найден в настройках")
                return False
            
            # Обновляем настройки
            settings['clients'] = clients
            new_settings = json.dumps(settings, ensure_ascii=False)
            
            # Экранируем для SQL (заменяем одинарные кавычки)
            escaped_settings = new_settings.replace("'", "''")
            
            # Записываем обратно в БД через временный файл (безопаснее)
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
                f.write(new_settings)
                temp_file = f.name
            
            try:
                # Используем .read для безопасной записи JSON
                sql_update = f"""sqlite3 {db_path} "UPDATE inbounds SET settings=readfile('{temp_file}') WHERE id={self.config.xui.inbound_id};" """
                result = subprocess.run(sql_update, shell=True, capture_output=True, text=True)
            finally:
                # Удаляем временный файл
                try:
                    os.unlink(temp_file)
                except Exception as e:
                    logger.warning(f"Не удалось удалить временный файл: {e}")
            
            if result.returncode == 0:
                logger.info(f"Клиент {email or client_uuid} удален из настроек inbound")
                
                # Также удаляем из client_traffics если есть (UUID уже валидирован)
                # db_path уже определен выше в функции
                sql_traffic = f"""sqlite3 {db_path} "DELETE FROM client_traffics WHERE id='{client_uuid}';" """
                subprocess.run(sql_traffic, shell=True, capture_output=True, text=True)
                
                return True
            else:
                logger.error(f"Ошибка обновления настроек: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка удаления клиента: {e}")
            return False
    
    async def get_expired_clients(self) -> list:
        """Получение списка истекших клиентов из JSON настроек inbound"""
        try:
            current_time = int(time.time() * 1000)  # Текущее время в миллисекундах
            
            # Валидация пути к БД
            db_path = sanitize_path(self.config.xui.db_path)
            
            # Получаем настройки inbound
            sql_get = f"""sqlite3 {db_path} "SELECT settings FROM inbounds WHERE id={self.config.xui.inbound_id};" """
            result = subprocess.run(sql_get, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0 or not result.stdout:
                logger.error(f"Не удалось получить настройки inbound")
                return []
            
            # Парсим JSON
            settings = json.loads(result.stdout.strip())
            clients = settings.get('clients', [])
            
            # Фильтруем истекшие клиенты
            expired_clients = []
            for client in clients:
                expiry_time = client.get('expiryTime', 0)
                if expiry_time > 0 and expiry_time < current_time:
                    expired_clients.append({
                        'uuid': client.get('id'),
                        'email': client.get('email'),
                        'expiry_time': expiry_time
                    })
            
            return expired_clients
            
        except Exception as e:
            logger.error(f"Ошибка получения истекших клиентов: {e}")
            return []
    async def get_all_clients(self) -> list:
        """Получение всех клиентов с полной информацией"""
        try:
            current_time = int(time.time() * 1000)  # Текущее время в миллисекундах
            
            # Валидация пути к БД
            db_path = sanitize_path(self.config.xui.db_path)
            
            # Получаем настройки inbound
            sql_get = f"""sqlite3 {db_path} "SELECT settings FROM inbounds WHERE id={self.config.xui.inbound_id};" """
            result = subprocess.run(sql_get, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0 or not result.stdout:
                logger.error(f"Не удалось получить настройки inbound")
                return []
            
            # Парсим JSON
            settings = json.loads(result.stdout.strip())
            clients = settings.get('clients', [])
            
            # Получаем данные о трафике из client_traffics по email
            sql_traffic = f"""sqlite3 {db_path} "SELECT email, up, down, all_time FROM client_traffics WHERE inbound_id={self.config.xui.inbound_id};" """
            traffic_result = subprocess.run(sql_traffic, shell=True, capture_output=True, text=True)
            
            # Создаем словарь трафика по email
            traffic_data = {}
            if traffic_result.returncode == 0 and traffic_result.stdout:
                for line in traffic_result.stdout.strip().split('\n'):
                    if line:
                        parts = line.split('|')
                        if len(parts) >= 4:
                            email = parts[0]
                            up = int(parts[1]) if parts[1] else 0
                            down = int(parts[2]) if parts[2] else 0
                            all_time = int(parts[3]) if parts[3] else 0
                            # Используем all_time если up и down равны 0
                            if up == 0 and down == 0 and all_time > 0:
                                # all_time содержит общий трафик, делим пополам для up/down
                                up = all_time // 2
                                down = all_time // 2
                            traffic_data[email] = {'up': up, 'down': down}
            
            # Обрабатываем каждого клиента
            all_clients = []
            for client in clients:
                client_uuid = client.get('id')
                client_email = client.get('email')
                expiry_time = client.get('expiryTime', 0)
                enable = client.get('enable', True)
                
                # Получаем трафик для клиента по email
                traffic = traffic_data.get(client_email, {'up': 0, 'down': 0})
                
                # Определяем статус
                if expiry_time > 0 and expiry_time < current_time:
                    status = 'expired'  # Просрочен
                elif not enable:
                    status = 'inactive'  # Неактивен (выключен)
                else:
                    status = 'active'  # Активен
                
                all_clients.append({
                    'uuid': client_uuid,
                    'email': client.get('email'),
                    'comment': client.get('comment', ''),
                    'enable': enable,
                    'expiryTime': expiry_time,
                    'totalGB': client.get('totalGB', 0),
                    'status': status,
                    'up': traffic['up'],
                    'down': traffic['down']
                })
            
            return all_clients
            
        except Exception as e:
            logger.error(f"Ошибка получения всех клиентов: {e}")
            return []
    
    async def get_client_details(self, client_uuid: str) -> dict:
        """Получение детальной информации о клиенте"""
        try:
            all_clients = await self.get_all_clients()
            
            for client in all_clients:
                if client['uuid'] == client_uuid:
                    return client
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения деталей клиента: {e}")
    
    async def get_user_clients_by_username(self, username: str) -> list:
        """Получение всех ключей пользователя по username (ищет в email)"""
        try:
            if not username:
                return []
            
            # Приводим username к нижнему регистру для поиска
            username_lower = username.lower()
            
            # Получаем все клиенты
            all_clients = await self.get_all_clients()
            
            # Фильтруем клиенты по username в email
            # Поддерживаем два формата:
            # 1. username_random (обычные ключи)
            # 2. temp_username_random (временные ключи)
            user_clients = []
            for client in all_clients:
                email = client.get('email', '').lower()
                
                # Проверяем формат: username_random
                if '_' in email:
                    parts = email.split('_')
                    # Первая часть должна быть username
                    if parts[0] == username_lower:
                        user_clients.append(client)
                    # Или это временный ключ: temp_username_random
                    elif len(parts) >= 2 and parts[0] == 'temp' and parts[1] == username_lower:
                        user_clients.append(client)
                elif email == username_lower:
                    # Если email совпадает полностью с username
                    user_clients.append(client)
            
            return user_clients
            
        except Exception as e:
            logger.error(f"Ошибка получения ключей пользователя {username}: {e}")
            return []
    
    async def has_active_keys(self, username: str) -> bool:
        """Проверка наличия активных ключей у пользователя"""
        try:
            user_clients = await self.get_user_clients_by_username(username)
            
            # Проверяем есть ли хотя бы один активный ключ
            for client in user_clients:
                if client['status'] == 'active':
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Ошибка проверки активных ключей для {username}: {e}")
            return False

            return None
    
    async def update_client_status(self, client_uuid: str, enable: bool) -> bool:
        """Включение/выключение клиента"""
        try:
            # Получаем текущие настройки inbound
            sql_get = f"""sqlite3 {self.config.xui.db_path} "SELECT settings FROM inbounds WHERE id={self.config.xui.inbound_id};" """
            result = subprocess.run(sql_get, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0 or not result.stdout:
                logger.error(f"Не удалось получить настройки inbound")
                return False
            
            # Парсим JSON
            settings = json.loads(result.stdout.strip())
            clients = settings.get('clients', [])
            
            # Находим и обновляем клиента
            client_found = False
            for client in clients:
                if client.get('id') == client_uuid:
                    client['enable'] = enable
                    client_found = True
                    break
            
            if not client_found:
                logger.warning(f"Клиент {client_uuid} не найден")
                return False
            
            # Обновляем настройки
            settings['clients'] = clients
            new_settings = json.dumps(settings, ensure_ascii=False)
            
            # Записываем обратно в БД через временный файл
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
                f.write(new_settings)
                temp_file = f.name
            
            # Используем .read для безопасной записи JSON
            sql_update = f"""sqlite3 {self.config.xui.db_path} "UPDATE inbounds SET settings=readfile('{temp_file}') WHERE id={self.config.xui.inbound_id};" """
            result = subprocess.run(sql_update, shell=True, capture_output=True, text=True)
            
            # Удаляем временный файл
            try:
                os.unlink(temp_file)
            except:
                pass
            
            if result.returncode == 0:
                status_text = "включен" if enable else "выключен"
                logger.info(f"Клиент {client_uuid} {status_text}")
                
                return True
            else:
                logger.error(f"Ошибка обновления статуса: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка обновления статуса клиента: {e}")
            return False



def generate_vless_link(client_uuid: str, email: str, vpn_config, inbound_id: int) -> str:
    """Универсальная генерация VLESS ссылки в зависимости от настроек
    
    Поддерживаемые сценарии:
    1. xhttp + reality
    2. tcp + reality
    3. tcp + tls
    """
    import urllib.parse
    
    # Получаем реальные параметры Reality из inbound (если используется Reality)
    reality_params = {}
    if vpn_config.security == "reality":
        reality_params = get_inbound_reality_settings(vpn_config.xui_db_path, inbound_id)
        logger.info(f"Используем параметры Reality из inbound: {reality_params}")
    
    # ВАЛИДАЦИЯ: Проверяем и устанавливаем transport
    transport = vpn_config.transport if vpn_config.transport else "tcp"
    if not transport or transport.strip() == "":
        logger.warning(f"⚠️ Transport не установлен или пустой, используем значение по умолчанию: tcp")
        transport = "tcp"
    
    # Валидация допустимых значений transport
    valid_transports = ["tcp", "xhttp", "ws", "grpc", "httpupgrade", "splithttp"]
    if transport not in valid_transports:
        logger.warning(f"⚠️ Неизвестный transport '{transport}', используем tcp")
        transport = "tcp"
    
    logger.info(f"🔗 Генерация VLESS ссылки: transport={transport}, security={vpn_config.security}")
    
    base = f"vless://{client_uuid}@{vpn_config.server_address}:{vpn_config.server_port}"
    
    # Начинаем с type (транспорт должен быть первым)
    params = f"type={transport}"
    
    # Encryption всегда none для VLESS
    params += "&encryption=none"
    
    # Security
    params += f"&security={vpn_config.security}"
    
    # ===== СЦЕНАРИЙ 1 и 2: Reality (xhttp или tcp) =====
    if vpn_config.security == "reality":
        # Fingerprint для Reality
        if reality_params.get('fingerprint'):
            params += f"&fp={reality_params['fingerprint']}"
        else:
            params += f"&fp={vpn_config.reality_fingerprint}"
        
        # Public key (pbk) - ОБЯЗАТЕЛЬНЫЙ параметр для Reality
        if reality_params.get('public_key'):
            params += f"&pbk={reality_params['public_key']}"
        else:
            reality_public_key = getattr(vpn_config, 'reality_public_key', '')
            if reality_public_key:
                params += f"&pbk={reality_public_key}"
            else:
                logger.error("⚠️ REALITY_PUBLIC_KEY не найден!")
        
        # SNI для Reality
        if reality_params.get('sni'):
            params += f"&sni={reality_params['sni']}"
        else:
            if vpn_config.reality_sni:
                params += f"&sni={vpn_config.reality_sni}"
        
        # Short ID (sid) для Reality
        if reality_params.get('short_id'):
            params += f"&sid={reality_params['short_id']}"
        else:
            reality_short_id = getattr(vpn_config, 'reality_short_id', '')
            if reality_short_id:
                params += f"&sid={reality_short_id}"
        
        # spiderX (SpiderX path) для Reality
        params += "&spx=%2F"
        
        # Flow для TCP + Reality
        if transport == "tcp":
            params += "&flow=xtls-rprx-vision"
            logger.debug(f"Добавлен flow для TCP+Reality")
    
    # ===== СЦЕНАРИЙ 3: TLS (обычно с tcp) =====
    elif vpn_config.security == "tls":
        # Fingerprint для TLS
        params += f"&fp={vpn_config.tls_fingerprint}"
        
        # ALPN - ОБЯЗАТЕЛЬНЫЙ параметр для TLS (URL-encoded)
        tls_alpn = getattr(vpn_config, 'tls_alpn', 'http/1.1')
        if tls_alpn:
            # URL-encode ALPN (http/1.1 -> http%2F1.1)
            alpn_encoded = urllib.parse.quote(tls_alpn, safe='')
            params += f"&alpn={alpn_encoded}"
        
        # SNI для TLS (если указан)
        if vpn_config.tls_sni:
            params += f"&sni={vpn_config.tls_sni}"
        
        # Flow для TCP + TLS
        if transport == "tcp":
            params += "&flow=xtls-rprx-vision"
            logger.debug(f"Добавлен flow для TCP+TLS")
    
    # ===== Дополнительные параметры для xHTTP =====
    if transport == "xhttp":
        xhttp_mode = getattr(vpn_config, 'xhttp_mode', 'auto')
        params += f"&mode={xhttp_mode}"
        # Для xHTTP добавляем дополнительные параметры
        params += "&path=%2F&host="
        logger.debug(f"Добавлены параметры xhttp: mode={xhttp_mode}")
    
    vless_link = f"{base}?{params}#{urllib.parse.quote(email)}"
    logger.info(f"✅ VLESS ссылка сгенерирована успешно для {email}")
    logger.debug(f"Полная ссылка: {vless_link}")
    return vless_link


def setup_logging(logging_config):
    """Настройка логирования"""
    import os
    from pathlib import Path
    
    log_level = getattr(logging, logging_config.level.upper())
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(handler)
    
    if logging_config.file_enabled:
        try:
            # Определяем путь к логам
            log_path = logging_config.file_path
            
            # Если путь относительный и мы в Docker (/app существует), используем /app/logs
            if not os.path.isabs(log_path) and os.path.exists('/app'):
                log_dir = '/app/logs'
                os.makedirs(log_dir, exist_ok=True)
                # Добавляем префикс xuibot_ к имени файла
                log_filename = os.path.basename(log_path)
                if not log_filename.startswith('xuibot_'):
                    log_filename = f'xuibot_{log_filename}'
                log_path = os.path.join(log_dir, log_filename)
            else:
                # Создаём директорию если нужно
                log_dir = os.path.dirname(log_path)
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)
            
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=logging_config.max_size_mb * 1024 * 1024,
                backupCount=logging_config.backup_count
            )
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            print(f"📝 Логи сохраняются в: {log_path}")
        except Exception as e:
            print(f"Ошибка создания лог-файла: {e}")