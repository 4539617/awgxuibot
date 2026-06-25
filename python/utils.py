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
from typing import Dict, Optional
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
        self.api_token = config.xui.api_token
    
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
    
    def update_xui_config(self, new_xui_config):
        """Обновить конфигурацию XUI для переключения между панелями"""
        self.config.xui = new_xui_config
        self.api_token = new_xui_config.api_token
        # Сбрасываем сессию для переподключения
        if self.session:
            asyncio.create_task(self.session.close())
            self.session = None
        self.cookies = None
        return self.session
    
    async def _get_headers(self):
        """Универсальные заголовки для v2 и v3"""
        if self.config.xui.is_v3_new_api() and self.api_token:
            return {'Authorization': f'Bearer {self.api_token}'}
        return {}
    
    async def login(self) -> bool:
        """Авторизация в панели 3x-ui с автоматическим переключением HTTPS/HTTP"""
        # Для v3 с API токеном авторизация не требуется
        if self.config.xui.is_v3_new_api() and self.api_token:
            await self._get_session()
            logger.info(f"Используется Bearer Token для v3 API")
            return True
        
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
        """Универсальный метод создания клиента для v2 и v3"""
        if self.config.xui.is_v3_new_api():
            return await self._add_client_v3(email, total_gb, expiry_days, comment)
        else:
            return await self._add_client_v2(email, total_gb, expiry_days, comment)
    
    async def _add_client_v2(self, email: str, total_gb: int, expiry_days: float, comment: str = None) -> Dict:
        """Создание нового клиента через API v2 с комментарием"""
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
    async def _add_client_v3(self, email: str, total_gb: int, expiry_days: float, comment: str = None) -> Dict:
        """Создание клиента через v3 API"""
        if not self.session:
            if not await self.login():
                return {"success": False, "error": "Не удалось авторизоваться"}
        
        # Вычисляем expiry time в миллисекундах
        expiry_time = int((time.time() + expiry_days * 86400) * 1000)
        
        # Для v3 API: если total_gb = 0, не устанавливаем лимит
        if total_gb == 0:
            total_bytes = 0  # 0 = без лимита
        else:
            total_bytes = total_gb * 1024 * 1024 * 1024
        
        # Получаем реальные настройки inbound из панели для определения flow
        flow = ""
        try:
            endpoint_get = f"{self.config.xui.url}/panel/api/inbounds/get/{self.config.xui.inbound_id}"
            headers = await self._get_headers()
            
            async with self.session.get(endpoint_get, headers=headers) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('success'):
                        inbound_data = result.get('obj', {})
                        stream_settings_str = inbound_data.get('streamSettings', '{}')
                        stream_settings = json.loads(stream_settings_str) if isinstance(stream_settings_str, str) else stream_settings_str
                        
                        # Извлекаем реальные transport и security из inbound
                        real_transport = stream_settings.get('network', 'tcp')
                        real_security = stream_settings.get('security', 'none')
                        
                        # Устанавливаем flow на основе реальных настроек inbound
                        if real_transport == "tcp" and real_security in ["reality", "tls"]:
                            flow = "xtls-rprx-vision"
                            logger.info(f"Установлен flow для {real_transport}+{real_security}: {flow}")
                        else:
                            logger.info(f"Flow не требуется для {real_transport}+{real_security}")
                    else:
                        logger.warning(f"⚠️ Не удалось получить настройки inbound, используем конфиг")
                        # Fallback на конфиг
                        if self.config.vpn.transport == "tcp" and self.config.vpn.security in ["reality", "tls"]:
                            flow = "xtls-rprx-vision"
                            logger.info(f"Установлен flow из конфига для TCP+{self.config.vpn.security}: {flow}")
                else:
                    logger.warning(f"⚠️ API вернул статус {resp.status}, используем конфиг")
                    # Fallback на конфиг
                    if self.config.vpn.transport == "tcp" and self.config.vpn.security in ["reality", "tls"]:
                        flow = "xtls-rprx-vision"
                        logger.info(f"Установлен flow из конфига для TCP+{self.config.vpn.security}: {flow}")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка получения настроек inbound: {e}, используем конфиг")
            # Fallback на конфиг
            if self.config.vpn.transport == "tcp" and self.config.vpn.security in ["reality", "tls"]:
                flow = "xtls-rprx-vision"
                logger.info(f"Установлен flow из конфига для TCP+{self.config.vpn.security}: {flow}")
        
        # Формируем данные клиента согласно API v3
        client_data = {
            "email": email,
            "flow": flow,  # ВАЖНО: flow обязателен для TCP+Reality/TLS
            "totalGB": total_bytes,
            "expiryTime": expiry_time,
            "enable": True,
            "limitIp": 0,
            "tgId": 0,
            "subId": ""
        }
        
        # Добавляем комментарий если указан
        if comment:
            client_data["comment"] = comment
        
        # Формируем финальный запрос
        # ВАЖНО: inboundIds должен быть массивом целых чисел, не строк
        data = {
            "client": client_data,
            "inboundIds": [int(self.config.xui.inbound_id)]
        }
        
        # ВАЖНО: Используем базовый путь из XUI_URL
        # URL уже содержит webBasePath, просто добавляем /panel/api/clients/add
        endpoint = f"{self.config.xui.url}/panel/api/clients/add"
        headers = await self._get_headers()
        
        try:
            logger.info(f"Создание клиента v3: email={email}, inbound={self.config.xui.inbound_id}, flow={flow}")
            logger.debug(f"Request URL: {endpoint}")
            logger.debug(f"Request headers: {headers}")
            logger.debug(f"Request data: {json.dumps(data, indent=2)}")
            
            async with self.session.post(endpoint, json=data, headers=headers) as resp:
                response_text = await resp.text()
                logger.info(f"Response status: {resp.status}")
                logger.info(f"Response text: {response_text}")
                
                if resp.status == 200:
                    try:
                        result = json.loads(response_text) if response_text else {}
                        logger.info(f"📦 Полный ответ API при создании: {json.dumps(result, indent=2, ensure_ascii=False)}")
                    except json.JSONDecodeError as je:
                        logger.error(f"Ошибка парсинга JSON: {je}")
                        logger.error(f"Response text: {response_text}")
                        result = {}
                    
                    if result.get('success'):
                        logger.info(f"✅ Клиент {email} создан через v3 API")
                        
                        # В v3 API UUID возвращается в obj
                        obj = result.get('obj', {})
                        logger.info(f"📦 obj из ответа: {obj}")
                        logger.info(f"📦 Тип obj: {type(obj)}")
                        
                        client_uuid = obj.get('uuid', '') if isinstance(obj, dict) else ''
                        logger.info(f"🔑 UUID из obj: '{client_uuid}'")
                        
                        # Если UUID не в ответе, получаем через список клиентов
                        if not client_uuid:
                            logger.warning(f"⚠️ UUID не найден в ответе API, получаем через список клиентов")
                            client_details = await self._get_client_details_v3(email)
                            logger.info(f"📦 Детали клиента из списка: {client_details}")
                            client_uuid = client_details.get('uuid', '') if client_details else ''
                            logger.info(f"🔑 UUID из списка: '{client_uuid}'")
                        
                        if client_uuid:
                            logger.info(f"✅ UUID клиента {email}: {client_uuid}")
                        else:
                            logger.error(f"❌ Не удалось получить UUID для клиента {email}")
                        
                        return {"success": True, "uuid": client_uuid}
                    else:
                        error_msg = result.get('msg', response_text)
                        logger.error(f"API вернул success=false: {error_msg}")
                        return {"success": False, "error": error_msg}
                
                # Для 400 ошибки выводим полную информацию
                logger.error(f"Ошибка v3 API создания клиента:")
                logger.error(f"  Status: {resp.status}")
                logger.error(f"  Response: {response_text}")
                logger.error(f"  Request URL: {endpoint}")
                logger.error(f"  Request data: {json.dumps(data, indent=2)}")
                return {"success": False, "error": f"{resp.status}: {response_text}"}
        except Exception as e:
            logger.error(f"Ошибка создания клиента v3: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _get_client_details_v3(self, email: str) -> dict:
        """Получение деталей клиента через v3 API GET /panel/api/clients/get/{email}"""
        if not self.session:
            if not await self.login():
                return None
        
        # В v3 API есть endpoint для получения клиента по email
        endpoint = f"{self.config.xui.url}/panel/api/clients/get/{email}"
        headers = await self._get_headers()
        
        try:
            logger.info(f"🔍 Запрос деталей клиента: {email}")
            async with self.session.get(endpoint, headers=headers) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('success'):
                        obj = result.get('obj', {})
                        # В v3 API структура: obj.client содержит данные клиента
                        client = obj.get('client', {})
                        logger.info(f"✅ Получены детали клиента {email}")
                        logger.info(f"📦 Полные данные obj: {json.dumps(obj, indent=2, ensure_ascii=False)}")
                        logger.info(f"📦 Данные client: {json.dumps(client, indent=2, ensure_ascii=False)}")
                        logger.info(f"🔑 UUID клиента: '{client.get('uuid')}'")
                        return client
                    else:
                        logger.warning(f"⚠️ API вернул success=false для клиента {email}")
                        return None
                else:
                    logger.error(f"❌ Ошибка получения клиента {email}: HTTP {resp.status}")
                    return None
            
        except Exception as e:
            logger.error(f"Ошибка получения деталей клиента v3: {e}")
            return None

    async def _get_all_clients_v3(self) -> list:
        """Получение всех клиентов через v3 API (полный список с UUID)"""
        if not self.session:
            if not await self.login():
                return []
        
        # Используем ПОЛНЫЙ endpoint /list который возвращает UUID
        # НЕ используем /list/paged - он возвращает slim данные БЕЗ UUID!
        endpoint = f"{self.config.xui.url}/panel/api/clients/list"
        headers = await self._get_headers()
        
        all_clients = []
        
        try:
            async with self.session.get(endpoint, headers=headers) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('success'):
                        clients = result.get('obj', [])
                        
                        if not clients:
                            logger.warning("⚠️ Список клиентов пуст")
                            return []
                        
                        current_time = int(time.time() * 1000)
                        
                        logger.info(f"📊 Получено {len(clients)} клиентов из /list endpoint")
                        
                        # Преобразуем в формат v2 для совместимости
                        for client in clients:
                            expiry_time = client.get('expiryTime', 0)
                            enable = client.get('enable', True)
                            
                            # Определяем статус
                            if expiry_time > 0 and expiry_time < current_time:
                                status = 'expired'
                            elif not enable:
                                status = 'inactive'
                            else:
                                status = 'active'
                            
                            traffic = client.get('traffic', {})
                            all_clients.append({
                                'uuid': client.get('uuid', ''),
                                'email': client.get('email', ''),
                                'comment': client.get('comment', ''),
                                'enable': enable,
                                'expiryTime': expiry_time,
                                'totalGB': client.get('totalGB', 0),
                                'status': status,
                                'up': traffic.get('up', 0),
                                'down': traffic.get('down', 0)
                            })
                        
                        return all_clients
                    else:
                        logger.error(f"API вернул success=false: {result}")
                        return []
                else:
                    text = await resp.text()
                    logger.error(f"Ошибка получения клиентов v3: {resp.status} - {text}")
                    return []
            
        except Exception as e:
            logger.error(f"Ошибка получения клиентов v3: {e}")
            return []

    async def _delete_client_v3(self, email: str) -> bool:
        """Удаление клиента через v3 API"""
        if not self.session:
            if not await self.login():
                return False
        
        endpoint = f"{self.config.xui.url}/panel/api/clients/del/{email}"
        headers = await self._get_headers()
        
        try:
            async with self.session.post(endpoint, headers=headers) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('success'):
                        logger.info(f"Клиент {email} удален через v3 API")
                        return True
                
                logger.error(f"Ошибка удаления v3: {resp.status}")
                return False
        except Exception as e:
            logger.error(f"Ошибка удаления клиента v3: {e}")
            return False

    async def get_client_links_v3(self, email: str) -> list:
        """Получить готовые ссылки клиента через v3 API"""
        if not self.session:
            if not await self.login():
                return None
        
        endpoint = f"{self.config.xui.url}/panel/api/clients/links/{email}"
        headers = await self._get_headers()
        
        try:
            async with self.session.get(endpoint, headers=headers) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('success'):
                        links = result.get('obj', [])
                        logger.info(f"Получены ссылки для {email}: {len(links)} шт.")
                        return links
            
            logger.error(f"Ошибка получения ссылок v3: {resp.status}")
            return None
        except Exception as e:
            logger.error(f"Ошибка получения ссылок v3: {e}")
            return None

            logger.error(f"Ошибка создания клиента v3: {e}")
            return {"success": False, "error": str(e)}


    async def delete_client(self, client_uuid: str, email: str = None) -> bool:
        """Универсальный метод удаления клиента для v2 и v3"""
        if self.config.xui.is_v3_new_api():
            return await self._delete_client_v3(email or client_uuid)
        else:
            return await self._delete_client_v2(client_uuid, email)
    
    async def _delete_client_v2(self, client_uuid: str, email: str = None) -> bool:
        """Удаление клиента через API v2 или SQL"""
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
                                logger.info(f"Клиент {email or client_uuid} удален через API v2")
                                return True
                        except:
                            pass
            except Exception as e:
                logger.error(f"Ошибка удаления через API v2: {e}")
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
        """Универсальный метод получения истекших клиентов для v2 и v3"""
        if self.config.xui.is_v3_new_api():
            return await self._get_expired_clients_v3()
        else:
            return await self._get_expired_clients_v2()
    
    async def _get_expired_clients_v3(self) -> list:
        """Получение истекших клиентов через v3 API"""
        all_clients = await self._get_all_clients_v3()
        current_time = int(time.time() * 1000)
        
        expired_clients = []
        for client in all_clients:
            expiry_time = client.get('expiryTime', 0)
            if expiry_time > 0 and expiry_time < current_time:
                expired_clients.append({
                    'uuid': client.get('uuid'),
                    'email': client.get('email'),
                    'expiry_time': expiry_time
                })
        
        return expired_clients
    
    async def _get_expired_clients_v2(self) -> list:
        """Получение списка истекших клиентов из JSON настроек inbound (v2)"""
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
        """Универсальный метод получения всех клиентов для v2 и v3"""
        if self.config.xui.is_v3_new_api():
            return await self._get_all_clients_v3()
        else:
            return await self._get_all_clients_v2()
    
    async def _get_all_clients_v2(self) -> list:
        """Получение всех клиентов с полной информацией (v2)"""
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
    
    async def get_client_details(self, client_uuid: str, email: str = None):
        """Универсальный метод получения деталей клиента для v2 и v3"""
        if self.config.xui.is_v3_new_api() and email:
            return await self._get_client_details_v3(email)
        else:
            # Для v2 или если email не указан - используем поиск по UUID
            try:
                all_clients = await self.get_all_clients()
                
                for client in all_clients:
                    if client['uuid'] == client_uuid:
                        return client
                
                return None
                
            except Exception as e:
                logger.error(f"Ошибка получения деталей клиента: {e}")
                return None
    
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
    
    async def update_client_status(self, client_uuid: str, enable: bool, email: str = None) -> bool:
        """Включение/выключение клиента (универсальный метод для v2 и v3)"""
        try:
            # Для v3 используем API
            if self.config.xui.is_v3_new_api():
                return await self._update_client_status_v3(client_uuid, enable, email)
            
            # Для v2 используем прямой доступ к SQLite
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
    
    async def _update_client_status_v3(self, client_uuid: str, enable: bool, email: str = None) -> bool:
        """Обновление статуса клиента через v3 API"""
        if not self.session:
            if not await self.login():
                return False
        
        try:
            # Если email не передан, получаем его из деталей клиента
            if not email:
                # Получаем все клиенты и ищем по UUID
                all_clients = await self.get_all_clients()
                client_data = None
                for client in all_clients:
                    if client.get('uuid') == client_uuid:
                        client_data = client
                        email = client.get('email')
                        break
                
                if not email:
                    logger.error(f"Не удалось найти email для клиента {client_uuid}")
                    return False
            
            # Получаем полные данные клиента
            client = await self._get_client_details_v3(email)
            if not client:
                logger.error(f"Не удалось получить данные клиента {email}")
                return False
            
            # Обновляем статус
            client['enable'] = enable
            
            # Отправляем обновление через API v3
            # Используем endpoint для обновления клиента по email
            endpoint = f"{self.config.xui.url}/panel/api/clients/update/{email}"
            headers = await self._get_headers()
            headers['Content-Type'] = 'application/json'
            
            # Формируем данные для обновления (используем структуру из client)
            # Важно: id должен быть UUID (строка), а не числовой ID из базы
            update_data = {
                "id": client_uuid,  # UUID клиента (строка)
                "email": email,
                "enable": enable,
                "flow": client.get('flow', ''),
                "limitIp": client.get('limitIp', 0),
                "totalGB": client.get('totalGB', 0),
                "expiryTime": client.get('expiryTime', 0),
                "subId": client.get('subId', ''),
                "tgId": client.get('tgId', ''),
                "reset": client.get('reset', 0)
            }
            
            logger.info(f"🔄 Обновление статуса клиента {email} (UUID: {client_uuid}) -> enable={enable}")
            logger.info(f"📤 Endpoint: {endpoint}")
            logger.info(f"📦 Данные: {json.dumps(update_data, indent=2)}")
            
            async with self.session.post(endpoint, headers=headers, json=update_data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('success'):
                        status_text = "включен" if enable else "выключен"
                        logger.info(f"✅ Клиент {email} {status_text}")
                        return True
                    else:
                        logger.error(f"❌ API вернул success=false: {result.get('msg', 'Unknown error')}")
                        return False
                else:
                    error_text = await resp.text()
                    logger.error(f"❌ Ошибка обновления статуса: HTTP {resp.status}, {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Ошибка обновления статуса клиента v3: {e}")
            return False
    async def get_server_status(self) -> dict:
        """Получение статуса сервера (CPU, RAM, Disk, Network, Xray)"""
        if not self.session:
            await self.login()
        
        endpoint = f"{self.config.xui.url}/panel/api/server/status"
        headers = await self._get_headers()
        
        try:
            async with self.session.get(endpoint, headers=headers) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('success'):
                        return result.get('obj', {})
                    else:
                        logger.error(f"API вернул success=false: {result}")
                        return {}
                else:
                    text = await resp.text()
                    logger.error(f"Ошибка получения статуса сервера: {resp.status} - {text}")
                    return {}
        except Exception as e:
            logger.error(f"Ошибка получения статуса сервера: {e}")
    
    async def download_backup(self):
        """Скачать бэкап базы данных"""
        if not self.session:
            await self.login()
        
        endpoint = f"{self.config.xui.url}/panel/api/server/getDb"
        headers = await self._get_headers()
        
        try:
            async with self.session.get(endpoint, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    text = await resp.text()
                    logger.error(f"Ошибка скачивания бэкапа: {resp.status} - {text}")
                    return None
        except Exception as e:
            logger.error(f"Ошибка при скачивании бэкапа: {e}")
            return None
            return {}



async def get_client_link(xui_client, email: str, client_uuid: str, vpn_config, inbound_id: int) -> Optional[str]:
    """Универсальная функция получения VLESS ссылки для v2 и v3"""
    # Для v3 и v2 ВСЕГДА используем ручную генерацию
    # Панель v3 часто возвращает некорректные ссылки (неправильный sid, spx и т.д.)
    
    # Получаем данные клиента для извлечения реального flow из БД
    client_flow = None
    try:
        if hasattr(xui_client, '_get_client_details_v3'):
            client_data = await xui_client._get_client_details_v3(email)
            if client_data:
                client_flow = client_data.get('flow', '')
                logger.info(f"🔑 Flow клиента {email} из БД: '{client_flow}'")
            else:
                logger.warning(f"⚠️ Не удалось получить данные клиента {email}, flow будет определен автоматически")
        else:
            logger.info(f"ℹ️ Метод _get_client_details_v3 недоступен, flow будет определен автоматически")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка получения flow клиента: {e}, flow будет определен автоматически")
    
    logger.info(f"🔗 Генерация VLESS ссылки вручную")
    return generate_vless_link(client_uuid, email, vpn_config, inbound_id, client_flow)



def generate_vless_link(client_uuid: str, email: str, vpn_config, inbound_id: int, client_flow: Optional[str] = None) -> str:
    """Универсальная генерация VLESS ссылки в зависимости от настроек
    
    Поддерживаемые сценарии:
    1. xhttp + reality
    2. tcp + reality
    3. tcp + tls
    
    Args:
        client_uuid: UUID клиента
        email: Email клиента
        vpn_config: Конфигурация VPN
        inbound_id: ID inbound
        client_flow: Flow клиента из БД (опционально, если None - определяется автоматически)
    """
    import urllib.parse
    
    # Для Docker-окружения используем параметры из конфига, а не из БД
    # (БД может быть недоступна, если X-UI на удаленном сервере)
    reality_params = {}
    if vpn_config.security == "reality":
        # Пытаемся получить параметры из БД только если путь локальный
        if hasattr(vpn_config, 'xui_db_path') and vpn_config.xui_db_path.startswith('/etc/'):
            reality_params = get_inbound_reality_settings(vpn_config.xui_db_path, inbound_id)
            if reality_params:
                logger.info(f"Используем параметры Reality из inbound БД: {reality_params}")
        
        # Если не получили из БД, используем параметры из конфига
        if not reality_params:
            logger.info(f"Используем параметры Reality из конфига")
    
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
        # Fingerprint для Reality (приоритет: БД -> конфиг)
        fingerprint = reality_params.get('fingerprint') or vpn_config.reality_fingerprint
        if fingerprint:
            params += f"&fp={fingerprint}"
        
        # Public key (pbk) - ОБЯЗАТЕЛЬНЫЙ параметр для Reality (приоритет: БД -> конфиг)
        public_key = reality_params.get('public_key') or getattr(vpn_config, 'reality_public_key', '')
        if public_key:
            params += f"&pbk={public_key}"
        else:
            logger.error("⚠️ REALITY_PUBLIC_KEY не найден ни в БД, ни в конфиге!")
        
        # SNI для Reality (приоритет: БД -> конфиг)
        sni = reality_params.get('sni') or vpn_config.reality_sni
        if sni:
            params += f"&sni={sni}"
        
        # Short ID (sid) для Reality (приоритет: БД -> конфиг)
        short_id = reality_params.get('short_id') or getattr(vpn_config, 'reality_short_id', '')
        if short_id:
            params += f"&sid={short_id}"
            logger.debug(f"Используем short_id: {short_id}")
        
        # spiderX (SpiderX path) для Reality
        params += "&spx=%2F"
        
        # Flow для TCP + Reality
        if transport == "tcp":
            # Используем переданный flow из БД клиента или значение по умолчанию
            flow_value = client_flow if client_flow is not None else "xtls-rprx-vision"
            if flow_value:  # Добавляем только если не пустой
                params += f"&flow={flow_value}"
                logger.info(f"Добавлен flow для TCP+Reality: {flow_value}")
            else:
                logger.warning(f"⚠️ Flow пустой для TCP+Reality, клиент может не подключиться!")
    
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
            # Используем переданный flow из БД клиента или значение по умолчанию
            flow_value = client_flow if client_flow is not None else "xtls-rprx-vision"
            if flow_value:  # Добавляем только если не пустой
                params += f"&flow={flow_value}"
                logger.info(f"Добавлен flow для TCP+TLS: {flow_value}")
            else:
                logger.warning(f"⚠️ Flow пустой для TCP+TLS, клиент может не подключиться!")
    
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


async def detect_xui_version_from_binary() -> str:
    """
    Определение версии 3x-ui через исполняемый файл на сервере
    Возвращает версию в формате "3.3.1" или "2.8.11" или None
    """
    import subprocess
    import re
    
    try:
        # Пробуем основной путь (работает для v2 и v3)
        result = subprocess.run(
            ['/usr/local/x-ui/x-ui', '-v'],
            capture_output=True,
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL
        )
        
        # Извлекаем версию из вывода
        match = re.search(r'(\d+\.\d+\.\d+)', result.stdout)
        if match:
            version = match.group(1)
            logger.info(f"✅ Версия панели определена через бинарный файл: {version}")
            return version
    except Exception as e:
        logger.debug(f"Не удалось определить версию через /usr/local/x-ui/x-ui: {e}")
    
    return None


async def detect_xui_version_from_url(xui_url: str) -> str:
    """
    Определение версии 3x-ui по структуре URL
    v3.x использует /panel в URL, v2.x - нет
    Возвращает "3.x" для v3.x или "2.x" для v2.x или None
    """
    import aiohttp
    
    try:
        async with aiohttp.ClientSession() as session:
            # Проверяем наличие /panel (характерно для v3)
            async with session.get(
                f"{xui_url}/panel",
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
                ssl=False
            ) as resp:
                if resp.status in [200, 302, 301]:
                    logger.info("✅ Определена версия v3.x по структуре URL (/panel доступен)")
                    return "3.x"
                elif resp.status == 404:
                    logger.info("✅ Определена версия v2.x по структуре URL (/panel не найден)")
                    return "2.x"
    except Exception as e:
        logger.debug(f"Не удалось определить версию через URL: {e}")
    
    return None


async def detect_xui_version(xui_url: str, current_version: str = "latest") -> str:
    """
    Определение версии 3x-ui панели несколькими методами
    Возвращает версию в формате "3.3.1" или "2.8.11" или текущую версию
    
    Приоритет методов:
    1. Через исполняемый файл на сервере (самый точный)
    2. Через структуру URL (определяет v2 vs v3)
    3. Текущая версия из конфига (fallback)
    """
    
    logger.info("🔍 Определение версии 3x-ui панели...")
    
    # Метод 1: Через исполняемый файл (самый точный)
    version = await detect_xui_version_from_binary()
    if version:
        return version
    
    # Метод 2: Через структуру URL (определяет v2 vs v3)
    version = await detect_xui_version_from_url(xui_url)
    if version:
        return version
    
    # Fallback: используем текущую версию
    logger.warning(f"⚠️ Не удалось определить версию панели, используется текущая: {current_version}")
    return current_version


async def update_env_file(key: str, value: str, env_path: str = ".env") -> bool:
    """
    Обновление значения в .env файле
    
    Args:
        key: Ключ параметра (например, "XUI_VERSION")
        value: Новое значение
        env_path: Путь к .env файлу
    
    Returns:
        True если успешно обновлено, False в случае ошибки
    """
    import os
    
    # Пробуем разные пути к .env файлу
    possible_paths = [
        env_path,  # Текущая директория
        "/app/.env",  # Docker контейнер
        "/opt/awgxuibot/.env",  # Хост система
        os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),  # Относительно скрипта
    ]
    
    actual_path = None
    for path in possible_paths:
        if os.path.exists(path):
            actual_path = path
            logger.debug(f"Найден .env файл: {path}")
            break
    
    # Проверяем существование файла
    if not actual_path:
        logger.info(f"ℹ️ Файл .env не доступен в контейнере (это нормально для Docker)")
        logger.info(f"✅ Версия {key}={value} успешно применена в текущей сессии")
        return False
    
    env_path = actual_path
    
    try:
        # Читаем файл
        with open(env_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Ищем и обновляем строку
        updated = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                old_value = line.strip().split('=', 1)[1] if '=' in line else ''
                lines[i] = f"{key}={value}\n"
                updated = True
                logger.info(f"📝 Обновлено: {key}={old_value} → {value}")
                break
        
        # Если не нашли, добавляем в конец
        if not updated:
            lines.append(f"\n# Автоматически определенная версия панели\n")
            lines.append(f"{key}={value}\n")
            logger.info(f"➕ Добавлено: {key}={value}")
        
        # Записываем обратно
        with open(env_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        logger.info(f"✅ Файл {env_path} успешно обновлен")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка обновления {env_path}: {e}")
        return False