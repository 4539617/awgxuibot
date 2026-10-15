# cascade_client.py — async Python client for Cascade API
import aiohttp
import asyncio
import logging
import ssl
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Имя интерфейса, который создаётся и используется ботом
AWGXUIBOT_IFACE_NAME = "AWGXUIBOT"
# Протокол: AWG 3.0
AWGXUIBOT_PROTOCOL = "amneziawg-3.0"


class CascadeAPIError(Exception):
    """Ошибка Cascade API"""
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class CascadeClient:
    """
    Асинхронный клиент для Cascade API.
    Поддерживает авторизацию через:
      - api_key (Bearer-токен) — приоритет 1
      - login + password → получить токен → logout — приоритет 2
    """

    def __init__(self, url: str, api_key: str = "", username: str = "admin",
                 password: str = "", label: str = ""):
        self.url = url.rstrip("/")
        self._api_key = api_key
        self.username = username
        self.password = password
        self.label = label or url
        self._token: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None

    # ── Session management ───────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=15, connect=8),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    # ── Auth ─────────────────────────────────────────────────────────────────────

    async def ensure_token(self):
        """Убедиться, что токен получен. Если api_key задан — использовать его."""
        if self._token:
            return
        if self._api_key:
            self._token = self._api_key
            return
        if not self.password:
            raise CascadeAPIError(
                f"[Cascade:{self.label}] Нет api_key и password — авторизация невозможна"
            )

        logger.info(f"[Cascade:{self.label}] Авторизация (login → token → logout)...")
        session = await self._get_session()

        # Шаг 1: Login
        session_cookie = ""
        try:
            async with session.post(
                f"{self.url}/api/session",
                json={"username": self.username, "password": self.password},
            ) as resp:
                body = await resp.json()
                if not body.get("authenticated"):
                    raise CascadeAPIError(
                        f"[Cascade:{self.label}] Login rejected", resp.status
                    )
                raw_cookie = resp.headers.get("Set-Cookie", "")
                if raw_cookie:
                    session_cookie = raw_cookie.split(";")[0].strip()
        except CascadeAPIError:
            raise
        except Exception as e:
            raise CascadeAPIError(f"[Cascade:{self.label}] Login failed: {e}")

        # Шаг 2: Create API token
        headers = {}
        if session_cookie:
            headers["Cookie"] = session_cookie
        try:
            async with session.post(
                f"{self.url}/api/tokens",
                json={"name": f"awgxuibot-{asyncio.get_event_loop().time():.0f}"},
                headers=headers,
            ) as resp:
                body = await resp.json()
                token = body.get("token") or body.get("raw_token")
                if not token:
                    raise CascadeAPIError(
                        f"[Cascade:{self.label}] Token creation returned empty token"
                    )
                self._token = token
        except CascadeAPIError:
            raise
        except Exception as e:
            raise CascadeAPIError(f"[Cascade:{self.label}] Token creation failed: {e}")

        # Шаг 3: Logout (cleanup)
        try:
            hdrs = {}
            if session_cookie:
                hdrs["Cookie"] = session_cookie
            async with session.delete(f"{self.url}/api/session", headers=hdrs):
                pass
        except Exception:
            pass

        logger.info(f"[Cascade:{self.label}] Токен получен")

    # ── Low-level HTTP ───────────────────────────────────────────────────────────

    async def _req(self, method: str, path: str, body=None, retried=False) -> Any:
        await self.ensure_token()
        session = await self._get_session()
        url = f"{self.url}/api{path}"
        headers = {"Authorization": f"Bearer {self._token}"}
        kwargs: Dict[str, Any] = {"headers": headers}
        if body is not None:
            kwargs["json"] = body

        try:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status == 401 and not retried:
                    # Если используется статический api_key — повторная попытка бессмысленна,
                    # тот же ключ снова вернёт 401. Сразу поднимаем ошибку.
                    if self._api_key:
                        text = await resp.text()
                        raise CascadeAPIError(
                            f"[Cascade:{self.label}] {method} {path} → 401: {text[:200]} "
                            f"(api_key невалиден или истёк)",
                            401,
                        )
                    logger.warning(f"[Cascade:{self.label}] 401 — сбрасываем токен и повторяем")
                    self._token = None
                    return await self._req(method, path, body, retried=True)
                if not resp.ok:
                    text = await resp.text()
                    raise CascadeAPIError(
                        f"[Cascade:{self.label}] {method} {path} → {resp.status}: {text[:200]}",
                        resp.status,
                    )
                ct = resp.content_type or ""
                if "json" in ct:
                    return await resp.json()
                return await resp.text()
        except CascadeAPIError:
            raise
        except Exception as e:
            raise CascadeAPIError(f"[Cascade:{self.label}] {method} {path} failed: {e}")

    # ── Health ───────────────────────────────────────────────────────────────────

    async def health(self) -> Dict:
        """GET /api/health → { status, version, host }"""
        session = await self._get_session()
        try:
            async with session.get(
                f"{self.url}/api/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                raise CascadeAPIError(f"health HTTP {resp.status}", resp.status)
        except CascadeAPIError:
            raise
        except Exception as e:
            raise CascadeAPIError(f"health failed: {e}")

    async def ping(self) -> bool:
        """Проверяет доступность сервера И валидность токена.
        Сначала проверяем /api/health (сервер жив?), затем делаем
        авторизованный запрос /api/tunnel-interfaces (токен работает?).
        Возвращает False если сервер недоступен ИЛИ токен невалиден (401).
        """
        try:
            await self.health()
        except Exception:
            return False
        # Проверяем авторизацию — /api/tunnel-interfaces требует валидный токен
        try:
            await self.list_interfaces()
            return True
        except CascadeAPIError as e:
            if e.status == 401:
                logger.warning(f"[Cascade:{self.label}] ping: токен невалиден (401) → считаем оффлайн")
            return False
        except Exception:
            return False

    # ── Interfaces ───────────────────────────────────────────────────────────────

    async def list_interfaces(self) -> List[Dict]:
        """GET /api/tunnel-interfaces → list of interface dicts"""
        data = await self._req("GET", "/tunnel-interfaces")
        return data.get("interfaces", []) if isinstance(data, dict) else []

    async def quick_create_interface(self, name: str, protocol: str = "amneziawg-2.0") -> Dict:
        """POST /api/tunnel-interfaces/quick-create"""
        return await self._req("POST", "/tunnel-interfaces/quick-create", {"name": name, "protocol": protocol})

    async def start_interface(self, iface_id: str) -> Dict:
        return await self._req("POST", f"/tunnel-interfaces/{iface_id}/start")

    # ── Peers ────────────────────────────────────────────────────────────────────

    async def list_peers(self, iface_id: str) -> List[Dict]:
        """GET /api/tunnel-interfaces/:id/peers → list of peer dicts"""
        data = await self._req("GET", f"/tunnel-interfaces/{iface_id}/peers")
        return data.get("peers", []) if isinstance(data, dict) else []

    async def create_peer(self, iface_id: str, name: str, expired_at: str = "") -> Dict:
        """POST /api/tunnel-interfaces/:id/peers
        
        expired_at: RFC3339 string (e.g. "2025-08-01T00:00:00Z") or "YYYY-MM-DD", "" = no expiry.
        """
        body = {
            "name": name,
            "peerType": "client",
            "generateKeys": True,
            "autoAllocateIP": True,
        }
        if expired_at:
            body["expiredAt"] = expired_at
        data = await self._req("POST", f"/tunnel-interfaces/{iface_id}/peers", body)
        return data.get("peer", data) if isinstance(data, dict) else data

    async def get_peer(self, iface_id: str, peer_id: str) -> Dict:
        """GET /api/tunnel-interfaces/:id/peers/:peerId → peer dict with expiredAt etc."""
        data = await self._req("GET", f"/tunnel-interfaces/{iface_id}/peers/{peer_id}")
        return data.get("peer", data) if isinstance(data, dict) else data

    async def delete_peer(self, iface_id: str, peer_id: str) -> None:
        """DELETE /api/tunnel-interfaces/:id/peers/:peerId"""
        await self._req("DELETE", f"/tunnel-interfaces/{iface_id}/peers/{peer_id}")

    async def get_peer_config(self, iface_id: str, peer_id: str) -> str:
        """GET /api/tunnel-interfaces/:id/peers/:pid/config → plain text WireGuard .conf"""
        await self.ensure_token()
        session = await self._get_session()
        url = f"{self.url}/api/tunnel-interfaces/{iface_id}/peers/{peer_id}/config"
        async with session.get(url, headers={"Authorization": f"Bearer {self._token}"}) as resp:
            if not resp.ok:
                raise CascadeAPIError(f"get_peer_config HTTP {resp.status}", resp.status)
            return await resp.text()

    async def get_peer_qrcode_svg(self, iface_id: str, peer_id: str) -> str:
        """GET /api/tunnel-interfaces/:id/peers/:pid/qrcode.svg → SVG string"""
        await self.ensure_token()
        session = await self._get_session()
        url = f"{self.url}/api/tunnel-interfaces/{iface_id}/peers/{peer_id}/qrcode.svg"
        async with session.get(url, headers={"Authorization": f"Bearer {self._token}"}) as resp:
            if not resp.ok:
                raise CascadeAPIError(f"get_peer_qrcode_svg HTTP {resp.status}", resp.status)
            return await resp.text()

    # ── High-level helpers ───────────────────────────────────────────────────────

    async def get_or_create_awgxuibot_interface(self) -> Dict:
        """
        Возвращает интерфейс AWGXUIBOT с протоколом amneziawg-3.0,
        создавая его при необходимости.
        Если интерфейс не запущен — запускает.
        """
        ifaces = await self.list_interfaces()
        for iface in ifaces:
            if iface.get("name") == AWGXUIBOT_IFACE_NAME:
                # Проверяем протокол — должен быть amneziawg-3.0
                proto = iface.get("protocol", "")
                if proto and proto != AWGXUIBOT_PROTOCOL:
                    logger.warning(
                        f"[Cascade:{self.label}] Интерфейс {AWGXUIBOT_IFACE_NAME} "
                        f"имеет протокол {proto!r} вместо {AWGXUIBOT_PROTOCOL!r} — "
                        f"продолжаем с существующим"
                    )
                # Запускаем если не запущен
                if not iface.get("running", False):
                    logger.info(f"[Cascade:{self.label}] Запуск интерфейса {AWGXUIBOT_IFACE_NAME}...")
                    await self.start_interface(iface["id"])
                return iface

        # Интерфейс не найден — создаём с протоколом AWG 3.0
        logger.info(
            f"[Cascade:{self.label}] Создаём интерфейс {AWGXUIBOT_IFACE_NAME} "
            f"(protocol={AWGXUIBOT_PROTOCOL})..."
        )
        result = await self.quick_create_interface(AWGXUIBOT_IFACE_NAME, AWGXUIBOT_PROTOCOL)
        iface = result.get("interface", result)
        logger.info(
            f"[Cascade:{self.label}] Интерфейс {AWGXUIBOT_IFACE_NAME} создан "
            f"(id={iface.get('id')}, protocol={iface.get('protocol')})"
        )
        return iface
