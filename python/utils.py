# utils.py
import aiohttp
import logging
import subprocess
import json
import uuid
import time
import ssl
import re
import tempfile
import os
import sqlite3
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


class XUIClient:
    def __init__(self, config):
        self.config = config
        self.session = None
        self.cookies = None
        self._detected_version = None
        self._reality_settings_cache = None
    
    def get_reality_settings_from_db(self, inbound_id: int) -> dict:
        """Читает настройки Reality из базы данных для указанного inbound"""
        if self._reality_settings_cache:
            return self._reality_settings_cache
        
        try:
            db_path = sanitize_path(self.config.xui.db_path)
            
            # Читаем stream_settings из inbound
            query = f"""sqlite3 {db_path} "SELECT stream_settings FROM inbounds WHERE id={inbound_id};" """
            result = subprocess.run(query, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0 or not result.stdout.strip():
                logger.warning(f"Не удалось прочитать stream_settings для inbound {inbound_id}")
                return {}
            
            stream_settings = json.loads(result.stdout.strip())
            
            # Извлекаем Reality настройки
            reality_settings = stream_settings.get('realitySettings', {})
            settings = reality_settings.get('settings', {})
            
            # Извлекаем xHTTP настройки
            xhttp_settings = stream_settings.get('xhttpSettings', {})
            
            # Формируем результат
            result_settings = {
                'security': stream_settings.get('security', 'reality'),
                'network': stream_settings.get('network', 'xhttp'),
                'sni': reality_settings.get('serverNames', [''])[0] if reality_settings.get('serverNames') else '',
                'fingerprint': settings.get('fingerprint', 'edge'),
                'public_key': settings.get('publicKey', ''),
                'short_id': reality_settings.get('shortIds', [''])[0] if reality_settings.get('shortIds') else '',
                'spider_x': settings.get('spiderX', '/'),
                'xhttp_path': xhttp_settings.get('path', '/'),
                'xhttp_mode': xhttp_settings.get('mode', 'auto'),
                'xhttp_host': xhttp_settings.get('host', ''),
                'x_padding_bytes': xhttp_settings.get('xPaddingBytes', '100-1000'),
                'sc_max_each_post_bytes': xhttp_settings.get('scMaxEachPostBytes', '1000000'),
                'sc_min_posts_interval_ms': xhttp_settings.get('scMinPostsIntervalMs', '30')
            }
            
            # Кешируем результат
            self._reality_settings_cache = result_settings
            logger.info(f"Reality настройки из БД: {result_settings}")
            
            return result_settings
            
        except Exception as e:
            logger.error(f"Ошибка чтения Reality настроек из БД: {e}")
            return {}
        self._reality_settings_cache = None
    
    def _get_api_endpoints(self, endpoint_type: str) -> list:
        """
        Возвращает список API endpoints в зависимости от версии панели
        
        Args:
            endpoint_type: тип endpoint ('add_client', 'delete_client', 'login')
        
        Returns:
            list: список URL endpoints для попытки подключения
        """
        base_url = self.config.xui.url.rstrip('/')
        
        # Определяем версию
        is_v2 = self.config.xui.is_v2()
        is_v3_new = self.config.xui.is_v3_new_api()
        
        if endpoint_type == 'add_client':
            if is_v2:
                # v2.9.4 использует старые endpoints
                return [
                    f"{base_url}/xui/inbound/addClient",
                    f"{base_url}/xui/API/inbounds/addClient",
                ]
            elif is_v3_new:
                # v3.2.8+ использует новый API
                return [
                    f"{base_url}/panel/api/clients/add",  # Новый API для 3.2.8+
                    f"{base_url}/panel/api/inbounds/addClient",  # Fallback на старый
                ]
            else:
                # v3.0-3.2.7 используют старый API
                return [
                    f"{base_url}/panel/api/inbounds/addClient",
                    f"{base_url}/xui/API/inbounds/addClient",
                ]
        
        elif endpoint_type == 'delete_client':
            client_uuid = self.config.xui.inbound_id  # Будет передан отдельно
            if is_v2:
                return [
                    f"{base_url}/xui/inbound/delClient/{{uuid}}",
                    f"{base_url}/xui/API/inbounds/{{inbound_id}}/delClient/{{uuid}}",
                ]
            else:
                return [
                    f"{base_url}/panel/api/inbounds/delClient/{{uuid}}",
                    f"{base_url}/xui/API/inbounds/{{inbound_id}}/delClient/{{uuid}}",
                ]
        
        elif endpoint_type == 'login':
            if is_v2:
                return [
                    f"{base_url}/login",
                    f"{base_url}/xui/login",
                ]
            else:
                return [
                    f"{base_url}/panel/login",
                    f"{base_url}/login",
                ]
        
        return []
    
    async def _get_session(self):
        """Создание сессии с SSL контекстом"""
        if self.session is None:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=self.config.xui.api_timeout)
            )
        return self.session
    
    async def login(self) -> bool:
        """
        Проверка доступности панели 3x-ui и создание сессии
        В версии 3.2.8 API авторизация через /login не работает из-за CSRF защиты.
        Бот работает напрямую с базой данных через SQL.
        """
        try:
            # Создаем сессию если её нет
            if not self.session:
                await self._get_session()
            
            # Проверяем доступность базы данных
            db_path = sanitize_path(self.config.xui.db_path)
            result = subprocess.run(
                f"sqlite3 {db_path} 'SELECT COUNT(*) FROM inbounds;'",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                logger.info("✅ Подключение к базе данных X-UI успешно")
                logger.info(f"📊 Найдено inbounds: {result.stdout.strip()}")
                return True
            else:
                logger.error(f"❌ Ошибка доступа к базе данных: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("❌ Timeout при подключении к базе данных")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка проверки базы данных: {e}")
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

        # Получаем endpoints в зависимости от версии
        endpoints = self._get_api_endpoints('add_client')
        
        for endpoint in endpoints:
            try:
                logger.info(f"Пробуем endpoint: {endpoint}")
                
                # Формат данных зависит от endpoint
                if "/panel/api/clients/add" in endpoint:
                    # Новый формат для 3.2.8+
                    client_data = {
                        "client": {
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
                        },
                        "inboundIds": [self.config.xui.inbound_id]
                    }
                else:
                    # Старый формат для 3.x и 2.x
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
        """Добавление клиента напрямую в БД (поддержка старой и новой структуры)"""
        try:
            expiry_time = int((time.time() + expiry_days * 86400) * 1000)
            total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0
            created_at = int(time.time() * 1000)
            
            # Определяем flow в зависимости от транспорта и безопасности
            if self.config.vpn.transport == "tcp" and self.config.vpn.security in ["reality", "tls"]:
                flow = "xtls-rprx-vision"
            else:
                flow = ""
            
            # Валидация пути к БД
            db_path = sanitize_path(self.config.xui.db_path)
            
            # Проверяем, какую структуру использует панель
            # Сначала проверяем, есть ли клиенты в settings.clients (старая структура)
            sql_check_settings = f"""sqlite3 {db_path} "SELECT settings FROM inbounds WHERE id={self.config.xui.inbound_id};" """
            result = subprocess.run(sql_check_settings, shell=True, capture_output=True, text=True)
            
            use_old_structure = False
            if result.returncode == 0 and result.stdout.strip():
                try:
                    settings = json.loads(result.stdout.strip())
                    # Если в settings есть ключ clients, значит используется старая структура
                    if 'clients' in settings:
                        use_old_structure = True
                        logger.info("Панель использует старую структуру БД (clients в settings)")
                except:
                    pass
            
            # Проверяем версию БД - есть ли таблица clients
            check_table = f"""sqlite3 {db_path} "SELECT name FROM sqlite_master WHERE type='table' AND name='clients';" """
            result = subprocess.run(check_table, shell=True, capture_output=True, text=True)
            has_clients_table = bool(result.stdout.strip())
            
            if has_clients_table and not use_old_structure:
                # Новая структура БД (3.2.8+)
                logger.info("Используем новую структуру БД (3.2.8+)")
                
                # Генерируем sub_id, password и auth
                import random
                import string
                sub_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
                password = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
                auth = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
                
                # 1. Добавляем в таблицу clients
                sql_insert_client = f"""sqlite3 {db_path} "INSERT INTO clients (email, sub_id, uuid, password, auth, flow, security, reverse, limit_ip, total_gb, expiry_time, enable, tg_id, group_name, comment, reset, created_at, updated_at) VALUES ('{email}', '{sub_id}', '{client_uuid}', '{password}', '{auth}', '{flow}', 'auto', '', 0, {total_bytes}, {expiry_time}, 1, 0, '', '{client_comment}', 0, {created_at}, {created_at});" """
                result = subprocess.run(sql_insert_client, shell=True, capture_output=True, text=True)
                
                if result.returncode != 0:
                    logger.error(f"Ошибка добавления в таблицу clients: {result.stderr}")
                    return {"success": False, "error": "Не удалось добавить клиента в таблицу clients"}
                
                logger.info(f"Клиент добавлен в таблицу clients с полями: sub_id={sub_id}, password={password}, auth={auth}, security=auto")
                
                # 2. Получаем client_id
                sql_get_id = f"""sqlite3 {db_path} "SELECT id FROM clients WHERE email='{email}';" """
                result = subprocess.run(sql_get_id, shell=True, capture_output=True, text=True)
                
                if result.returncode != 0 or not result.stdout.strip():
                    logger.error("Не удалось получить client_id")
                    return {"success": False, "error": "Не удалось получить client_id"}
                
                client_id = int(result.stdout.strip())
                
                # 3. Добавляем связь в client_inbounds
                sql_insert_relation = f"""sqlite3 {db_path} "INSERT INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES ({client_id}, {self.config.xui.inbound_id}, '', {created_at});" """
                result = subprocess.run(sql_insert_relation, shell=True, capture_output=True, text=True)
                
                if result.returncode != 0:
                    logger.error(f"Ошибка добавления связи в client_inbounds: {result.stderr}")
                    # Не критично, продолжаем
                
                # 4. ВАЖНО! Также добавляем в settings.clients для генерации ссылок в панели
                # Панель 3.2.8+ использует гибридную структуру
                try:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute("SELECT settings FROM inbounds WHERE id = ?", (self.config.xui.inbound_id,))
                    result = cursor.fetchone()
                    
                    if result and result[0]:
                        settings = json.loads(result[0])
                        clients_list = settings.get('clients', [])
                        
                        # Создаем клиента для settings.clients
                        settings_client = {
                            "id": client_uuid,
                            "email": email,
                            "limitIp": 0,
                            "totalGB": total_bytes,
                            "expiryTime": expiry_time,
                            "enable": True,
                            "flow": flow,
                            "tgId": 0,
                            "subId": sub_id,
                            "password": password,
                            "auth": auth,
                            "security": "auto",
                            "comment": client_comment,
                            "reset": 0,
                            "created_at": created_at,
                            "updated_at": created_at
                        }
                        
                        clients_list.append(settings_client)
                        settings['clients'] = clients_list
                        
                        # Обновляем settings
                        settings_json = json.dumps(settings, ensure_ascii=False)
                        cursor.execute(
                            "UPDATE inbounds SET settings = ? WHERE id = ?",
                            (settings_json, self.config.xui.inbound_id)
                        )
                        conn.commit()
                        logger.info(f"Клиент также добавлен в settings.clients (всего: {len(clients_list)})")
                    
                    conn.close()
                except Exception as e:
                    logger.error(f"Ошибка добавления в settings.clients: {e}")
                    # Не критично, продолжаем
                
                # Перезапускаем X-UI чтобы панель перечитала settings
                try:
                    logger.info("Перезапускаем X-UI для применения изменений...")
                    result = subprocess.run("systemctl restart x-ui", shell=True, capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        logger.info("X-UI успешно перезапущен")
                    else:
                        logger.warning(f"Не удалось перезапустить X-UI: {result.stderr}")
                except Exception as e:
                    logger.warning(f"Ошибка при перезапуске X-UI: {e}")
                
                logger.info(f"Клиент {email} успешно добавлен в новую структуру БД (client_id={client_id}, sub_id={sub_id})")
                return {"success": True, "uuid": client_uuid}
                
            else:
                # Старая структура БД (до 3.2.8)
                logger.info("Используем старую структуру БД (до 3.2.8)")
                
                # Генерируем дополнительные поля как в панели
                import random
                import string
                sub_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
                password = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
                auth = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
                
                # Получаем текущие настройки inbound
                sql_get = f"""sqlite3 {db_path} "SELECT settings FROM inbounds WHERE id={self.config.xui.inbound_id};" """
                result = subprocess.run(sql_get, shell=True, capture_output=True, text=True)
                
                if result.returncode != 0 or not result.stdout:
                    logger.error(f"Не удалось получить настройки inbound id={self.config.xui.inbound_id}")
                    return {"success": False, "error": "Inbound не найден в базе данных"}
                
                # Парсим JSON
                settings = json.loads(result.stdout.strip())
                clients = settings.get('clients', [])
                
                # Создаем нового клиента с ВСЕМИ необходимыми полями как в панели
                new_client = {
                    "id": client_uuid,
                    "email": email,
                    "limitIp": 0,
                    "totalGB": total_bytes,
                    "expiryTime": expiry_time,
                    "enable": True,
                    "flow": flow,
                    "tgId": 0,  # Изменено с "" на 0 как в панели
                    "subId": sub_id,  # Добавлен сгенерированный subId
                    "password": password,  # Добавлен сгенерированный password
                    "auth": auth,  # Добавлен сгенерированный auth
                    "security": "auto",  # Добавлено поле security как в панели
                    "comment": client_comment,
                    "reset": 0,
                    "created_at": created_at,
                    "updated_at": created_at
                }
                
                # Добавляем клиента в список
                clients.append(new_client)
                settings['clients'] = clients
                
                # Сериализуем JSON
                settings_json = json.dumps(settings, ensure_ascii=False)
                
                try:
                    # Используем sqlite3 Python модуль для корректной записи
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE inbounds SET settings = ? WHERE id = ?",
                        (settings_json, self.config.xui.inbound_id)
                    )
                    conn.commit()
                    conn.close()
                    
                    logger.info(f"Клиент {email} успешно добавлен в старую структуру БД с полями: sub_id={sub_id}, password={password}, auth={auth}, security=auto")
                    logger.info(f"Обновлено клиентов в settings: {len(clients)}")
                    
                    # Также добавляем запись в client_traffics для отслеживания трафика
                    sql_traffic = f"""sqlite3 {db_path} "INSERT OR IGNORE INTO client_traffics (inbound_id, enable, email, up, down, all_time, expiry_time, total, reset) VALUES ({self.config.xui.inbound_id}, 1, '{email}', 0, 0, 0, {expiry_time}, {total_bytes}, 0);" """
                    subprocess.run(sql_traffic, shell=True, capture_output=True, text=True)
                    
                    # Перезапускаем X-UI чтобы панель перечитала settings
                    try:
                        logger.info("Перезапускаем X-UI для применения изменений...")
                        result = subprocess.run("systemctl restart x-ui", shell=True, capture_output=True, text=True, timeout=10)
                        if result.returncode == 0:
                            logger.info("X-UI успешно перезапущен")
                        else:
                            logger.warning(f"Не удалось перезапустить X-UI: {result.stderr}")
                    except Exception as e:
                        logger.warning(f"Ошибка при перезапуске X-UI: {e}")
                    
                    return {"success": True, "uuid": client_uuid}
                except Exception as e:
                    logger.error(f"Ошибка при обновлении БД: {e}")
                    return {"success": False, "error": str(e)}
                    
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

        # Получаем endpoints в зависимости от версии
        endpoints = self._get_api_endpoints('delete_client')
        # Подставляем UUID и inbound_id в шаблоны
        endpoints = [
            ep.replace('{uuid}', client_uuid).replace('{inbound_id}', str(self.config.xui.inbound_id))
            for ep in endpoints
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



def generate_vless_link(client_uuid: str, email: str, vpn_config, inbound_id: int, xui_client=None) -> str:
    """Универсальная генерация VLESS ссылки в зависимости от настроек"""
    import urllib.parse
    import random
    import string
    
    # Пытаемся получить настройки из БД
    db_settings = {}
    if xui_client:
        try:
            db_settings = xui_client.get_reality_settings_from_db(inbound_id)
            logger.info(f"Используем настройки из БД: {db_settings}")
        except Exception as e:
            logger.warning(f"Не удалось получить настройки из БД: {e}")
    
    # Используем настройки из БД или fallback на config
    security = db_settings.get('security', vpn_config.security)
    transport = db_settings.get('network', vpn_config.transport)
    sni = db_settings.get('sni', vpn_config.get_sni())
    fingerprint = db_settings.get('fingerprint', vpn_config.get_fingerprint())
    public_key = db_settings.get('public_key', getattr(vpn_config, 'reality_public_key', ''))
    short_id = db_settings.get('short_id', getattr(vpn_config, 'reality_short_id', ''))
    spider_x_base = db_settings.get('spider_x', '/')
    
    # Генерируем случайный spiderX путь как в панели
    random_path = ''.join(random.choices(string.ascii_lowercase + string.digits, k=15))
    spider_x = f"{spider_x_base}{random_path}" if spider_x_base == '/' else spider_x_base
    xhttp_path = db_settings.get('xhttp_path', '/')
    xhttp_mode = db_settings.get('xhttp_mode', getattr(vpn_config, 'xhttp_mode', 'auto'))
    xhttp_host = db_settings.get('xhttp_host', '')
    x_padding_bytes = db_settings.get('x_padding_bytes', '100-1000')
    sc_max_each_post_bytes = db_settings.get('sc_max_each_post_bytes', '1000000')
    sc_min_posts_interval_ms = db_settings.get('sc_min_posts_interval_ms', '30')
    
    base = f"vless://{client_uuid}@{vpn_config.server_address}:{vpn_config.server_port}"
    
    # Начинаем с type (транспорт должен быть первым)
    params = f"type={transport}"
    
    # Encryption всегда none для VLESS
    params += "&encryption=none"
    
    # Security
    params += f"&security={security}"
    
    # Fingerprint
    params += f"&fp={fingerprint}"
    
    # ALPN - для TLS обязательно указываем (URL-encoded)
    tls_alpn = getattr(vpn_config, 'tls_alpn', 'http/1.1')
    if security == "tls" and tls_alpn:
        # URL-encode ALPN (http/1.1 -> http%2F1.1)
        alpn_encoded = urllib.parse.quote(tls_alpn, safe='')
        params += f"&alpn={alpn_encoded}"
    
    # Flow - только для TCP с Reality или TLS
    # Для xHTTP flow НЕ добавляется независимо от security
    if transport == "tcp" and security in ["reality", "tls"]:
        params += "&flow=xtls-rprx-vision"
    
    # SNI - добавляем после основных параметров
    if sni:
        params += f"&sni={sni}"
    
    # Reality параметры
    if security == "reality":
        if public_key:
            params += f"&pbk={public_key}"
        if short_id:
            params += f"&sid={short_id}"
        # spiderX (SpiderX path) - URL-encode
        spider_x_encoded = urllib.parse.quote(spider_x, safe='')
        params += f"&spx={spider_x_encoded}"
    
    # xHTTP параметры
    if transport == "xhttp":
        params += f"&mode={xhttp_mode}"
        # Для xHTTP добавляем дополнительные параметры
        xhttp_path_encoded = urllib.parse.quote(xhttp_path, safe='')
        params += f"&path={xhttp_path_encoded}"
        if xhttp_host:
            params += f"&host={xhttp_host}"
        else:
            params += "&host="
        # xPaddingBytes для xHTTP
        params += f"&x_padding_bytes={x_padding_bytes}"
        # extra параметры для совместимости - добавляем все параметры как в панели
        extra = {
            "scMaxEachPostBytes": sc_max_each_post_bytes,
            "scMinPostsIntervalMs": sc_min_posts_interval_ms,
            "xPaddingBytes": x_padding_bytes
        }
        params += f"&extra={urllib.parse.quote(json.dumps(extra))}"
    
    return f"{ base}?{params}#{urllib.parse.quote(email)}"


def setup_logging(logging_config):
    """Настройка логирования"""
    log_level = getattr(logging, logging_config.level.upper())
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(handler)
    
    if logging_config.file_enabled:
        try:
            file_handler = RotatingFileHandler(
                logging_config.file_path,
                maxBytes=logging_config.max_size_mb * 1024 * 1024,
                backupCount=logging_config.backup_count
            )
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            print(f"Ошибка создания лог-файла: {e}")