import asyncio
import logging
import random
import re
import socket
import string
from io import BytesIO
from datetime import datetime, timedelta
from collections import defaultdict
import qrcode
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
import sqlite3
from config import config
from utils import XUIClient, generate_vless_link, get_client_link, setup_logging
from panel_monitor import PanelMonitor
from cascade_client import CascadeClient, CascadeAPIError, AWGXUIBOT_IFACE_NAME, AWGXUIBOT_PROTOCOL

try:
    from remote_monitor import RemoteMonitor
    REMOTE_MONITOR_AVAILABLE = True
except ImportError:
    REMOTE_MONITOR_AVAILABLE = False

try:
    from system_monitor import SystemMonitor
    SYSTEM_MONITOR_AVAILABLE = True
except ImportError:
    SYSTEM_MONITOR_AVAILABLE = False
    import warnings
    warnings.warn("SystemMonitor not available - system monitoring disabled")

setup_logging(config.logging)
logger = logging.getLogger(__name__)

bot = Bot(token=config.bot.token)
dp = Dispatcher()


@dp.errors()
async def errors_handler(event: types.ErrorEvent):
    """Глушим типовые ошибки Telegram которые не требуют внимания."""
    exception = event.exception
    if isinstance(exception, TelegramBadRequest):
        msg = str(exception)
        if "message is not modified" in msg:
            return True  # игнорируем — содержимое не изменилось
        if "query is too old" in msg or "query ID is invalid" in msg:
            return True  # игнорируем — пользователь нажал старую кнопку
    return False  # остальные ошибки логируются как обычно


xui_client = XUIClient(config)

# Глобальная ссылка на монитор панелей (инициализируется в main())
_panel_monitor = None
_remote_monitor = None  # RemoteMonitor — мониторинг удалённых панелей
_cascade_online: dict = {}  # cascade_id -> bool, кэш последней проверки доступности


# ─── SQLite-хранилище имён AWG-пиров (только peer_name для отображения) ──────
# Срок действия хранится в самом Cascade (поле expiredAt) — бот его не дублирует.

import os as _os
_AWG_PEERS_DB = _os.path.join(_os.path.dirname(__file__), "..", "settings.db")


def _awg_db_ensure():
    """Создаёт таблицу awg_peers если её нет."""
    with sqlite3.connect(_AWG_PEERS_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS awg_peers (
                peer_id    TEXT PRIMARY KEY,
                peer_name  TEXT NOT NULL
            )
        """)


def _awg_peer_save(peer_id: str, peer_name: str):
    _awg_db_ensure()
    with sqlite3.connect(_AWG_PEERS_DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO awg_peers (peer_id, peer_name) VALUES (?,?)",
            (peer_id, peer_name),
        )


def _awg_peer_get_name(peer_id: str) -> str | None:
    """Возвращает peer_name по peer_id или None если не найден."""
    _awg_db_ensure()
    with sqlite3.connect(_AWG_PEERS_DB) as conn:
        row = conn.execute(
            "SELECT peer_name FROM awg_peers WHERE peer_id=?", (peer_id,)
        ).fetchone()
    return row[0] if row else None


def get_panel_online_status(panel_id: str) -> bool:
    """Возвращает последний известный статус панели из монитора.
    True = онлайн (или монитор ещё не запущен — не блокируем).
    """
    if _panel_monitor is None:
        return True
    state = _panel_monitor.panel_states.get(panel_id)
    if state is None:
        return True
    return state.is_available


def make_panel_client(panel_id: str) -> XUIClient:
    """Создаёт временный XUIClient для указанной панели (не меняет глобальный xui_client)"""
    xui_cfg = config.panel_manager.create_xui_config_from_panel(panel_id)
    if not xui_cfg:
        raise ValueError(f"Панель {panel_id} не найдена")

    panel_cfg = config.panel_manager.get_panel(panel_id)

    import types as _types

    # Строим vpn-конфиг из конкретной панели, а не из глобального config.vpn.
    # Это важно: server_address (и все Reality-параметры) должны браться
    # из той панели, для которой создаётся клиент.
    server_addr = (panel_cfg.server_address or panel_cfg.server_ip or '').strip()
    panel_vpn = _types.SimpleNamespace(
        server_address=server_addr,
        server_port=config.common.server_port,
        transport=panel_cfg.transport,
        security=panel_cfg.security,
        tls_sni=panel_cfg.tls_sni,
        tls_fingerprint=panel_cfg.tls_fingerprint or config.common.tls_fingerprint,
        tls_alpn=config.common.tls_alpn,
        reality_sni=panel_cfg.reality_sni,
        reality_fingerprint=panel_cfg.reality_fingerprint,
        reality_public_key=panel_cfg.reality_public_key,
        reality_short_id=panel_cfg.reality_short_id,
        xhttp_mode=config.common.xhttp_mode,
    )

    tmp_config = _types.SimpleNamespace(
        xui=xui_cfg,
        vpn=panel_vpn,
        common=config.common,
    )
    return XUIClient(tmp_config)


def get_available_panels() -> list:
    """Возвращает список панелей доступных пользователям для создания ключей.
    Правило: panel0 (по ID) или сетевые v3+.
    """
    panels = config.panel_manager.get_all_panels()
    result = []
    for panel_id, panel_cfg in panels.items():
        is_local_panel = (panel_id == "panel0")
        is_v3 = panel_cfg.is_v3() if hasattr(panel_cfg, 'is_v3') else False
        if is_local_panel or is_v3:
            result.append((panel_id, panel_cfg))
    return result


def _build_panels_block() -> str:
    """Компактный список всех панелей: алиас, локация, доступность."""
    panels = config.panel_manager.get_all_panels()
    if not panels:
        return ""
    lines = []
    for panel_id, p in panels.items():
        status = "🟢" if get_panel_online_status(panel_id) else "🔴"
        location = p.location_label or ''
        loc_part = f" · <code>{location}</code>" if location else ""
        lines.append(f"{status} <b>{p.alias}</b>{loc_part}")
    return "\n".join(lines) + "\n\n"


def _build_panels_block_admin() -> str:
    """Список панелей для главного меню администратора — формат как в окне Панели 3xui."""
    panels = config.panel_manager.get_all_panels()
    if not panels:
        return ""
    current_panel_id = config.panel_manager.get_current_panel_id()
    lines = []
    for panel_id, p in panels.items():
        online_icon = "🟢 Онлайн" if get_panel_online_status(panel_id) else "🔴 Оффлайн"
        is_current = panel_id == current_panel_id
        panel_icon = "✅" if is_current else "⏸️"
        location = p.location_label or ''
        location_str = f"  |  📍 {location}" if location else ""
        transport = (getattr(p, 'transport', '') or '—').upper()
        security = (getattr(p, 'security', '') or '—').upper()
        url = getattr(p, 'xui_url', '') or ''
        panel_id_str = (
            f"<a href=\"{url}\">{panel_id}</a>" if url else f"<code>{panel_id}</code>"
        )
        line = (
            f"{panel_icon} <b>{p.alias}</b>  <code>v{p.xui_version}</code>{location_str}\n"
            f"   {online_icon}  |  🆔 {panel_id_str}\n"
            f"   🔌 <code>{transport}</code> · <code>{security}</code>"
        )
        lines.append(line)
    return "\n".join(lines) + "\n\n"


def _build_cascade_block_admin() -> str:
    """Список Cascade-серверов для главного меню администратора."""
    cascade_servers = config.get_cascade_servers()
    if not cascade_servers:
        return ""
    lines = []
    for srv in cascade_servers:
        online = _cascade_online.get(srv.cascade_id, True)
        online_icon = "🟢 Онлайн" if online else "🔴 Оффлайн"
        url = srv.url or ''
        id_str = (
            f"<a href=\"{url}\">{srv.cascade_id}</a>" if url else f"<code>{srv.cascade_id}</code>"
        )
        line = (
            f"⏸️ <b>{srv.label}</b>\n"
            f"   {online_icon}  |  🆔 {id_str}"
        )
        lines.append(line)
    return "\n".join(lines) + "\n\n"


async def send_panel_select(chat_id: int, header: str, back_callback: str, key_type: str):
    """Отправляет экран выбора панели.
    key_type: 'new' | 'temp'
    """
    # Живая проверка доступности всех панелей перед показом списка выбора
    await _refresh_panel_states_now()

    current_panel_id = config.panel_manager.get_current_panel_id()
    available = get_available_panels()

    if not available:
        await bot.send_message(chat_id, "❌ Нет доступных панелей для создания ключей.")
        return

    buttons = []
    online_count = 0
    for panel_id, panel_cfg in available:
        alias = panel_cfg.alias or panel_id
        location = getattr(panel_cfg, 'location_label', '')
        loc_str = f" [{location}]" if location else ""
        is_current = (panel_id == current_panel_id)
        is_online = get_panel_online_status(panel_id)

        if is_online:
            online_count += 1
            icon = "✅" if is_current else "⏸️"
            buttons.append([
                InlineKeyboardButton(
                    text=f"{icon} {alias}{loc_str} 🟢",
                    callback_data=f"sel_panel_{key_type}:{panel_id}"
                )
            ])
        else:
            # Оффлайн — показываем, но кнопка ведёт на заглушку
            buttons.append([
                InlineKeyboardButton(
                    text=f"🔴 {alias}{loc_str} — недоступна",
                    callback_data=f"panel_offline:{panel_id}"
                )
            ])

    if online_count == 0:
        await bot.send_message(
            chat_id,
            "❌ <b>Все панели сейчас недоступны.</b>\n\nПопробуйте позже.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)]
            ])
        )
        return

    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await bot.send_message(chat_id, header, reply_markup=keyboard, parse_mode="HTML")



# ─── FSM-состояния ────────────────────────────────────────────────────────
class NewClientState(StatesGroup):
    waiting_for_panel = State()
    waiting_for_comment = State()


class TempKeyState(StatesGroup):
    waiting_for_panel = State()
    waiting_for_duration = State()
    waiting_for_comment = State()


class AwgKeyState(StatesGroup):
    waiting_for_cascade_server = State()
    waiting_for_duration = State()


class AddUserState(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_username = State()


class PaymentLinkState(StatesGroup):
    waiting_for_link = State()


class SetPriceState(StatesGroup):
    waiting_for_price = State()


# ─── Шаг 1: Создать ключ → выбор типа срока ───────────────────────────────
@dp.callback_query(lambda c: c.data == "create_key")
async def on_create_key(callback_query: types.CallbackQuery):
    """Подменю: Бессрочный ключ / Временный ключ"""
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await callback_query.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="♾ Бессрочный ключ", callback_data="key_duration_new"),
            InlineKeyboardButton(text="⏱ Временный ключ",  callback_data="key_duration_temp"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")],
    ])
    await bot.send_message(
        callback_query.message.chat.id,
        "🔑 <b>Создать ключ</b>\n\nВыберите тип ключа:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )



# ─── Вспомогательная функция: экран выбора комментария ─────────────────────
async def _send_comment_choice(chat_id: int, state: FSMContext, key_type: str, context_text: str):
    """Показывает экран выбора: добавить комментарий или создать без него.
    key_type: "new" (бессрочный VLESS) | "temp" (временный VLESS) | "awg_new" | "awg_temp"
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✏️ Добавить комментарий (рекомендуется)",
                callback_data=f"comment_choice_add:{key_type}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="⏭️ Без комментария",
                callback_data=f"comment_choice_skip:{key_type}",
            ),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")],
    ])
    await bot.send_message(
        chat_id,
        f"{context_text}\n\n💬 Добавить комментарий к ключу?",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    # Сохраняем тип ключа и оставляем состояние — оно будет установлено в хендлере кнопки
    await state.update_data(_comment_key_type=key_type)



# ─── Шаг 2: выбор протокола ────────────────────────────────────────────────
@dp.callback_query(lambda c: c.data in ("key_duration_new", "key_duration_temp"))
async def on_key_duration(callback_query: types.CallbackQuery):
    """Подменю: VLESS ключ / AWG 3.0 ключ (неактивен)"""
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await callback_query.answer()
    duration_type = callback_query.data  # key_duration_new | key_duration_temp
    label = "♾ Бессрочный" if duration_type == "key_duration_new" else "⏱ Временный"
    # Показываем AWG как активный только если настроены Cascade-серверы
    cascade_servers = config.get_cascade_servers()
    if cascade_servers:
        awg_btn = InlineKeyboardButton(
            text="🔒 AWG 3.0 ключ",
            callback_data=f"key_proto_awg:{duration_type}",
        )
    else:
        awg_btn = InlineKeyboardButton(
            text="🔒 AWG 3.0 ключ",
            callback_data="key_proto_awg_no_cascade",
        )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🌐 VLESS ключ",
                callback_data=f"key_proto_vless:{duration_type}",
            ),
            awg_btn,
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="create_key")],
    ])
    await bot.send_message(
        callback_query.message.chat.id,
        f"🔑 <b>Создать ключ</b> · {label}\n\nВыберите протокол:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# ─── AWG 3.0 — нет Cascade серверов ───────────────────────────────────────
@dp.callback_query(lambda c: c.data == "key_proto_awg_no_cascade")
async def on_key_proto_awg_no_cascade(callback_query: types.CallbackQuery):
    await callback_query.answer(
        "🔒 AWG 3.0 ключ недоступен: не настроены Cascade-серверы в config.yaml",
        show_alert=True,
    )


# ─── AWG 3.0 — выбор Cascade-сервера ──────────────────────────────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("key_proto_awg:"))
async def on_key_proto_awg(callback_query: types.CallbackQuery, state: FSMContext):
    """Шаг 1 AWG: показываем список Cascade-серверов для выбора"""
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await callback_query.answer()
    duration_type = callback_query.data.split(":", 1)[1]  # key_duration_new | key_duration_temp

    cascade_servers = config.get_cascade_servers()
    if not cascade_servers:
        await bot.send_message(
            callback_query.message.chat.id,
            "❌ <b>Cascade-серверы не настроены.</b>\n\nДобавьте раздел <code>cascades:</code> в config.yaml.",
            parse_mode="HTML",
        )
        return

    await state.update_data(awg_duration_type=duration_type)
    await state.set_state(AwgKeyState.waiting_for_cascade_server)

    # Обновляем статусы Cascade перед показом списка
    await _refresh_panel_states_now()

    label = "♾ Бессрочный" if duration_type == "key_duration_new" else "⏱ Временный"
    buttons = []
    online_count = 0
    for srv in cascade_servers:
        online = _cascade_online.get(srv.cascade_id, None)
        if online is None:
            # Статус ещё неизвестен — считаем онлайн (первый запуск)
            online = True
        if online:
            online_count += 1
            buttons.append([
                InlineKeyboardButton(
                    text=f"🖥 {srv.label} 🟢",
                    callback_data=f"awg_sel_cascade:{srv.cascade_id}",
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🔴 {srv.label} — недоступен",
                    callback_data=f"cascade_offline:{srv.cascade_id}",
                )
            ])

    if online_count == 0:
        await bot.send_message(
            callback_query.message.chat.id,
            "🔴 <b>Все Cascade-серверы сейчас недоступны.</b>\n\nСоздание AWG ключей временно невозможно. Попробуйте позже.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data=f"key_duration_{duration_type.split('_')[-1]}")]
            ]),
        )
        await state.clear()
        return

    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"key_duration_{duration_type.split('_')[-1]}")])
    await bot.send_message(
        callback_query.message.chat.id,
        f"🔒 <b>AWG 3.0 ключ</b> · {label}\n\nВыберите сервер:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("awg_sel_cascade:"))
async def on_awg_select_cascade(callback_query: types.CallbackQuery, state: FSMContext):
    """Шаг 2 AWG: проверяем Cascade, получаем/создаём интерфейс.
    Для бессрочного ключа — сразу создаём пира.
    Для временного — сохраняем iface_id в state и переходим к выбору срока.
    """
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return

    cascade_id = callback_query.data.split(":", 1)[1]
    srv_list = config.get_cascade_servers()
    srv = next((s for s in srv_list if s.cascade_id == cascade_id), None)
    if not srv:
        await callback_query.answer("❌ Сервер не найден", show_alert=True)
        return

    await callback_query.answer()
    chat_id = callback_query.message.chat.id
    status_msg = await bot.send_message(chat_id, f"⏳ Подключаюсь к <b>{srv.label}</b>...", parse_mode="HTML")

    async with CascadeClient(
        url=srv.url,
        api_key=srv.api_key,
        username=srv.username,
        password=srv.password,
        label=srv.label,
    ) as client:
        # ── Проверка доступности Cascade ──────────────────────────────────
        try:
            await client.health()
        except CascadeAPIError as e:
            await status_msg.edit_text(
                f"❌ <b>Не удалось подключиться к серверу {srv.label}</b>\n\n"
                f"Cascade недоступен для этого сервера.\n"
                f"<i>{e}</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ]),
            )
            await state.clear()
            return

        # ── Получаем/создаём интерфейс AWGXUIBOT ──────────────────────────
        await status_msg.edit_text(
            f"⏳ Настраиваю интерфейс <b>{AWGXUIBOT_IFACE_NAME}</b> "
            f"(<code>{AWGXUIBOT_PROTOCOL}</code>)...",
            parse_mode="HTML",
        )
        try:
            iface = await client.get_or_create_awgxuibot_interface()
        except CascadeAPIError as e:
            await status_msg.edit_text(
                f"❌ <b>Ошибка создания интерфейса {AWGXUIBOT_IFACE_NAME}</b>\n\n<i>{e}</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ]),
            )
            await state.clear()
            return

        iface_id = iface.get("id")
        if not iface_id:
            await status_msg.edit_text(
                "❌ Не удалось получить ID интерфейса.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ]),
            )
            await state.clear()
            return

        # ── Для временного ключа — сохраняем контекст и показываем выбор срока ─
        data = await state.get_data()
        duration_type = data.get("awg_duration_type", "key_duration_new")
        if duration_type == "key_duration_temp":
            await bot.delete_message(chat_id, status_msg.message_id)
            await state.update_data(awg_iface_id=iface_id, awg_cascade_id=cascade_id)
            await state.set_state(AwgKeyState.waiting_for_duration)
            buttons = [
                [InlineKeyboardButton(text="🕐 1 час",   callback_data="awg_dur:1h")],
                [InlineKeyboardButton(text="📅 1 день",  callback_data="awg_dur:1d")],
                [InlineKeyboardButton(text="📅 3 дня",   callback_data="awg_dur:3d")],
                [InlineKeyboardButton(text="📅 7 дней",  callback_data="awg_dur:7d")],
                [InlineKeyboardButton(text="📅 30 дней", callback_data="awg_dur:30d")],
                [InlineKeyboardButton(text="🔙 Назад",   callback_data="back_to_start")],
            ]
            await bot.send_message(
                chat_id,
                f"⏰ <b>Временный AWG ключ</b>\n🖥 Сервер: <b>{srv.label}</b>\n\nВыберите срок действия:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                parse_mode="HTML",
            )
            return

        # ── Бессрочный: сразу создаём пира ────────────────────────────────
        username = callback_query.from_user.username
        if not username:
            username = (callback_query.from_user.first_name or "user").lower().replace(" ", "_")

        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        peer_name = f"{username}_{suffix}"

        await status_msg.edit_text(f"⏳ Создаю ключ <b>{peer_name}</b>...", parse_mode="HTML")
        try:
            peer = await client.create_peer(iface_id, peer_name)
        except CascadeAPIError as e:
            await status_msg.edit_text(
                f"❌ <b>Ошибка создания ключа</b>\n\n<i>{e}</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ]),
            )
            await state.clear()
            return

        peer_id = peer.get("id")
        if not peer_id:
            await status_msg.edit_text(
                "❌ Не удалось получить ID пира.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ]),
            )
            await state.clear()
            return

    _awg_peer_save(peer_id, peer_name)
    await bot.delete_message(chat_id, status_msg.message_id)
    await state.clear()

    cb_qr = f"awg_showqr:{cascade_id}:{iface_id}:{peer_id}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Показать QR / Ключ", callback_data=cb_qr)],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")],
    ])
    await bot.send_message(
        chat_id,
        f"🔒 <b>AWG 3.0 ключ создан</b> · ♾ Бессрочный\n\n"
        f"🖥 Сервер: <b>{srv.label}</b>\n"
        f"👤 Имя: <code>{peer_name}</code>\n"
        f"📅 Срок: ♾ Бессрочный",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ─── AWG 3.0 — выбор срока для временного ключа ───────────────────────────
@dp.callback_query(AwgKeyState.waiting_for_duration, lambda c: c.data and c.data.startswith("awg_dur:"))
async def on_awg_select_duration(callback_query: types.CallbackQuery, state: FSMContext):
    """Шаг 3 AWG (временный): выбор срока → создаём пира → сохраняем в БД."""
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return

    dur_code = callback_query.data.split(":", 1)[1]  # 1h | 1d | 3d | 7d | 30d
    duration_map = {
        "1h":  (1/24,  "1 час"),
        "1d":  (1,     "1 день"),
        "3d":  (3,     "3 дня"),
        "7d":  (7,     "7 дней"),
        "30d": (30,    "30 дней"),
    }
    if dur_code not in duration_map:
        await callback_query.answer("❌ Неизвестный срок", show_alert=True)
        return

    days, duration_text = duration_map[dur_code]

    data = await state.get_data()
    cascade_id = data.get("awg_cascade_id")
    iface_id   = data.get("awg_iface_id")

    if not cascade_id or not iface_id:
        await callback_query.answer("❌ Потеряны данные сессии", show_alert=True)
        await state.clear()
        return

    srv_list = config.get_cascade_servers()
    srv = next((s for s in srv_list if s.cascade_id == cascade_id), None)
    if not srv:
        await callback_query.answer("❌ Сервер не найден", show_alert=True)
        await state.clear()
        return

    await callback_query.answer()
    chat_id = callback_query.message.chat.id

    username = callback_query.from_user.username
    if not username:
        username = (callback_query.from_user.first_name or "user").lower().replace(" ", "_")

    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    peer_name = f"{username}_{suffix}"

    status_msg = await bot.send_message(
        chat_id,
        f"⏳ Создаю временный ключ <b>{peer_name}</b> на {duration_text}...",
        parse_mode="HTML",
    )

    expires_dt = datetime.utcnow() + timedelta(days=days)
    expired_at_str = expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        async with CascadeClient(
            url=srv.url,
            api_key=srv.api_key,
            username=srv.username,
            password=srv.password,
            label=srv.label,
        ) as client:
            peer = await client.create_peer(iface_id, peer_name, expired_at=expired_at_str)
    except CascadeAPIError as e:
        await status_msg.edit_text(
            f"❌ <b>Ошибка создания ключа</b>\n\n<i>{e}</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ]),
        )
        await state.clear()
        return

    peer_id = peer.get("id")
    if not peer_id:
        await status_msg.edit_text(
            "❌ Не удалось получить ID пира.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ]),
        )
        await state.clear()
        return

    _awg_peer_save(peer_id, peer_name)
    await bot.delete_message(chat_id, status_msg.message_id)
    await state.clear()

    expiry_str = expires_dt.strftime("%Y-%m-%d %H:%M UTC")
    cb_qr = f"awg_showqr:{cascade_id}:{iface_id}:{peer_id}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Показать QR / Ключ", callback_data=cb_qr)],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")],
    ])
    await bot.send_message(
        chat_id,
        f"⏰ <b>Временный AWG ключ создан</b> · {duration_text}\n\n"
        f"🖥 Сервер: <b>{srv.label}</b>\n"
        f"👤 Имя: <code>{peer_name}</code>\n"
        f"📅 Истекает: {expiry_str}",
        parse_mode="HTML",
        reply_markup=keyboard,
    )



# ─── Хендлеры выбора: добавить комментарий или без ─────────────────────────

@dp.callback_query(lambda c: c.data and c.data.startswith("comment_choice_add:"))
async def on_comment_choice_add(callback_query: types.CallbackQuery, state: FSMContext):
    """Пользователь выбрал «добавить комментарий» — показываем форму ввода."""
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await callback_query.answer()
    key_type = callback_query.data.split(":", 1)[1]

    if key_type == "new":
        await state.set_state(NewClientState.waiting_for_comment)
        await bot.send_message(
            callback_query.message.chat.id,
            "📝 Введите комментарий к новому бессрочному ключу\n\n"
            "Или отправьте /start для отмены",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ]),
        )
    elif key_type == "temp":
        await state.set_state(TempKeyState.waiting_for_comment)
        await bot.send_message(
            callback_query.message.chat.id,
            "📝 Введите комментарий к временному ключу\n\n"
            "Или отправьте /start для отмены",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ]),
        )


@dp.callback_query(lambda c: c.data and c.data.startswith("comment_choice_skip:"))
async def on_comment_choice_skip(callback_query: types.CallbackQuery, state: FSMContext):
    """Пользователь выбрал «без комментария» — создаём ключ с пустым комментарием."""
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await callback_query.answer()
    key_type = callback_query.data.split(":", 1)[1]
    data = await state.get_data()

    if key_type == "new":
        # VLESS бессрочный — имитируем ввод пустого комментария
        panel_id = data.get('selected_panel_id') or config.panel_manager.get_current_panel_id()
        if not get_panel_online_status(panel_id):
            panel_cfg_chk = config.panel_manager.get_panel(panel_id)
            alias_chk = panel_cfg_chk.alias if panel_cfg_chk else panel_id
            await callback_query.message.answer(
                f"🔴 <b>Панель {alias_chk} сейчас недоступна.</b>\n\nПопробуйте позже.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ])
            )
            await state.clear()
            return
        panel_cfg = config.panel_manager.get_panel(panel_id)
        username = callback_query.from_user.username
        if not username:
            username = (callback_query.from_user.first_name or "user").lower().replace(" ", "_")
        panel_prefix = (panel_cfg.alias[:5].lower() if panel_cfg and panel_cfg.alias else "panel")
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        email = f"{panel_prefix}_{username}_{random_suffix}"
        status_msg = await callback_query.message.answer("🔄 Ожидайте...")
        async with make_panel_client(panel_id) as local_client:
            result = await local_client.add_client(email, 0, 3650, "")
            if result['success']:
                vless_link = await get_client_link(local_client, email, result['uuid'], local_client.config.vpn, panel_cfg.inbound_id)
                if not vless_link:
                    await status_msg.edit_text("❌ Ошибка получения ссылки")
                    await state.clear()
                    return
                await bot.delete_message(callback_query.message.chat.id, status_msg.message_id)
                location = getattr(panel_cfg, 'location_label', '')
                loc_str = f" [{location}]" if location else ""
                pid = data.get('selected_panel_id', '')
                cb_qr = f"showqr_{pid}:{result['uuid']}" if pid else f"showqr_{result['uuid']}"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📱 Показать QR / Ключ", callback_data=cb_qr)],
                    [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")]
                ])
                await callback_query.message.answer(
                    f"🔑 <b>Бессрочный ключ создан</b>\n\n"
                    f"📡 Панель: <b>{panel_cfg.alias}{loc_str}</b>",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            else:
                await status_msg.edit_text(f"❌ Ошибка: {result.get('error')}")
        await state.clear()

    elif key_type == "temp":
        # VLESS временный — имитируем ввод пустого комментария
        panel_id = data.get('selected_panel_id') or config.panel_manager.get_current_panel_id()
        duration = data.get('temp_duration', '1d')
        if not get_panel_online_status(panel_id):
            panel_cfg_chk = config.panel_manager.get_panel(panel_id)
            alias_chk = panel_cfg_chk.alias if panel_cfg_chk else panel_id
            await callback_query.message.answer(
                f"🔴 <b>Панель {alias_chk} сейчас недоступна.</b>\n\nПопробуйте позже.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ])
            )
            await state.clear()
            return
        duration_map = {
            '1h': (1/24, '1 час'), '1d': (1, '1 день'),
            '3d': (3, '3 дня'),    '7d': (7, '7 дней'), '30d': (30, '30 дней')
        }
        days, duration_text = duration_map.get(duration, (1, '1 день'))
        panel_cfg = config.panel_manager.get_panel(panel_id)
        username = callback_query.from_user.username
        if not username:
            username = (callback_query.from_user.first_name or "user").lower().replace(" ", "_")
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        email = f"temp_{username}_{random_suffix}"
        status_msg = await callback_query.message.answer(f"🔄 Создаю временный ключ на {duration_text}...")
        async with make_panel_client(panel_id) as local_client:
            result = await local_client.add_client(email, 0, days, f"Временный {duration_text}")
            if result['success']:
                vless_link = await get_client_link(local_client, email, result['uuid'], local_client.config.vpn, panel_cfg.inbound_id)
                if not vless_link:
                    await status_msg.edit_text("❌ Ошибка получения ссылки")
                    await state.clear()
                    return
                await bot.delete_message(callback_query.message.chat.id, status_msg.message_id)
                location = getattr(panel_cfg, 'location_label', '')
                loc_str = f" [{location}]" if location else ""
                pid = data.get('selected_panel_id', '')
                cb_qr = f"showqr_{pid}:{result['uuid']}" if pid else f"showqr_{result['uuid']}"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📱 Показать QR / Ключ", callback_data=cb_qr)],
                    [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")]
                ])
                await callback_query.message.answer(
                    f"⏰ <b>Временный ключ на {duration_text} создан</b>\n\n"
                    f"📡 Панель: <b>{panel_cfg.alias}{loc_str}</b>",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            else:
                await status_msg.edit_text(f"❌ Ошибка: {result.get('error')}")
        await state.clear()


@dp.callback_query(lambda c: c.data and c.data.startswith("awg_showqr:"))
async def on_awg_show_qr(callback_query: types.CallbackQuery):
    """Показать QR-код и конфиг AWG 3.0 пира — по аналогии с VLESS show_qr_code"""
    # awg_showqr:{cascade_id}:{iface_id}:{peer_id}
    parts = callback_query.data.split(":", 3)
    if len(parts) != 4:
        await callback_query.answer("❌ Неверный формат", show_alert=True)
        return

    _, cascade_id, iface_id, peer_id = parts

    # Имя пира — из локальной БД (fallback: peer_id)
    peer_name = _awg_peer_get_name(peer_id) or peer_id

    srv_list = config.get_cascade_servers()
    srv = next((s for s in srv_list if s.cascade_id == cascade_id), None)
    if not srv:
        await callback_query.answer("❌ Сервер не найден в конфигурации", show_alert=True)
        return

    try:
        async with CascadeClient(
            url=srv.url,
            api_key=srv.api_key,
            username=srv.username,
            password=srv.password,
            label=srv.label,
        ) as client:
            # Получаем конфиг и метаданные пира параллельно
            peer_conf, peer_info = await asyncio.gather(
                client.get_peer_config(iface_id, peer_id),
                client.get_peer(iface_id, peer_id),
            )

        # ── Срок действия из Cascade ──────────────────────────────────────
        expired_at_raw = peer_info.get("expiredAt", "") if isinstance(peer_info, dict) else ""
        if expired_at_raw:
            try:
                from datetime import timezone
                expiry_dt = datetime.fromisoformat(expired_at_raw.replace("Z", "+00:00"))
                expiry_str = expiry_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                key_type_line = f"⏰ Временный · до {expiry_str}"
            except Exception:
                key_type_line = f"⏰ Временный · {expired_at_raw}"
        else:
            key_type_line = "♾ Бессрочный"

        # Генерируем QR из текста .conf
        # AWG 3.0 конфиг содержит много данных (параметры обфускации + ключи),
        # поэтому нужен крупный box_size — иначе QR слишком плотный для камеры мобильного.
        # ERROR_CORRECT_L минимизирует количество модулей (конфиги не нуждаются в коррекции ошибок).
        qr = qrcode.QRCode(
            box_size=10,
            border=4,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
        )
        qr.add_data(peer_conf)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        qr_img.save(buf, format="PNG")
        buf.seek(0)

        await callback_query.answer()

        conf_bytes = peer_conf.encode("utf-8")
        conf_filename = f"awg_{peer_id}.conf"

        # Telegram не позволяет смешивать Photo и Document в одной media group —
        # отправляем QR-фото и .conf-файл двумя отдельными сообщениями.
        await callback_query.message.answer_photo(
            photo=types.BufferedInputFile(buf.getvalue(), filename="awg.png"),
        )
        await callback_query.message.answer_document(
            document=types.BufferedInputFile(conf_bytes, filename=conf_filename),
            caption=(
                f"🔒 <b>AWG 3.0 ключ</b>\n"
                f"🖥 <b>Сервер:</b> {srv.label}\n"
                f"👤 <b>Имя:</b> <code>{peer_name}</code>\n"
                f"📅 <b>Срок:</b> {key_type_line}\n\n"
                f"⬇️ Импортируйте файл в приложение поддерживающее <b>AWG</b>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")],
            ]),
        )

    except CascadeAPIError as e:
        logger.error(f"[awg_showqr] cascade error: {e}")
        await callback_query.answer(f"❌ Ошибка Cascade: {e}", show_alert=True)
    except Exception as e:
        logger.error(f"[awg_showqr] unexpected error: {e}", exc_info=True)
        await callback_query.answer(f"❌ Ошибка: {e}", show_alert=True)


# ─── Шаг 3: VLESS → существующий flow выбора сервера ──────────────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("key_proto_vless:"))
async def on_key_proto_vless(callback_query: types.CallbackQuery, state: FSMContext):
    """Перенаправляет в flow создания ключа (бессрочный или временный)"""
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await callback_query.answer()
    duration_type = callback_query.data.split(":", 1)[1]  # key_duration_new | key_duration_temp

    chat_id = callback_query.message.chat.id
    await _refresh_panel_states_now()
    available = get_available_panels()
    online_available = [(pid, pcfg) for pid, pcfg in available if get_panel_online_status(pid)]

    if duration_type == "key_duration_new":
        # ── Бессрочный ключ ──
        if len(online_available) == 0:
            await bot.send_message(
                chat_id,
                "🔴 <b>Все серверы сейчас недоступны.</b>\n\nПопробуйте позже.",
                parse_mode="HTML",
            )
            await state.clear()
            return
        if len(online_available) == 1:
            panel_id, panel_cfg = online_available[0]
            await state.update_data(selected_panel_id=panel_id)
            location = getattr(panel_cfg, 'location_label', '')
            loc_str = f" [{location}]" if location else ""
            await _send_comment_choice(chat_id, state, "new", f"📡 Панель: <b>{panel_cfg.alias}{loc_str}</b>")
            return
        await send_panel_select(
            chat_id,
            "🔑 <b>Создание бессрочного ключа</b>\n\nВыберите сервер:",
            "create_key", "new",
        )
    else:
        # ── Временный ключ ──
        if len(online_available) == 0:
            await bot.send_message(
                chat_id,
                "🔴 <b>Все серверы сейчас недоступны.</b>\n\nПопробуйте позже.",
                parse_mode="HTML",
            )
            await state.clear()
            return
        if len(online_available) == 1:
            panel_id, panel_cfg = online_available[0]
            await state.update_data(selected_panel_id=panel_id)
            await state.set_state(TempKeyState.waiting_for_duration)
            await _send_duration_select(chat_id, panel_cfg.alias)
            return
        await send_panel_select(
            chat_id,
            "⏰ <b>Создание временного ключа</b>\n\nВыберите сервер:",
            "create_key", "temp",
        )





async def _send_duration_select(chat_id: int, panel_alias: str):
    """Отправляет экран выбора срока временного ключа"""
    buttons = [
        [InlineKeyboardButton(text="🕐 1 час",   callback_data="tempkey_1h")],
        [InlineKeyboardButton(text="📅 1 день",  callback_data="tempkey_1d")],
        [InlineKeyboardButton(text="📅 3 дня",   callback_data="tempkey_3d")],
        [InlineKeyboardButton(text="📅 7 дней",  callback_data="tempkey_7d")],
        [InlineKeyboardButton(text="📅 30 дней", callback_data="tempkey_30d")],
        [InlineKeyboardButton(text="🔙 Назад",   callback_data="back_to_start")],
    ]
    await bot.send_message(
        chat_id,
        f"⏰ <b>Временный ключ</b>\n📡 Панель: <b>{panel_alias}</b>\n\nВыберите срок действия:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )


# ─── Хендлер нажатия на недоступную панель ─────────────────────────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("panel_offline:"))
async def on_panel_offline(callback_query: types.CallbackQuery):
    panel_id = callback_query.data.split(":", 1)[1]
    panel_cfg = config.panel_manager.get_panel(panel_id)
    alias = panel_cfg.alias if panel_cfg else panel_id
    location = getattr(panel_cfg, 'location_label', '') if panel_cfg else ''
    loc_str = f" [{location}]" if location else ""
    await callback_query.answer(
        f"🔴 {alias}{loc_str} сейчас недоступна.\nВыберите другую панель.",
        show_alert=True
    )


# ─── Хендлер нажатия на недоступный Cascade-сервер ────────────────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("cascade_offline:"))
async def on_cascade_offline(callback_query: types.CallbackQuery):
    cascade_id = callback_query.data.split(":", 1)[1]
    srv_list = config.get_cascade_servers()
    srv = next((s for s in srv_list if s.cascade_id == cascade_id), None)
    label = srv.label if srv else cascade_id
    await callback_query.answer(
        f"🔴 {label} сейчас недоступен.\nВыберите другой сервер.",
        show_alert=True
    )


# ─── Хендлер выбора панели для бессрочного ключа ───────────────────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("sel_panel_new:"))
async def on_select_panel_new(callback_query: types.CallbackQuery, state: FSMContext):
    panel_id = callback_query.data.split(":", 1)[1]
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    # Повторная проверка онлайн-статуса (мог измениться пока открыто меню)
    if not get_panel_online_status(panel_id):
        panel_cfg = config.panel_manager.get_panel(panel_id)
        alias = panel_cfg.alias if panel_cfg else panel_id
        await callback_query.answer(
            f"🔴 {alias} сейчас недоступна. Выберите другую панель.",
            show_alert=True
        )
        return
    panel_cfg = config.panel_manager.get_panel(panel_id)
    if not panel_cfg:
        await callback_query.answer("❌ Панель не найдена", show_alert=True)
        return
    await callback_query.answer()
    await state.update_data(selected_panel_id=panel_id)
    location = getattr(panel_cfg, 'location_label', '')
    loc_str = f" [{location}]" if location else ""
    await _send_comment_choice(callback_query.message.chat.id, state, "new", f"📡 Панель: <b>{panel_cfg.alias}{loc_str}</b>")


# ─── Хендлер выбора панели для временного ключа ────────────────────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("sel_panel_temp:"))
async def on_select_panel_temp(callback_query: types.CallbackQuery, state: FSMContext):
    panel_id = callback_query.data.split(":", 1)[1]
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    # Повторная проверка онлайн-статуса
    if not get_panel_online_status(panel_id):
        panel_cfg = config.panel_manager.get_panel(panel_id)
        alias = panel_cfg.alias if panel_cfg else panel_id
        await callback_query.answer(
            f"🔴 {alias} сейчас недоступна. Выберите другую панель.",
            show_alert=True
        )
        return
    panel_cfg = config.panel_manager.get_panel(panel_id)
    if not panel_cfg:
        await callback_query.answer("❌ Панель не найдена", show_alert=True)
        return
    await callback_query.answer()
    await state.update_data(selected_panel_id=panel_id)
    await state.set_state(TempKeyState.waiting_for_duration)
    await _send_duration_select(callback_query.message.chat.id, panel_cfg.alias)


# Антифлуд защита
user_message_count = defaultdict(list)
ANTIFLOOD_LIMIT = 5
ANTIFLOOD_TIME = 60
ANTIFLOOD_BLOCK_TIME = 300
flood_blocked_users = {}


def is_flood_blocked(user_id: int) -> bool:
    if user_id in flood_blocked_users:
        if datetime.now() < flood_blocked_users[user_id]:
            return True
        else:
            del flood_blocked_users[user_id]
    return False


def check_antiflood(user_id: int) -> bool:
    now = datetime.now()
    user_message_count[user_id] = [t for t in user_message_count[user_id] if
                                   now - t < timedelta(seconds=ANTIFLOOD_TIME)]
    user_message_count[user_id].append(now)
    if len(user_message_count[user_id]) > ANTIFLOOD_LIMIT:
        flood_blocked_users[user_id] = now + timedelta(seconds=ANTIFLOOD_BLOCK_TIME)
        user_message_count[user_id] = []
        return True
    return False


def is_admin(user_id):
    return user_id == config.users_db.get_main_admin()


def is_allowed(user_id):
    return config.users_db.is_allowed(user_id)


def is_blocked_by_admin(user_id):
    return config.users_db.is_blocked_by_admin(user_id)


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    # Отменяем ожидание комментария, если оно было
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("✅ Редактирование отменено.")

    # Проверка на блокировку администратором
    if is_blocked_by_admin(user_id):
        await message.answer("⛔ Вы заблокированы администратором.")
        return

    # Проверка наличия активных ключей у пользователя (автодобавление)
    if not is_allowed(user_id) and username:
        # Проверяем есть ли у пользователя активные ключи на любой из доступных панелей
        has_keys = False
        for panel_id, panel_cfg in get_available_panels():
            if not get_panel_online_status(panel_id):
                continue
            try:
                async with make_panel_client(panel_id) as _pc:
                    if await _pc.has_active_keys(username):
                        has_keys = True
                        break
            except Exception as _e:
                logger.warning(f"Автодобавление: не удалось проверить панель {panel_id}: {_e}")

        if has_keys:
            # Пользователь имеет активные ключи - добавляем автоматически
            admin_id = config.users_db.get_main_admin()
            
            # Проверяем был ли пользователь ранее в системе (возвращение)
            was_user_before = config.users_db.was_user_registered(user_id)
            
            # Добавляем пользователя
            config.users_db.add_user(user_id, username, admin_id)
            logger.info(f"✅ Автодобавлен пользователь {username} (ID: {user_id}) с активными ключами")
            
            # Уведомления администратору отключены
            # Пользователь добавлен автоматически при наличии активных ключей
            
            # Показываем меню пользователя с кнопками
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔑 Создать ключ", callback_data="create_key"),
                    InlineKeyboardButton(text="📡 Статус", callback_data="status_servers"),
                ],
                [
                    InlineKeyboardButton(text="🗝 Мои ключи", callback_data="cmd_myclients")
                ]
            ])
            await message.answer(
                f"👤 <b>Пользователь:</b> {username or first_name}\n\n",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return
    
    if not is_allowed(user_id):
        if is_flood_blocked(user_id):
            await message.answer("⛔ Вы временно заблокированы за флуд. Попробуйте позже.")
            return
        if check_antiflood(user_id):
            await message.answer(f"⚠️ Слишком много запросов! Заблокированы на {ANTIFLOOD_BLOCK_TIME // 60} минут.")
            return

    if is_allowed(user_id):
        if is_admin(user_id):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔑 Создать ключ", callback_data="create_key"),
                    InlineKeyboardButton(text="📡 Статус", callback_data="status_servers"),
                ],
                [
                    InlineKeyboardButton(text="🗝 Мои ключи", callback_data="cmd_myclients"),
                    InlineKeyboardButton(text="📱 Скачать приложение", callback_data="app_download"),
                ],
                [
                    InlineKeyboardButton(text="⚙️ Администрирование", callback_data="server_status"),
                ]
            ])
            await message.answer(
                f"👑 Администратор\n\n"
                f"",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔑 Создать ключ", callback_data="create_key"),
                    InlineKeyboardButton(text="📡 Статус", callback_data="status_servers"),
                ],
                [
                    InlineKeyboardButton(text="🗝 Мои ключи", callback_data="cmd_myclients"),
                    InlineKeyboardButton(text="📱 Скачать приложение", callback_data="app_download"),
                ]
            ])
            await message.answer(
                f"👤 <b>Пользователь:</b> {username or first_name}\n\n"
                f"📲 <b>Как начать:</b>\n"
                f"1. Скачайте приложение\n"
                f"2. Создайте ключ или выберите его из <b>Мои ключи</b>\n"
                f"3. Добавьте ключ в приложении через кнопку <b>+</b> или <b>Добавить</b>",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    else:
        # Если запрос уже ожидает решения — молчим (не показываем кнопку повторно)
        if config.users_db.has_pending_request(user_id):
            await message.answer(
                "⏳ Ваш запрос на доступ уже отправлен.\n\n",
                parse_mode="HTML"
            )
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Запросить доступ", callback_data="request_access")]
        ])
        await message.answer(
            f"👋 Добро пожаловать, {first_name}!\n\n"
            f"Нажмите кнопку ниже, чтобы начать.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )


@dp.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        if not config.users_db.has_pending_request(message.from_user.id):
            await message.answer("⛔ Доступ запрещен. Пожалуйста, сначала выполните /start")
        return
    if is_blocked_by_admin(message.from_user.id):
        await message.answer("⛔ Вы заблокированы.")
        return
    await state.set_state(NewClientState.waiting_for_panel)
    await _refresh_panel_states_now()
    available = get_available_panels()
    online_available = [(pid, pcfg) for pid, pcfg in available if get_panel_online_status(pid)]
    if len(online_available) == 0:
        await message.answer(
            "🔴 <b>Все серверы сейчас недоступны.</b>\n\nСоздание ключей временно невозможно. Попробуйте позже.",
            parse_mode="HTML"
        )
        await state.clear()
        return
    if len(online_available) == 1:
        panel_id, panel_cfg = online_available[0]
        await state.update_data(selected_panel_id=panel_id)
        await _send_comment_choice(message.chat.id, state, "new", f"📡 Панель: <b>{panel_cfg.alias}</b>")
        return
    await send_panel_select(
        message.chat.id,
        "🔑 <b>Создание бессрочного ключа</b>\n\nВыберите сервер:",
        "back_to_start", "new"
    )


@dp.message(NewClientState.waiting_for_comment)
async def process_new_comment(message: Message, state: FSMContext):
    comment = message.text.strip()

    if comment.startswith('/'):
        await message.answer(
            "❌ Недопустимый символ! Комментарий не может начинаться с '/'. Введите заново или /start")
        return
    if len(comment) > 50:
        await message.answer("❌ Комментарий слишком длинный (максимум 50 символов). Попробуйте снова:")
        return

    data = await state.get_data()
    panel_id = data.get('selected_panel_id') or config.panel_manager.get_current_panel_id()

    # Проверяем онлайн-статус перед попыткой подключения
    if not get_panel_online_status(panel_id):
        panel_cfg_chk = config.panel_manager.get_panel(panel_id)
        alias_chk = panel_cfg_chk.alias if panel_cfg_chk else panel_id
        location_chk = getattr(panel_cfg_chk, 'location_label', '') if panel_cfg_chk else ''
        loc_chk = f" [{location_chk}]" if location_chk else ""
        await message.answer(
            f"🔴 <b>Панель {alias_chk}{loc_chk} сейчас недоступна.</b>\n\n"
            f"Попробуйте позже или выберите другую панель.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ])
        )
        await state.clear()
        return

    try:
        panel_cfg = config.panel_manager.get_panel(panel_id)
    except Exception as e:
        await message.answer(f"❌ Ошибка получения панели: {e}")
        await state.clear()
        return

    username = message.from_user.username
    if not username:
        username = message.from_user.first_name.lower().replace(" ", "_")

    panel_prefix = (panel_cfg.alias[:5].lower() if panel_cfg and panel_cfg.alias else "panel")
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    email = f"{panel_prefix}_{username}_{random_suffix}"

    status_msg = await message.answer(f"🔄 Ожидайте...")

    async with make_panel_client(panel_id) as local_client:
        result = await local_client.add_client(email, 0, 3650, comment)

        if result['success']:
            vless_link = await get_client_link(local_client, email, result['uuid'], local_client.config.vpn, panel_cfg.inbound_id)
            if not vless_link:
                await status_msg.edit_text(f"❌ Ошибка получения ссылки")
                await state.clear()
                return

            await bot.delete_message(message.chat.id, status_msg.message_id)

            location = getattr(panel_cfg, 'location_label', '')
            loc_str = f" [{location}]" if location else ""
            pid = data.get('selected_panel_id', '')
            cb_qr = f"showqr_{pid}:{result['uuid']}" if pid else f"showqr_{result['uuid']}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Показать QR / Ключ", callback_data=cb_qr)],
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")]
            ])
            display_comment = comment.replace('Временный ', '')
            await message.answer(
                f"🔑 <b>Бессрочный ключ создан</b>\n\n"
                f"📡 Панель: <b>{panel_cfg.alias}{loc_str}</b>\n"
                f"📝 Комментарий: {display_comment}",
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await status_msg.edit_text(f"❌ Ошибка: {result.get('error')}")

    await state.clear()


@dp.message(Command("tempkey"))
async def cmd_temp_key(message: Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        if not config.users_db.has_pending_request(message.from_user.id):
            await message.answer("⛔ Доступ запрещен. Пожалуйста, сначала выполните /start")
        return
    if is_blocked_by_admin(message.from_user.id):
        await message.answer("⛔ Вы заблокированы администратором.")
        return
    await state.set_state(TempKeyState.waiting_for_panel)
    await _refresh_panel_states_now()
    available = get_available_panels()
    online_available = [(pid, pcfg) for pid, pcfg in available if get_panel_online_status(pid)]
    if len(online_available) == 0:
        await message.answer(
            "🔴 <b>Все серверы сейчас недоступны.</b>\n\nСоздание ключей временно невозможно. Попробуйте позже.",
            parse_mode="HTML"
        )
        await state.clear()
        return
    if len(online_available) == 1:
        panel_id, panel_cfg = online_available[0]
        await state.update_data(selected_panel_id=panel_id)
        await state.set_state(TempKeyState.waiting_for_duration)
        await _send_duration_select(message.chat.id, panel_cfg.alias)
        return
    await send_panel_select(
        message.chat.id,
        "⏰ <b>Создание временного ключа</b>\n\nВыберите сервер:",
        "back_to_start", "temp"
    )


@dp.callback_query(lambda c: c.data and c.data.startswith('tempkey_') and not c.data.startswith('tempkey_comment_'))
async def process_tempkey_duration(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработка выбора срока для временного ключа"""
    duration = callback_query.data.split('_')[1]  # 1h, 1d, 3d, 7d, 30d
    await state.update_data(temp_duration=duration)
    duration_map = {
        '1h': '1 час', '1d': '1 день', '3d': '3 дня', '7d': '7 дней', '30d': '30 дней'
    }
    duration_text = duration_map.get(duration, '1 день')
    # Получаем alias выбранной панели для отображения
    data = await state.get_data()
    panel_id = data.get('selected_panel_id') or config.panel_manager.get_current_panel_id()
    panel_cfg = config.panel_manager.get_panel(panel_id)
    panel_alias = panel_cfg.alias if panel_cfg else "N/A"
    await callback_query.answer()
    await _send_comment_choice(
        callback_query.message.chat.id, state, "temp",
        f"⏰ <b>Временный ключ на {duration_text}</b>\n📡 Панель: <b>{panel_alias}</b>"
    )


@dp.message(TempKeyState.waiting_for_comment)
async def process_tempkey_comment(message: Message, state: FSMContext):
    comment = message.text.strip()

    if comment.startswith('/'):
        await message.answer(
            "❌ Недопустимый символ! Комментарий не может начинаться с '/'. Введите заново или /start")
        return
    if len(comment) > 50:
        await message.answer("❌ Комментарий слишком длинный (максимум 50 символов). Попробуйте снова:")
        return

    data = await state.get_data()
    duration = data.get('temp_duration', '1d')
    panel_id = data.get('selected_panel_id') or config.panel_manager.get_current_panel_id()

    # Проверяем онлайн-статус перед попыткой подключения
    if not get_panel_online_status(panel_id):
        panel_cfg_chk = config.panel_manager.get_panel(panel_id)
        alias_chk = panel_cfg_chk.alias if panel_cfg_chk else panel_id
        location_chk = getattr(panel_cfg_chk, 'location_label', '') if panel_cfg_chk else ''
        loc_chk = f" [{location_chk}]" if location_chk else ""
        await message.answer(
            f"🔴 <b>Панель {alias_chk}{loc_chk} сейчас недоступна.</b>\n\n"
            f"Попробуйте позже или выберите другую панель.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ])
        )
        await state.clear()
        return

    duration_map = {
        '1h': (1/24, '1 час'), '1d': (1, '1 день'),
        '3d': (3, '3 дня'),    '7d': (7, '7 дней'), '30d': (30, '30 дней')
    }
    days, duration_text = duration_map.get(duration, (1, '1 день'))

    try:
        panel_cfg = config.panel_manager.get_panel(panel_id)
    except Exception as e:
        await message.answer(f"❌ Ошибка получения панели: {e}")
        await state.clear()
        return

    username = message.from_user.username
    if not username:
        username = message.from_user.first_name.lower().replace(" ", "_")

    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    email = f"temp_{username}_{random_suffix}"

    status_msg = await message.answer(f"🔄 Создаю временный ключ на {duration_text}...")

    async with make_panel_client(panel_id) as local_client:
        result = await local_client.add_client(email, 0, days, f"{comment} (Временный {duration_text})")

        if result['success']:
            vless_link = await get_client_link(local_client, email, result['uuid'], local_client.config.vpn, panel_cfg.inbound_id)
            if not vless_link:
                await status_msg.edit_text(f"❌ Ошибка получения ссылки")
                await state.clear()
                return

            await bot.delete_message(message.chat.id, status_msg.message_id)

            location = getattr(panel_cfg, 'location_label', '')
            loc_str = f" [{location}]" if location else ""
            pid = data.get('selected_panel_id', '')
            cb_qr = f"showqr_{pid}:{result['uuid']}" if pid else f"showqr_{result['uuid']}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Показать QR / Ключ", callback_data=cb_qr)],
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")]
            ])
            display_comment = comment.replace('Временный ', '')
            await message.answer(
                f"⏰ <b>Временный ключ на {duration_text} создан</b>\n\n"
                f"📡 Панель: <b>{panel_cfg.alias}{loc_str}</b>\n"
                f"📝 Комментарий: {display_comment}",
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await status_msg.edit_text(f"❌ Ошибка: {result.get('error')}")

    await state.clear()


@dp.message(Command("myclients"))
async def cmd_my_clients(message: Message):
    if not is_allowed(message.from_user.id):
        if not config.users_db.has_pending_request(message.from_user.id):
            await message.answer("⛔ Доступ запрещен. Пожалуйста, сначала выполните /start")
        return

    username = message.from_user.username
    if not username:
        await message.answer("❌ У вас не установлен username в Telegram.\n\nУстановите username в настройках Telegram для использования бота.")
        return

    # Получаем ключи пользователя из X-UI по username
    clients = await xui_client.get_user_clients_by_username(username)

    if not clients:
        await message.answer("📭 У вас пока нет ключей.\n\n")
        return

    # Подсчитываем статистику
    active_count = sum(1 for c in clients if c['status'] == 'active')
    inactive_count = sum(1 for c in clients if c['status'] == 'inactive')
    expired_count = sum(1 for c in clients if c['status'] == 'expired')

    buttons = []
    for client in clients:
        email = client['email']
        comment = client['comment']
        status = client['status']
        
        # Формируем текст кнопки
        if comment:
            # Убираем слово "Временный" из комментария для кнопки
            display_comment = comment.replace('Временный ', '')
            display_text = f"{display_comment[:25]}"
        else:
            display_text = f"{email[:25]}"
        
        # Добавляем иконку статуса
        if status == 'active':
            icon = "✅"
        elif status == 'inactive':
            icon = "⏸️"
        else:  # expired
            icon = "⏰"
        
        buttons.append([
            InlineKeyboardButton(text=f"{icon} {display_text}", callback_data=f"myclient_{client['uuid']}")
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    text = f"📋 <b>Ваши ключи ({len(clients)})</b>\n\n"
    text += f"✅ Активных: {active_count}\n"
    text += f"⏸️ Неактивных: {inactive_count}\n"
    text += f"⏰ Просроченных: {expired_count}\n\n"
    text += "Выберите ключ для просмотра:"
    
    await message.answer(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@dp.callback_query(lambda c: c.data and c.data.startswith('myclient_'))
async def show_my_client_details(callback_query: types.CallbackQuery):
    """Показать детали ключа пользователя из /myclients"""
    client_uuid = callback_query.data.split('_', 1)[1]

    # Получаем детали клиента из X-UI
    client = await xui_client.get_client_details(client_uuid)

    if not client:
        await callback_query.answer("❌ Ключ не найден!", show_alert=True)
        return

    email = client['email']
    comment = client['comment']
    status = client['status']

    # Определяем статус с иконкой
    if status == 'active':
        status_text = "✅ Активен"
    elif status == 'inactive':
        status_text = "⏸️ Неактивен (выключен)"
    else:  # expired
        status_text = "⏰ Просрочен"
    
    await callback_query.answer()
    
    # Редактируем текущее сообщение - показываем информацию с кнопками
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔑 Показать ключ", callback_data=f"showmykey_{client_uuid}"),
            InlineKeyboardButton(text="📱 Показать QR", callback_data=f"showqr_{client_uuid}")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="cmd_myclients")]
    ])
    
    # Всегда отправляем новое сообщение для навигации
    # Убираем слово "Временный" из комментария для отображения
    display_comment = comment.replace('Временный ', '') if comment else 'Без комментария'
    await bot.send_message(
        callback_query.message.chat.id,
        f"🔑 <b>Информация о ключе</b>\n\n"
        f"Статус: {status_text}\n"
        f"📝 Комментарий: {display_comment}",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@dp.callback_query(lambda c: c.data and c.data.startswith('showmykey_'))
async def show_my_key_link(callback_query: types.CallbackQuery):
    """Показать VLESS ссылку для ключа из Мои ключи"""
    client_uuid = callback_query.data.split('_', 1)[1]
    
    try:
        # Получаем детали клиента
        client = await xui_client.get_client_details(client_uuid)
        
        if not client:
            await callback_query.answer("❌ Ключ не найден!", show_alert=True)
            return
        
        # Генерируем VLESS ссылку
        vless_link = await get_client_link(xui_client, client['email'], client_uuid, config.vpn, config.xui.inbound_id)
        if not vless_link:
            await callback_query.answer("❌ Ошибка получения ссылки!", show_alert=True)
            return
        
        await callback_query.answer()
        
        # Редактируем текущее сообщение - показываем только ссылку
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"myclient_{client_uuid}")]
        ])
        
        await callback_query.message.edit_text(
            f"<code>{vless_link}</code>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка показа ключа: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.message(Command("users"))
async def cmd_list_users(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Отказано в доступе.")
        return

    users = config.users_db.list_users()
    main_admin = config.users_db.get_main_admin()

    try:
        admin_chat = await bot.get_chat(main_admin)
        admin_name = f"@{admin_chat.username}" if admin_chat.username else str(main_admin)
    except:
        admin_name = str(main_admin)

    text = f"👑 <b>Администратор:</b> {admin_name}\n\n"

    if users:
        text += "<b>📋 Пользователи:</b>\n"
        for user_id, username, added_at in users:
            blocked_status = "🔒 Заблокирован" if config.users_db.is_blocked_by_admin(user_id) else "✅ Активен"
            if username:
                text += f"• @{username} (ID: {user_id}) - {blocked_status} - добавлен {added_at[:10]}\n"
            else:
                try:
                    chat = await bot.get_chat(user_id)
                    user_name = f"@{chat.username}" if chat.username else str(user_id)
                    text += f"• {user_name} - {blocked_status} - добавлен {added_at[:10]}\n"
                except:
                    text += f"• ID: {user_id} - {blocked_status} - добавлен {added_at[:10]}\n"
    else:
        text += "Нет добавленных пользователей."

    # Добавляем кнопки действий и навигации
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="show_users")],
        [InlineKeyboardButton(text="🔒 Заблокировать", callback_data="action_block")],
        [InlineKeyboardButton(text="🔓 Разблокировать", callback_data="action_unblock")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data="action_remove")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)




# Кеш для списка клиентов
allclients_cache = {}

@dp.message(Command("allclients"))
async def cmd_all_clients(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Отказано в доступе.")
        return

    try:
        # Получаем все клиенты
        all_clients = await xui_client.get_all_clients()
        
        if not all_clients:
            await message.answer("📭 Нет ключей в системе.")
            return
        
        # Получаем список онлайн клиентов
        online_clients_emails = await xui_client.get_online_clients()
        
        # Подсчитываем статистику
        total_count = len(all_clients)
        active_count = sum(1 for c in all_clients if c['status'] == 'active')
        inactive_count = sum(1 for c in all_clients if c['status'] == 'inactive')
        expired_count = sum(1 for c in all_clients if c['status'] == 'expired')
        online_count = len(online_clients_emails)
        
        # Подсчитываем общий расход трафика
        total_traffic = 0
        for client in all_clients:
            # up - отправлено, down - скачано
            traffic = client.get('up', 0) + client.get('down', 0)
            total_traffic += traffic
        
        # Форматируем трафик
        def format_traffic(bytes_value):
            if bytes_value < 1024:
                return f"{bytes_value} B"
            elif bytes_value < 1024**2:
                return f"{bytes_value / 1024:.2f} KB"
            elif bytes_value < 1024**3:
                return f"{bytes_value / (1024**2):.2f} MB"
            else:
                return f"{bytes_value / (1024**3):.2f} GB"
        
        # Получаем информацию о текущей панели
        current_panel = config.get_current_panel()
        panel_info = ""
        if current_panel:
            panel_info = f"📡 <b>Панель:</b> {current_panel.alias}  {current_panel.xui_version}\n\n"
        
        # Формируем текст статистики
        text = panel_info
        text += f"🔑 Всего ключей: {total_count}\n"
        text += f"✅ Активных: {active_count}\n"
        text += f"⏸️ Неактивных: {inactive_count}\n"
        text += f"⏰ Просроченных: {expired_count}\n"
        text += f"🟢 Онлайн: {online_count}\n"
        text += f"📊 Расход трафика: {format_traffic(total_traffic)}\n\n"
        
        # Ограничение на количество кнопок
        clients_to_show = all_clients[:50]
        if total_count > 50:
            text += f"⚠️ <i>Показаны первые 50 из {total_count} ключей</i>\n\n"
        
        text += "<b>Выберите ключ:</b>"
        
        # Создаем кнопки для каждого клиента в два ряда
        buttons = []
        row = []
        for i, client in enumerate(clients_to_show):
            email = client['email']
            comment = client['comment']
            is_online = email in online_clients_emails
            
            # Подсчитываем трафик клиента
            client_traffic = client.get('up', 0) + client.get('down', 0)
            traffic_mb = client_traffic / (1024**2)  # Переводим в MB
            
            # Формируем текст кнопки (короче для двух колонок)
            if comment:
                # Убираем слово "Временный" из комментария для кнопки
                display_comment = comment.replace('Временный ', '')
                button_text = f"{email[:10]}-{display_comment[:10]}"
            else:
                button_text = email[:20]
            
            # Добавляем расход трафика
            if traffic_mb >= 1:
                button_text += f" ({traffic_mb:.0f}MB)"
            
            # Для онлайн клиентов показываем только 🟢, для остальных - иконку статуса
            if is_online:
                button_text = f"🟢 {button_text}"
            elif client['status'] == 'active':
                button_text = f"✅ {button_text}"
            elif client['status'] == 'inactive':
                button_text = f"⏸️ {button_text}"
            else:  # expired
                button_text = f"⏰ {button_text}"
            
            row.append(InlineKeyboardButton(text=button_text, callback_data=f"allclient_{client['uuid']}"))
            
            # Добавляем ряд после каждых двух кнопок
            if len(row) == 2:
                buttons.append(row)
                row = []
        
        # Добавляем последний ряд если он не пустой
        if row:
            buttons.append(row)
        
        # Добавляем кнопку очистки если есть просроченные ключи
        if expired_count > 0:
            buttons.append([
                InlineKeyboardButton(text=f"🧹 Очистить просроченные ({expired_count})", callback_data="cleanup_expired")
            ])
        
        # Добавляем кнопку "Назад"
        buttons.append([
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_server_status")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # Сохраняем в кеш с временной меткой
        import time
        allclients_cache[message.from_user.id] = {
            'time': time.time(),
            'data': all_clients
        }
        
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка в cmd_all_clients: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")


@dp.callback_query(lambda c: c.data and c.data.startswith('allclient_'))
async def show_all_client_details(callback_query: types.CallbackQuery):
    """Показать детальную информацию о ключе из списка всех ключей"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    client_uuid = callback_query.data.split('_', 1)[1]
    
    try:
        # Получаем детали клиента
        client = await xui_client.get_client_details(client_uuid)
        
        if not client:
            await callback_query.answer("❌ Ключ не найден!", show_alert=True)
            return
        
        # Определяем статус с иконкой
        if client['status'] == 'active':
            status_text = "✅ Активен"
        elif client['status'] == 'inactive':
            status_text = "⏸️ Неактивен (выключен)"
        else:  # expired
            status_text = "⏰ Просрочен"
        
        # Форматируем скачанный трафик (up + down)
        downloaded_traffic = client.get('up', 0) + client.get('down', 0)
        
        def format_traffic(bytes_value):
            if bytes_value < 1024:
                return f"{bytes_value} B"
            elif bytes_value < 1024**2:
                return f"{bytes_value / 1024:.2f} KB"
            elif bytes_value < 1024**3:
                return f"{bytes_value / (1024**2):.2f} MB"
            else:
                return f"{bytes_value / (1024**3):.2f} GB"
        
        traffic_text = format_traffic(downloaded_traffic)
        
        # Форматируем срок окончания
        expiry_time = client['expiryTime']
        if expiry_time > 0:
            from datetime import datetime
            expiry_date = datetime.fromtimestamp(expiry_time / 1000)
            expiry_text = expiry_date.strftime("%Y-%m-%d %H:%M")
        else:
            expiry_text = "Бессрочно"
        
        # Формируем текст
        text = f"📋 <b>Информация о ключе</b>\n\n"
        text += f"{status_text}\n"
        text += f"📧 <b>Имя ключа:</b> <code>{client['email']}</code>\n"
        # Убираем слово "Временный" из комментария для отображения
        display_comment = client['comment'] if client['comment'] else 'Не указан'
        if display_comment != 'Не указан':
            display_comment = display_comment.replace('Временный ', '')
        text += f"📝 <b>Комментарий:</b> {display_comment}\n"
        text += f"📊 <b>Скачано:</b> {traffic_text}\n"
        text += f"📅 <b>Срок окончания:</b> {expiry_text}\n"
        
        # Создаем кнопки управления
        buttons = []
        
        # Кнопки "Показать ключ" и "Показать QR" в одной строке
        buttons.append([
            InlineKeyboardButton(text="🔑 Показать ключ", callback_data=f"showkey_{client_uuid}"),
            InlineKeyboardButton(text="📱 Показать QR", callback_data=f"showqr_{client_uuid}")
        ])
        
        # Кнопки включить/выключить в зависимости от статуса
        if client['enable']:
            buttons.append([InlineKeyboardButton(text="⏸️ Выключить ключ", callback_data=f"disable_{client_uuid}")])
        else:
            buttons.append([InlineKeyboardButton(text="✅ Включить ключ", callback_data=f"enable_{client_uuid}")])
        
        # Кнопка "Назад"
        buttons.append([InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_allclients")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # Всегда отправляем новое сообщение для навигации
        await bot.send_message(
            callback_query.message.chat.id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Ошибка показа деталей клиента: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith('showkey_'))
async def show_client_key(callback_query: types.CallbackQuery):
    """Показать VLESS ключ и QR-код"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    client_uuid = callback_query.data.split('_', 1)[1]
    
    try:
        # Получаем детали клиента
        client = await xui_client.get_client_details(client_uuid)
        
        if not client:
            await callback_query.answer("❌ Ключ не найден!", show_alert=True)
            return
        
        # Генерируем VLESS ссылку
        vless_link = await get_client_link(xui_client, client['email'], client['uuid'], config.vpn, config.xui.inbound_id)
        if not vless_link:
            await callback_query.answer("❌ Ошибка получения ссылки!", show_alert=True)
            return
        
        await callback_query.answer()
        
        # Редактируем текущее сообщение - показываем только ссылку
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"allclient_{client_uuid}")]
        ])
        
        await callback_query.message.edit_text(
            f"<code>{vless_link}</code>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка показа ключа: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith('enable_'))
async def enable_client(callback_query: types.CallbackQuery):
    """Включить ключ"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    client_uuid = callback_query.data.split('_', 1)[1]
    
    try:
        # Получаем email клиента для v3 API
        client = await xui_client.get_client_details(client_uuid)
        email = client.get('email') if client else None
        
        # Включаем клиента
        success = await xui_client.update_client_status(client_uuid, True, email)
        
        if success:
            await callback_query.answer("✅ Ключ включен")
            # Обновляем информацию о клиенте - получаем свежие данные
            await refresh_client_details(callback_query, client_uuid)
        else:
            await callback_query.answer("❌ Ошибка включения ключа", show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка включения клиента: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith('disable_'))
async def disable_client(callback_query: types.CallbackQuery):
    """Выключить ключ"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    client_uuid = callback_query.data.split('_', 1)[1]
    
    try:
        # Получаем email клиента для v3 API
        client = await xui_client.get_client_details(client_uuid)
        email = client.get('email') if client else None
        
        # Выключаем клиента
        success = await xui_client.update_client_status(client_uuid, False, email)
        
        if success:
            await callback_query.answer("⏸️ Ключ выключен")
            # Обновляем информацию о клиенте - получаем свежие данные
            await refresh_client_details(callback_query, client_uuid)
        else:
            await callback_query.answer("❌ Ошибка выключения ключа", show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка выключения клиента: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


async def refresh_client_details(callback_query: types.CallbackQuery, client_uuid: str):
    """Обновить информацию о клиенте после изменения статуса"""
    try:
        # Получаем обновленные детали клиента
        client = await xui_client.get_client_details(client_uuid)
        
        if not client:
            await callback_query.message.edit_text("❌ Ключ не найден!")
            return
        
        # Определяем статус с иконкой
        if client['status'] == 'active':
            status_text = "✅ Активен"
        elif client['status'] == 'inactive':
            status_text = "⏸️ Неактивен (выключен)"
        else:  # expired
            status_text = "⏰ Просрочен"
        
        # Форматируем трафик
        total_gb = client['totalGB']
        if total_gb > 0:
            traffic_text = f"{total_gb / (1024**3):.2f} GB"
        else:
            traffic_text = "Безлимит"
        
        # Форматируем срок окончания
        expiry_time = client['expiryTime']
        if expiry_time > 0:
            from datetime import datetime
            expiry_date = datetime.fromtimestamp(expiry_time / 1000)
            expiry_text = expiry_date.strftime("%Y-%m-%d %H:%M")
        else:
            expiry_text = "Бессрочно"
        
        # Формируем текст
        text = f"📋 <b>Информация о ключе</b>\n\n"
        text += f"{status_text}\n"
        text += f"📧 <b>Имя ключа:</b> <code>{client['email']}</code>\n"
        # Убираем слово "Временный" из комментария для отображения
        display_comment = client['comment'] if client['comment'] else 'Не указан'
        if display_comment != 'Не указан':
            display_comment = display_comment.replace('Временный ', '')
        text += f"📝 <b>Комментарий:</b> {display_comment}\n"
        text += f"📊 <b>Общий трафик:</b> {traffic_text}\n"
        text += f"📅 <b>Срок окончания:</b> {expiry_text}\n"
        
        # Создаем кнопки управления
        buttons = []
        
        # Кнопки "Показать ключ" и "Показать QR" в одной строке
        buttons.append([
            InlineKeyboardButton(text="🔑 Показать ключ", callback_data=f"showkey_{client_uuid}"),
            InlineKeyboardButton(text="📱 Показать QR", callback_data=f"showqr_{client_uuid}")
        ])
        
        # Кнопки включить/выключить в зависимости от статуса
        if client['enable']:
            buttons.append([InlineKeyboardButton(text="⏸️ Выключить ключ", callback_data=f"disable_{client_uuid}")])
        else:
            buttons.append([InlineKeyboardButton(text="✅ Включить ключ", callback_data=f"enable_{client_uuid}")])
        
        # Кнопка "Назад"
        buttons.append([InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_allclients")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка обновления информации о клиенте: {e}")
        await callback_query.message.edit_text(f"❌ Ошибка обновления: {str(e)}")


@dp.callback_query(lambda c: c.data and c.data.startswith('showlink_'))
async def show_link(callback_query: types.CallbackQuery):
    """Показать VLESS ссылку"""
    client_uuid = callback_query.data.split('_', 1)[1]
    
    try:
        # Получаем детали клиента
        client = await xui_client.get_client_details(client_uuid)
        
        if not client:
            await callback_query.answer("❌ Ключ не найден!", show_alert=True)
            return
        
        # Генерируем VLESS ссылку
        vless_link = await get_client_link(xui_client, client['email'], client_uuid, config.vpn, config.xui.inbound_id)
        if not vless_link:
            await callback_query.answer("❌ Ошибка получения ссылки!", show_alert=True)
            return
        
        await callback_query.answer()
        
        # Редактируем текущее сообщение - показываем только ссылку
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"backtoinfo_{client_uuid}")]
        ])
        
        await callback_query.message.edit_text(
            f"<code>{vless_link}</code>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка показа ссылки: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@dp.callback_query(lambda c: c.data and c.data.startswith('backtoinfo_'))
async def back_to_info(callback_query: types.CallbackQuery):
    """Вернуться к информации о ключе после просмотра ссылки"""
    client_uuid = callback_query.data.split('_', 1)[1]
    
    try:
        # Получаем детали клиента
        client = await xui_client.get_client_details(client_uuid)
        
        if not client:
            await callback_query.answer("❌ Ключ не найден!", show_alert=True)
            return
        
        # Определяем тип ключа по сроку действия
        expiry_time = client.get('expiryTime', 0)
        comment = client.get('comment', '')
        
        # Проверяем, временный ли это ключ
        if 'Временный' in comment:
            # Извлекаем длительность из комментария
            if '1 час' in comment:
                key_type = "⏰ <b>Временный ключ на 1 час</b>"
            elif '1 день' in comment:
                key_type = "⏰ <b>Временный ключ на 1 день</b>"
            elif '3 дня' in comment:
                key_type = "⏰ <b>Временный ключ на 3 дня</b>"
            elif '7 дней' in comment:
                key_type = "⏰ <b>Временный ключ на 7 дней</b>"
            elif '30 дней' in comment:
                key_type = "⏰ <b>Временный ключ на 30 дней</b>"
            else:
                key_type = "⏰ <b>Временный ключ</b>"
        else:
            key_type = "🔑 <b>Бессрочный ключ</b>"
        
        # Убираем префикс "Временный (...)" из комментария для отображения
        display_comment = comment.replace('Временный (1 час)', '').replace('Временный (1 день)', '').replace('Временный (3 дня)', '').replace('Временный (7 дней)', '').replace('Временный (30 дней)', '').strip()
        if display_comment.startswith('(') and display_comment.endswith(')'):
            display_comment = display_comment[1:-1]
        
        # Создаем кнопки
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔑 Показать ключ", callback_data=f"showlink_{client_uuid}"),
                InlineKeyboardButton(text="📱 Показать QR", callback_data=f"showqr_{client_uuid}")
            ],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")]
        ])
        
        await callback_query.answer()
        
        # Редактируем сообщение (теперь это всегда текстовое сообщение)
        await callback_query.message.edit_text(
            f"{key_type}\n\n"
            f"📝 Комментарий: {display_comment if display_comment else comment}",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка возврата к информации: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@dp.callback_query(lambda c: c.data and c.data.startswith('showqr_'))
async def show_qr_code(callback_query: types.CallbackQuery):
    """Показать QR-код для ключа"""
    # Формат callback_data: showqr_{panel_id}:{uuid}  или  showqr_{uuid} (старый)
    raw = callback_query.data.split('_', 1)[1]
    if ':' in raw:
        panel_id_hint, client_uuid = raw.split(':', 1)
    else:
        panel_id_hint, client_uuid = None, raw

    try:
        # Находим клиент и панель для ключа
        client = None
        target_panel_cfg = None
        vless_link = None

        # Список панелей для поиска: сначала указанная, потом остальные
        search_panels = []
        if panel_id_hint:
            search_panels.append(panel_id_hint)
        search_panels += [pid for pid, _ in get_available_panels() if pid != panel_id_hint]

        for search_pid in search_panels:
            try:
                is_global = (search_pid == config.panel_manager.get_current_panel_id() and not panel_id_hint)
                pc = xui_client if is_global else make_panel_client(search_pid)
                found = await pc.get_client_details(client_uuid)
                if found:
                    client = found
                    target_panel_cfg = config.panel_manager.get_panel(search_pid)
                    inbound_id = target_panel_cfg.inbound_id if target_panel_cfg else config.xui.inbound_id
                    vpn_cfg = pc.config.vpn
                    vless_link = await get_client_link(pc, client['email'], client_uuid, vpn_cfg, inbound_id)
                    if not is_global:
                        await pc.close()
                    break
                if not is_global:
                    await pc.close()
            except Exception:
                pass

        if not client:
            await callback_query.answer("❌ Ключ не найден!", show_alert=True)
            return

        # Проверяем активность ключа
        if client.get('status') != 'active':
            status = client.get('status')
            if status == 'expired':
                msg = "⏰ Ключ просрочен и недоступен для использования."
            elif status == 'inactive':
                msg = "⏸️ Ключ не активен."
            else:
                msg = "❌ Ключ не активен."
            await callback_query.answer(msg, show_alert=True)
            return

        # Генерируем VLESS ссылку (уже получена выше)
        vless_link = vless_link
        if not vless_link:
            await callback_query.answer("❌ Ошибка получения ссылки!", show_alert=True)
            return
        
        # Генерируем QR-код (уменьшенный размер)
        qr = qrcode.QRCode(box_size=5, border=2)
        qr.add_data(vless_link)
        qr.make()
        qr_img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)
        
        await callback_query.answer()
        
        # Формируем информативный caption с VLESS-ссылкой и комментарием
        comment = client.get('comment', 'Не указан')
        caption = f"""📱 <b>{client['email']}</b>

🔑 <b>VLESS-ссылка:</b>
<code>{vless_link}</code>

💬 <b>Комментарий:</b> {comment.replace('Временный ', '')}"""
        
        # Добавляем кнопки навигации
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Мои ключи", callback_data="cmd_myclients")],
            [InlineKeyboardButton(text="📱 Скачать приложение", callback_data="app_download")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")]
        ])
        
        # Отправляем QR-код как отдельное сообщение (не удаляя предыдущее)
        await callback_query.message.answer_photo(
            photo=types.BufferedInputFile(buffer.getvalue(), filename="vless.png"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка показа QR-кода: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@dp.callback_query(lambda c: c.data == "refresh_allclients")
async def refresh_allclients(callback_query: types.CallbackQuery):
    """Обновить список всех ключей с очисткой кеша"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    try:
        user_id = callback_query.from_user.id
        
        # Очищаем кеш для принудительного обновления
        if user_id in allclients_cache:
            del allclients_cache[user_id]
        
        # Показываем уведомление об обновлении
        await callback_query.answer("🔄 Обновление данных...", show_alert=False)
        
        # Перенаправляем на back_to_allclients для отображения обновленных данных (с флагом refresh)
        await back_to_allclients(callback_query, is_refresh=True)
        
    except Exception as e:
        logger.error(f"Ошибка обновления списка ключей: {e}")
        # Проверяем, не является ли ошибка "message is not modified"
        if "message is not modified" in str(e):
            await callback_query.answer("✅ Данные актуальны", show_alert=False)
        else:
            await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)



@dp.callback_query(lambda c: c.data == "back_to_allclients")
async def back_to_allclients(callback_query: types.CallbackQuery, is_refresh: bool = False):
    """Вернуться к списку всех ключей"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    try:
        import time
        user_id = callback_query.from_user.id
        
        # Всегда очищаем кеш при возврате из информации о ключе
        # для актуальности данных после включения/выключения
        if user_id in allclients_cache:
            del allclients_cache[user_id]
        
        # Получаем свежие данные
        all_clients = await xui_client.get_all_clients()
        
        # Получаем список онлайн клиентов
        online_clients_emails = await xui_client.get_online_clients()
        
        allclients_cache[user_id] = {
            'time': time.time(),
            'data': all_clients
        }
        
        # Получаем информацию о текущей панели
        current_panel = config.get_current_panel()
        panel_info = ""
        if current_panel:
            panel_info = f"📡 <b>Панель:</b> {current_panel.alias}  {current_panel.xui_version}\n\n"
        
        # Подсчитываем статистику
        if not all_clients:
            # Показываем полное окно даже если ключей нет
            text = "📋 <b>Все ключи</b>\n\n"
            text += panel_info
            text += f"🔑 Всего ключей: 0\n"
            text += f"✅ Активных: 0\n"
            text += f"⏸️ Неактивных: 0\n"
            text += f"⏰ Просроченных: 0\n"
            text += f"🟢 Онлайн: 0\n"
            text += f"📊 Расход трафика: 0 B\n\n"
            text += "📭 <i>Нет ключей в системе</i>"
            
            # Добавляем кнопки "Обновить" и "Назад"
            buttons = [
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_allclients")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="select_panel_to_connect")]
            ]
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            
            # Для refresh обновляем сообщение, для навигации - отправляем новое
            if is_refresh:
                await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            else:
                await bot.send_message(
                    callback_query.message.chat.id,
                    text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            await callback_query.answer()
            return
        
        # Подсчитываем статистику для существующих ключей
        total_count = len(all_clients)
        active_count = sum(1 for c in all_clients if c['status'] == 'active')
        inactive_count = sum(1 for c in all_clients if c['status'] == 'inactive')
        expired_count = sum(1 for c in all_clients if c['status'] == 'expired')
        online_count = len(online_clients_emails)
        
        
        # Подсчитываем общий расход трафика для обновленного списка
        total_traffic = 0
        for client in all_clients:
            traffic = client.get('up', 0) + client.get('down', 0)
            total_traffic += traffic
        
        # Форматируем трафик
        def format_traffic(bytes_value):
            if bytes_value < 1024:
                return f"{bytes_value} B"
            elif bytes_value < 1024**2:
                return f"{bytes_value / 1024:.2f} KB"
            elif bytes_value < 1024**3:
                return f"{bytes_value / (1024**2):.2f} MB"
            else:
                return f"{bytes_value / (1024**3):.2f} GB"
        
        # Обновляем текст статистики
        text = "📋 <b>Все ключи</b>\n\n"
        text += panel_info
        text += f"🔑 Всего ключей: {total_count}\n"
        text += f"✅ Активных: {active_count}\n"
        text += f"⏸️ Неактивных: {inactive_count}\n"
        text += f"⏰ Просроченных: {expired_count}\n"
        text += f"🟢 Онлайн: {online_count}\n"
        text += f"📊 Расход трафика: {format_traffic(total_traffic)}\n\n"
        
        # Ограничение на количество кнопок
        clients_to_show = all_clients[:50]
        if total_count > 50:
            text += f"⚠️ <i>Показаны первые 50 из {total_count} ключей</i>\n\n"
        
        text += "<b>Выберите ключ:</b>"
        
        # Создаем кнопки для каждого клиента в два ряда
        buttons = []
        row = []
        for i, client in enumerate(clients_to_show):
            email = client['email']
            comment = client['comment']
            is_online = email in online_clients_emails
            
            # Подсчитываем трафик клиента
            client_traffic = client.get('up', 0) + client.get('down', 0)
            traffic_mb = client_traffic / (1024**2)  # Переводим в MB
            
            # Формируем текст кнопки (короче для двух колонок)
            if comment:
                # Убираем слово "Временный" из комментария для кнопки
                display_comment = comment.replace('Временный ', '')
                button_text = f"{email[:10]}-{display_comment[:10]}"
            else:
                button_text = email[:20]
            
            # Добавляем расход трафика
            if traffic_mb >= 1:
                button_text += f" ({traffic_mb:.0f}MB)"
            
            # Для онлайн клиентов показываем только 🟢, для остальных - иконку статуса
            if is_online:
                button_text = f"🟢 {button_text}"
            elif client['status'] == 'active':
                button_text = f"✅ {button_text}"
            elif client['status'] == 'inactive':
                button_text = f"⏸️ {button_text}"
            else:  # expired
                button_text = f"⏰ {button_text}"
            
            row.append(InlineKeyboardButton(text=button_text, callback_data=f"allclient_{client['uuid']}"))
            
            # Добавляем ряд после каждых двух кнопок
            if len(row) == 2:
                buttons.append(row)
                row = []
        
        # Добавляем последний ряд если он не пустой
        if row:
            buttons.append(row)
        
        # Добавляем кнопку очистки если есть просроченные ключи
        if expired_count > 0:
            buttons.append([
                InlineKeyboardButton(text=f"🧹 Очистить просроченные ({expired_count})", callback_data="cleanup_expired")
            ])
        
        # Добавляем кнопки "Обновить" и "Назад"
        buttons.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_allclients"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="select_panel_to_connect")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # Для refresh обновляем сообщение, для навигации - отправляем новое
        if is_refresh:
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await bot.send_message(
                callback_query.message.chat.id,
                text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Ошибка возврата к списку: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not is_allowed(message.from_user.id):
        if not config.users_db.has_pending_request(message.from_user.id):
            await message.answer("⛔ Доступ запрещен. Отправьте /start для запроса доступа.")
        return

    if is_admin(message.from_user.id):
        text = """
<b>👑 Команды администратора:</b>

/new - Создать ключ
/tempkey - Временный ключ
/myclients - Мои ключи
/allclients - Все ключи
/users - Список пользователей
/help - Помощь

<i>Управление пользователями доступно через меню "Пользователи"</i>
<i>Пользователи сами отправляют запрос на доступ через /start</i>
"""
    else:
        text = """
⚠️ Одно устройство - один ключ.

<b>📖 Команды пользователя:</b>

/new - Создать ключ
/tempkey - Временный ключ
/myclients - Мои ключи
/help - Помощь

<i>Если у вас нет доступа - отправьте /start и нажмите "Запросить доступ"</i>
"""
    await message.answer(text, parse_mode="HTML")


@dp.callback_query(lambda c: c.data == "request_access")
async def process_request_access(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    username = callback_query.from_user.username
    first_name = callback_query.from_user.first_name
    last_name = callback_query.from_user.last_name

    if is_allowed(user_id):
        await callback_query.message.edit_text("✅ У вас уже есть доступ! Используйте /start")
        await callback_query.answer()
        return

    # Если запрос уже был отправлен и ожидает решения — ничего не делаем
    if config.users_db.has_pending_request(user_id):
        await callback_query.answer()
        return

    # Фиксируем запрос как ожидающий
    config.users_db.add_pending_request(user_id)

    admin_id = config.users_db.get_main_admin()
    user_info = f"@{username}" if username else first_name
    user_full_name = f"{first_name} {last_name if last_name else ''}".strip()

    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Разрешить", callback_data=f"approve_{user_id}")],
        [InlineKeyboardButton(text="🕐 Ключ на 1 час", callback_data=f"temp_1h_{user_id}"),
         InlineKeyboardButton(text="📅 Ключ на 1 день", callback_data=f"temp_1d_{user_id}")],
        [InlineKeyboardButton(text="📅 Ключ на 3 дня", callback_data=f"temp_3d_{user_id}"),
         InlineKeyboardButton(text="📅 Ключ на 7 дней", callback_data=f"temp_7d_{user_id}")],
        [InlineKeyboardButton(text="📅 Ключ на 30 дней", callback_data=f"temp_30d_{user_id}")],
        [InlineKeyboardButton(text="💳 Отправить на оплату", callback_data=f"pay_req_{user_id}")],
        [InlineKeyboardButton(text="🔄 Сброс статуса", callback_data=f"reset_pending_{user_id}")],
        [InlineKeyboardButton(text="❌ Заблокировать", callback_data=f"deny_{user_id}")]
    ])

    await bot.send_message(
        admin_id,
        f"🆕 <b>Новый запрос на доступ!</b>\n\n"
        f"👤 Пользователь: {user_info}\n"
        f"📝 Имя: {user_full_name}\n"
        f"🆔 ID: <code>{user_id}</code>",
        reply_markup=admin_keyboard,
        parse_mode="HTML"
    )

    await callback_query.message.edit_text("📨 Запрос отправлен! Ожидайте")
    await callback_query.answer()


@dp.callback_query(lambda c: c.data == "cleanup_expired")
async def process_cleanup_expired(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    await callback_query.message.edit_text("🔄 Удаление просроченных ключей...")

    try:
        expired_clients = await xui_client.get_expired_clients()
        
        if not expired_clients:
            await callback_query.message.edit_text("✅ Просроченных ключей не найдено")
            await callback_query.answer()
            return
        
        deleted_count = 0
        failed_count = 0
        deleted_keys = []
        
        for client in expired_clients:
            # Удаляем только временные ключи (начинаются с temp_)
            if client['email'].startswith('temp_'):
                success = await xui_client.delete_client(client['uuid'], client['email'])
                if success:
                    deleted_count += 1
                    deleted_keys.append(client['email'])
                    logger.info(f"🗑️ Удален истекший ключ: {client['email']}")
                else:
                    failed_count += 1
        
        result_text = f"🧹 <b>Очистка завершена</b>\n\n"
        result_text += f"✅ Удалено: {deleted_count}\n"
        if failed_count > 0:
            result_text += f"❌ Ошибок: {failed_count}\n"
        
        if deleted_keys:
            result_text += f"\n<b>Удаленные ключи:</b>\n"
            for key in deleted_keys[:10]:
                result_text += f"• {key}\n"
            if len(deleted_keys) > 10:
                result_text += f"... и еще {len(deleted_keys) - 10}\n"
        
        back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку ключей", callback_data="back_to_allclients")]
        ])
        await callback_query.message.edit_text(result_text, parse_mode="HTML", reply_markup=back_keyboard)
        
    except Exception as e:
        logger.error(f"Ошибка при очистке: {e}")
        back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку ключей", callback_data="back_to_allclients")]
        ])
        await callback_query.message.edit_text(f"❌ Ошибка при очистке: {str(e)}", reply_markup=back_keyboard)
    
    await callback_query.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith(('approve_', 'deny_')))
async def process_admin_decision(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    action, user_id_str = callback_query.data.split('_')
    user_id = int(user_id_str)

    try:
        chat = await bot.get_chat(user_id)
        username = chat.username
        first_name = chat.first_name
        user_info = f"@{username}" if username else first_name
    except:
        user_info = str(user_id)

    config.users_db.remove_pending_request(user_id)

    if action == "approve":
        if config.users_db.add_user(user_id, username, callback_query.from_user.id):
            await callback_query.message.edit_text(f"✅ Пользователь {user_info} добавлен!")
            try:
                await bot.send_message(user_id, "🚀 Доступ разрешен! Отправьте /start для начала работы.")
            except:
                pass
        else:
            await callback_query.message.edit_text(f"❌ Ошибка при добавлении пользователя!")
    else:
        await callback_query.message.edit_text(f"❌ Пользователь {user_info} заблокирован.")
        config.users_db.block_user(user_id, callback_query.from_user.id)
        try:
            await bot.send_message(user_id, "❌ Ваш запрос на доступ отклонен администратором.")
        except:
            pass
    await callback_query.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("reset_pending_"))
async def process_reset_pending(callback_query: types.CallbackQuery):
    """Сброс статуса ожидающего запроса — пользователь сможет отправить запрос заново."""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    user_id = int(callback_query.data.split("_", 2)[2])
    await callback_query.answer()

    try:
        chat = await bot.get_chat(user_id)
        user_info = f"@{chat.username}" if chat.username else (chat.first_name or str(user_id))
    except Exception:
        user_info = str(user_id)

    config.users_db.remove_pending_request(user_id)
    config.users_db.clear_pay_awaiting(user_id)  # снимаем флаг незавершённой оплаты
    config.users_db.clear_pay_sent(user_id)       # снимаем флаг отправленного запроса

    await callback_query.message.edit_text(
        f"🔄 Статус сброшен для {user_info}.\n\n"
        f"Пользователь может снова отправить запрос на доступ.",
        parse_mode="HTML",
    )

    try:
        await bot.send_message(
            user_id,
            "🔄 Время на обработку запроса вышло.\n\n"
            "Отправить запрос повторно — нажмите /start.",
        )
    except Exception:
        pass




@dp.callback_query(lambda c: c.data and c.data.startswith('temp_'))
async def process_temp_key_request(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    # Парсим данные: temp_1h_123456 -> duration=1h, user_id=123456
    parts = callback_query.data.split('_')
    duration = parts[1]  # 1h, 1d, 3d, 7d, 30d
    user_id = int(parts[2])

    # Определяем количество дней для ключа
    duration_map = {
        '1h': (1/24, '1 час'),      # 1 час = 1/24 дня
        '1d': (1, '1 день'),
        '3d': (3, '3 дня'),
        '7d': (7, '7 дней'),
        '30d': (30, '30 дней')
    }

    days, duration_text = duration_map.get(duration, (1, '1 день'))

    try:
        chat = await bot.get_chat(user_id)
        username = chat.username if chat.username else chat.first_name
        first_name = chat.first_name
        user_info = f"@{username}" if chat.username else first_name
    except:
        user_info = str(user_id)
        username = str(user_id)
        first_name = str(user_id)

    # При выдаче временного ключа пользователь остаётся кандидатом —
    # НЕ удаляем pending-запрос и НЕ добавляем в список пользователей.

    # Генерируем email для временного ключа
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    email = f"temp_{username}_{random_suffix}".lower().replace(" ", "_")
    comment = f"Временный ({duration_text})"

    # Создаем временный ключ
    await callback_query.message.edit_text(f"🔄 Создаю временный ключ на {duration_text}...")

    result = await xui_client.add_client(email, 0, days, comment)

    if result['success']:
        vless_link = await get_client_link(xui_client, email, result['uuid'], config.vpn, config.xui.inbound_id)
        if not vless_link:
            await callback_query.message.edit_text(f"❌ Ошибка получения ссылки")
            return

        # Отправляем ключ пользователю
        try:
            # Первое сообщение - только ссылка
            await bot.send_message(
                user_id,
                f"<code>{vless_link}</code>",
                parse_mode="HTML"
            )
            
            # Второе сообщение - информация с кнопками
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Показать QR", callback_data=f"showqr_{result['uuid']}")],
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")]
            ])
            
            await bot.send_message(
                user_id,
                f"🎁 <b>Временный ключ на {duration_text}</b>\n\n"
                f"⏰ Ключ действителен: {duration_text}\n"
                f"⚠️ После истечения срока ключ будет деактивирован",
                parse_mode="HTML",
                reply_markup=keyboard
            )

            # Уведомляем администратора об успехе
            await callback_query.message.edit_text(
                f"✅ Временный ключ на {duration_text} выдан пользователю {user_info}!\n\n"
                f"📧 Имя ключа: {email}\n"
                f"🆔 UUID: <code>{result['uuid']}</code>",
                parse_mode="HTML"
            )
        except Exception as e:
            await callback_query.message.edit_text(
                f"⚠️ Ключ создан, но не удалось отправить пользователю {user_info}.\n\n"
                f"Возможно, пользователь заблокировал бота.\n\n"
                f"📧 Имя ключа: {email}\n"
                f"🆔 UUID: <code>{result['uuid']}</code>",
                parse_mode="HTML"
            )
    else:
        await callback_query.message.edit_text(
            f"❌ Ошибка при создании ключа: {result.get('error')}"
        )

    await callback_query.answer()


# ─── Оплата: шаг 1 — администратор нажимает «Отправить на оплату» ──────────
def _get_pay_price(period_code: str) -> str:
    """Возвращает цену из БД или 'не указана'."""
    raw = config.users_db.get_admin_setting(f"pay_price_{period_code}", "")
    return raw if raw else "не указана"


@dp.callback_query(lambda c: c.data and c.data.startswith("pay_req_") and not c.data.startswith("pay_req_back_"))
async def process_pay_request(callback_query: types.CallbackQuery, state: FSMContext):
    """Отправляет кандидату выбор периода оплаты с ценами.
    Если кандидат уже подтвердил оплату и ждёт ссылку — сразу открываем ввод ссылки.
    """
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    user_id = int(callback_query.data.split("_", 2)[2])
    await callback_query.answer()

    # Проверяем флаг «ожидает ссылку на оплату»
    awaiting_period = config.users_db.get_pay_awaiting(user_id)
    if awaiting_period:
        period_labels = {"1h": "1 час", "3d": "3 дня", "30d": "30 дней"}
        period_text = period_labels.get(awaiting_period, awaiting_period)
        price = _get_pay_price(awaiting_period)

        try:
            chat = await bot.get_chat(user_id)
            user_info = f"@{chat.username}" if chat.username else (chat.first_name or str(user_id))
        except Exception:
            user_info = str(user_id)

        # Сразу переходим к вводу ссылки
        await state.set_state(PaymentLinkState.waiting_for_link)
        await state.update_data(pay_candidate_id=user_id, pay_period_text=period_text)

        await callback_query.message.edit_text(
            f"💳 <b>Незавершённая оплата</b>\n\n"
            f"👤 Пользователь: {user_info}\n"
            f"⏱ Период: <b>{period_text}</b>\n"
            f"💰 Сумма: <b>{price}</b>\n\n"
            f"Кандидат уже подтвердил оплату и ожидает ссылку.\n"
            f"Отправьте ссылку на оплату следующим сообщением.\n"
            f"Для отмены нажмите /start",
            parse_mode="HTML",
        )
        return

    p1h  = _get_pay_price("1h")
    p3d  = _get_pay_price("3d")
    p30d = _get_pay_price("30d")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⏱ 1 час  —  {p1h}",   callback_data=f"pay_period_1h_{user_id}")],
        [InlineKeyboardButton(text=f"📅 3 дня  —  {p3d}",   callback_data=f"pay_period_3d_{user_id}")],
        [InlineKeyboardButton(text=f"📅 30 дней  —  {p30d}", callback_data=f"pay_period_30d_{user_id}")],
    ])

    try:
        await bot.send_message(
            user_id,
            "💳 <b>Выберите период действия доступа:</b>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        # Помечаем: запрос успешно отправлен кандидату
        config.users_db.set_pay_sent(user_id)
        await callback_query.message.edit_text(
            callback_query.message.text + "\n\n⏳ Запрос на оплату отправлен кандидату.",
            parse_mode="HTML",
        )
    except Exception as e:
        await callback_query.answer(f"❌ Не удалось отправить сообщение: {e}", show_alert=True)


# ─── Оплата: шаг 2 — кандидат выбрал период, показываем диалог подтверждения
@dp.callback_query(lambda c: c.data and c.data.startswith("pay_period_"))
async def process_pay_period(callback_query: types.CallbackQuery):
    """Показывает кандидату сумму и кнопку «Оплатить»."""
    await callback_query.answer()

    # pay_period_1h_123456  ->  parts = ['pay', 'period', '1h', '123456']
    parts = callback_query.data.split("_")
    period_code = parts[2]          # 1h | 3d | 30d
    user_id     = int(parts[3])     # ID кандидата (sender == кандидат, не admin)

    period_labels = {"1h": "1 час", "3d": "3 дня", "30d": "30 дней"}
    period_text = period_labels.get(period_code, period_code)
    price = _get_pay_price(period_code)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💳 Оплатить",
            callback_data=f"pay_confirm_{period_code}_{user_id}",
        )],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"pay_req_back_{user_id}")],
    ])

    await callback_query.message.edit_text(
        f"💳 <b>Подтверждение оплаты</b>\n\n"
        f"⏱ Период: <b>{period_text}</b>\n"
        f"💰 Сумма: <b>{price}</b>\n\n"
        f" "
        f" ",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# ─── Оплата: шаг 2б — кандидат нажал «Назад» (вернуться к выбору периода) ─
@dp.callback_query(lambda c: c.data and c.data.startswith("pay_req_back_"))
async def process_pay_req_back(callback_query: types.CallbackQuery):
    """Возврат к выбору периода (кандидат нажал Назад на экране подтверждения)."""
    await callback_query.answer()
    user_id = int(callback_query.data.split("_", 3)[3])

    p1h  = _get_pay_price("1h")
    p3d  = _get_pay_price("3d")
    p30d = _get_pay_price("30d")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⏱ 1 час  —  {p1h}",    callback_data=f"pay_period_1h_{user_id}")],
        [InlineKeyboardButton(text=f"📅 3 дня  —  {p3d}",    callback_data=f"pay_period_3d_{user_id}")],
        [InlineKeyboardButton(text=f"📅 30 дней  —  {p30d}", callback_data=f"pay_period_30d_{user_id}")],
    ])
    await callback_query.message.edit_text(
        "💳 <b>Выберите период действия доступа:</b>",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# ─── Оплата: шаг 3 — кандидат нажал «Оплатить» → уведомляем администратора ─
@dp.callback_query(lambda c: c.data and c.data.startswith("pay_confirm_"))
async def process_pay_confirm(callback_query: types.CallbackQuery):
    """Кандидат подтвердил намерение оплатить — уведомляем администратора."""
    await callback_query.answer()

    # pay_confirm_1h_123456
    parts = callback_query.data.split("_")
    period_code = parts[2]
    user_id     = int(parts[3])

    period_labels = {"1h": "1 час", "3d": "3 дня", "30d": "30 дней"}
    period_text = period_labels.get(period_code, period_code)
    price = _get_pay_price(period_code)

    try:
        chat = await bot.get_chat(user_id)
        user_info = f"@{chat.username}" if chat.username else (chat.first_name or str(user_id))
    except Exception:
        user_info = str(user_id)

    # Сохраняем флаг: кандидат ожидает ссылку на оплату
    config.users_db.set_pay_awaiting(user_id, period_code)

    admin_id = config.users_db.get_main_admin()

    # Уведомляем администратора
    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔗 Отправить ссылку на оплату",
            callback_data=f"pay_send_link_{user_id}_{period_code}",
        )]
    ])
    await bot.send_message(
        admin_id,
        f"💳 <b>Кандидат подтвердил оплату</b>\n\n"
        f"👤 Пользователь: {user_info}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"⏱ Период: <b>{period_text}</b>\n"
        f"💰 Сумма: <b>{price}</b>",
        reply_markup=admin_keyboard,
        parse_mode="HTML",
    )

    # Подтверждаем кандидату
    await callback_query.message.edit_text(
        f"✅ <b>Запрос на оплату отправлен!</b>\n\n"
        f"⏱ Период: <b>{period_text}</b>\n"
        f"💰 Сумма: <b>{price}</b>\n\n"
        f"⏳ Формирую ссылку...",
        parse_mode="HTML",
    )


# ─── Оплата: шаг 3 — администратор нажимает «Отправить ссылку» ─────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("pay_send_link_"))
async def process_pay_send_link(callback_query: types.CallbackQuery, state: FSMContext):
    """Переводит администратора в режим ожидания ввода ссылки."""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    await callback_query.answer()

    # pay_send_link_123456_1h
    parts = callback_query.data.split("_", 4)
    # parts: ['pay', 'send', 'link', '123456', '1h']
    candidate_id  = int(parts[3])
    period_code   = parts[4] if len(parts) > 4 else ""

    period_labels = {"1h": "1 час", "3d": "3 дня", "30d": "30 дней"}
    period_text   = period_labels.get(period_code, period_code)

    try:
        chat = await bot.get_chat(candidate_id)
        user_info = f"@{chat.username}" if chat.username else (chat.first_name or str(candidate_id))
    except Exception:
        user_info = str(candidate_id)

    await state.set_state(PaymentLinkState.waiting_for_link)
    await state.update_data(pay_candidate_id=candidate_id, pay_period_text=period_text)

    await callback_query.message.edit_text(
        f"🔗 Отправьте ссылку на оплату для {user_info} (период: <b>{period_text}</b>).\n\n"
        f"Просто напишите или вставьте URL в следующем сообщении.\n"
        f"Для отмены нажмите /start",
        parse_mode="HTML",
    )


# ─── Оплата: шаг 4 — администратор отправляет ссылку ──────────────────────
@dp.message(PaymentLinkState.waiting_for_link)
async def process_payment_link_input(message: types.Message, state: FSMContext):
    """Получает ссылку от администратора и пересылает кандидату."""
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    candidate_id = data.get("pay_candidate_id")
    period_text  = data.get("pay_period_text", "")
    link         = message.text.strip() if message.text else ""

    if not link:
        await message.answer("❌ Сообщение пустое. Отправьте ссылку текстом.")
        return

    await state.clear()
    # Снимаем оба флага — операция завершена
    config.users_db.clear_pay_awaiting(candidate_id)
    config.users_db.clear_pay_sent(candidate_id)

    try:
        await bot.send_message(
            candidate_id,
            f"💳 <b>Ссылка на оплату</b>\n\n"
            f"⏱ Период: <b>{period_text}</b>\n\n"
            f"👇 Перейдите по ссылке для оплаты:\n{link}",
            parse_mode="HTML",
        )
        await message.answer(f"✅ Ссылка отправлена кандидату (ID: <code>{candidate_id}</code>).", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить ссылку кандидату: {e}")



class EditThresholdState(StatesGroup):
    waiting_for_value = State()


@dp.message(EditThresholdState.waiting_for_value)
async def process_threshold_value(message: types.Message, state: FSMContext):
    """Обработка нового значения порога для конкретной панели"""
    if not is_admin(message.from_user.id):
        return

    try:
        value = float(message.text.strip().replace('%', ''))
        if value < 1 or value > 99:
            await message.answer("❌ Значение должно быть от 1 до 99%")
            return

        data = await state.get_data()
        threshold_type = data.get('threshold_type')
        panel_id       = data.get('panel_id', 'panel0')

        threshold_names = {'cpu': '💻 CPU', 'ram': '🧠 RAM', 'disk': '💿 Диск'}
        config.users_db.set_panel_threshold(panel_id, f"{threshold_type}_threshold", value)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Назад к настройкам", callback_data=f"notif_panel_{panel_id}")]
        ])
        await message.answer(
            f"✅ Порог {threshold_names.get(threshold_type, threshold_type)} для <b>{panel_id}</b> обновлён: <b>{value:.0f}%</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await state.clear()

    except ValueError:
        await message.answer("❌ Неверный формат. Введите число от 1 до 99")


# FSM-состояние ожидания файла импорта apps.yaml
# ВАЖНО: класс и хендлер должны быть зарегистрированы ДО catch-all @dp.message(),
# иначе aiogram 3.x перехватит сообщение раньше FSM-фильтра.
class AppsImportState(StatesGroup):
    waiting_for_file = State()


@dp.message(AppsImportState.waiting_for_file)
async def process_apps_import(message: Message, state: FSMContext):
    """Принимает файл apps.yaml и обновляет кнопки."""
    global _apps_platforms
    if not is_admin(message.from_user.id):
        return
    if not message.document:
        await message.answer("❌ Нужно отправить файл. Для отмены нажмите /start")
        return
    if not message.document.file_name.endswith(".yaml"):
        await message.answer("❌ Файл должен быть .yaml. Для отмены нажмите /start")
        return
    try:
        file = await bot.get_file(message.document.file_id)
        buf = BytesIO()
        await bot.download_file(file.file_path, buf)
        raw = buf.getvalue()
        _apps_platforms = _save_apps_config(raw)
        await state.clear()
        platforms_info = "\n".join(
            f"  • {p['name']}: {len(p.get('apps', []))} прил."
            for p in _apps_platforms
        )
        await message.answer(
            f"✅ <b>apps.yaml успешно импортирован!</b>\n\nПлатформы:\n{platforms_info}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка импорта apps.yaml: {e}")
        await message.answer(f"❌ Ошибка при импорте: {e}\n\nФайл не сохранён.")


@dp.message(SetPriceState.waiting_for_price)
async def process_set_price_input(message: types.Message, state: FSMContext):
    """Сохраняет введённую цену и показывает обновлённый экран расценок."""
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    period_code = data.get("set_price_period", "")
    price = message.text.strip() if message.text else ""

    if not price:
        await message.answer("❌ Пустое значение. Введите цену текстом.")
        return

    await state.clear()
    config.users_db.set_admin_setting(f"pay_price_{period_code}", price)

    period_labels = {"1h": "1 час", "3d": "3 дня", "30d": "30 дней"}
    period_text = period_labels.get(period_code, period_code)

    p1h  = _get_pay_price("1h")
    p3d  = _get_pay_price("3d")
    p30d = _get_pay_price("30d")

    text = (
        f"✅ Цена для <b>{period_text}</b> обновлена: <b>{price}</b>\n\n"
        "💰 <b>Текущие расценки:</b>\n\n"
        f"⏱ 1 час:    <b>{p1h}</b>\n"
        f"📅 3 дня:   <b>{p3d}</b>\n"
        f"📅 30 дней: <b>{p30d}</b>"
    )
    await message.answer(
        text,
        reply_markup=_build_pay_prices_keyboard(),
        parse_mode="HTML",
    )


# ─── Keenetic .bat generator ─────────────────────────────────────────────────

_DOMAIN_RE = re.compile(
    r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'
)
_DNS_SERVERS = [
    "77.88.8.8",      # Yandex
    "77.88.8.1",
    "8.8.8.8",        # Google
    "8.8.4.4",
    "1.1.1.1",        # Cloudflare
    "1.0.0.1",
    "208.67.222.222", # OpenDNS
    "208.67.220.220",
]


def _clean_domain(raw: str) -> str:
    """Убирает протокол, www и путь; возвращает чистый hostname в нижнем регистре."""
    d = raw.strip()
    d = re.sub(r'^https?://', '', d)
    d = re.sub(r'^www\.', '', d)
    d = d.split('/')[0].split(':')[0].lower()
    return d


def is_valid_domain_query(text: str) -> bool:
    """Возвращает True если весь текст — один или несколько валидных доменов (по одному на строку)."""
    if not text or not text.strip():
        return False
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return False
    return all(_DOMAIN_RE.match(_clean_domain(l)) for l in lines)


async def _resolve_domain(domain: str) -> list[str]:
    """Резолвит домен через системный резолвер и список DNS-серверов; возвращает уникальные IPv4."""
    loop = asyncio.get_event_loop()
    ip_set: set[str] = set()

    # Системный резолвер
    try:
        infos = await loop.getaddrinfo(domain, None, family=socket.AF_INET)
        for info in infos:
            ip_set.add(info[4][0])
    except Exception:
        pass

    # Дополнительные DNS-серверы через getaddrinfo в executor с принудительным сервером
    # (простой fallback — используем dns через системный резолвер с разными серверами
    #  недоступен без dnspython; делаем параллельные getaddrinfo запросы)
    def _sync_resolve(host: str) -> list[str]:
        try:
            return [r[4][0] for r in socket.getaddrinfo(host, None, socket.AF_INET)]
        except Exception:
            return []

    results = await asyncio.gather(
        *[loop.run_in_executor(None, _sync_resolve, domain)],
        return_exceptions=True
    )
    for r in results:
        if isinstance(r, list):
            ip_set.update(r)

    return sorted(ip_set)


async def process_domains_to_bat(message: Message) -> bool:
    """
    Если текст сообщения является валидным доменным запросом — резолвит домены,
    генерирует .bat файл и отправляет его. Возвращает True если файл был отправлен.
    """
    text = message.text or ""
    if not is_valid_domain_query(text):
        return False

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    domains = []
    seen: set[str] = set()
    for l in lines:
        d = _clean_domain(l)
        if d and d not in seen:
            seen.add(d)
            domains.append(d)

    # Резолвим все домены параллельно
    resolved_list = await asyncio.gather(*[_resolve_domain(d) for d in domains])
    domain_ips: dict[str, list[str]] = {}
    for domain, ips in zip(domains, resolved_list):
        if ips:
            domain_ips[domain] = ips

    if not domain_ips:
        return False  # не удалось разрезолвить — молчим, стандартный ответ

    # Генерируем содержимое .bat файла
    bat_lines = []
    for domain in domains:
        for ip in domain_ips.get(domain, []):
            bat_lines.append(f"route add {ip} mask 255.255.255.255 0.0.0.0 :: rem {domain}")

    if not bat_lines:
        return False

    content = "\n".join(bat_lines) + "\n"

    # Имя файла
    def _slug(d: str) -> str:
        return d.replace('.', '_')

    if len(domains) == 1:
        filename = f"{_slug(domains[0])}_keenetic.bat"
    else:
        filename = f"{_slug(domains[0])}_and_{len(domains) - 1}_other_keenetic.bat"

    buf = BytesIO(content.encode('utf-8'))
    buf.name = filename
    await message.answer_document(buf)
    logger.info(f"Keenetic .bat sent to {message.from_user.id}: {filename} ({len(bat_lines)} routes)")
    return True


@dp.message()
async def handle_unknown(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if is_blocked_by_admin(user_id):
        await message.answer("⛔ Вы заблокированы администратором. Обратитесь к администратору.")
        return

    if is_flood_blocked(user_id):
        await message.answer("⛔ Вы временно заблокированы за флуд. Попробуйте позже.")
        return

    if not is_allowed(user_id):
        # Если запрос уже ожидает решения — полностью молчим
        if config.users_db.has_pending_request(user_id):
            return
        if check_antiflood(user_id):
            await message.answer(
                f"⚠️ Вы отправляете слишком много сообщений!\n\nЗаблокированы на {ANTIFLOOD_BLOCK_TIME // 60} минут.")
            logger.warning(f"Пользователь {user_id} заблокирован за флуд")
            return

    if message.text and message.text.startswith('/'):
        return

    # Keenetic .bat: если текст — домены и фича включена — отдаём файл без лишних сообщений
    if message.text and config.common.allow_user_dns_queries:
        if await process_domains_to_bat(message):
            return

    if is_allowed(user_id):
        await message.answer(
            "❓ Неизвестная команда.\n\nОтправьте /start для списка команд.",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "❓ Для начала работы отправьте /start",
            parse_mode="HTML"
        )


@dp.callback_query(lambda c: c.data == "server_status")
async def show_server_status(callback_query: types.CallbackQuery, state: FSMContext, is_refresh: bool = False):
    """Показать состояние сервера (только для администратора)"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    # Очищаем состояние при открытии нового окна
    await state.clear()
    
    if not is_refresh:
        await callback_query.answer()

    try:
        # Формируем сообщение
        message = "<b>⚙️ Администрирование</b>"

        # Добавляем кнопки в два ряда
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="💾 Бэкап", callback_data="create_backup")
            ],
            [
                InlineKeyboardButton(text="🔔 Уведомления", callback_data="notification_settings"),
                InlineKeyboardButton(text="👥 Пользователи", callback_data="show_users")
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="stability_chart"),
                InlineKeyboardButton(text="🖥️ Сервер", callback_data="select_panel_to_connect")
            ],
            [
                InlineKeyboardButton(text="📱 Приложения", callback_data="app_settings"),
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")
            ]
        ])
        
        # Для refresh обновляем текущее сообщение, для навигации - отправляем новое
        if is_refresh:
            try:
                await callback_query.message.edit_text(
                    message,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            except:
                # Если не удалось отредактировать (например, сообщение слишком старое),
                # отправляем новое
                await callback_query.message.answer(
                    message,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
        else:
            await bot.send_message(
                callback_query.message.chat.id,
                message,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса сервера: {e}")
        await callback_query.message.answer(f"❌ Ошибка: {str(e)}")


@dp.callback_query(lambda c: c.data == "export_json_config")
async def export_json_config(callback_query: types.CallbackQuery, state: FSMContext):
    """Экспорт JSON конфигурации подключения"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    await callback_query.answer("⏳ Формирую JSON конфиг...")
    
    try:
        import json
        
        # Формируем JSON конфигурацию с настройками подключения
        json_config = {
            "version": "1.0",
            "server": {
                "address": config.vpn.server_address,
                "port": config.vpn.server_port
            },
            "connection": {
                "transport": config.vpn.transport,
                "security": config.vpn.security
            }
        }
        
        # Добавляем специфичные настройки в зависимости от типа безопасности
        if config.vpn.security == "reality":
            json_config["reality"] = {
                "public_key": config.vpn.reality_public_key,
                "short_id": config.vpn.reality_short_id,
                "sni": config.vpn.reality_sni,
                "fingerprint": config.vpn.reality_fingerprint
            }
        elif config.vpn.security == "tls":
            json_config["tls"] = {
                "sni": config.vpn.tls_sni,
                "fingerprint": config.vpn.tls_fingerprint,
                "alpn": config.vpn.tls_alpn
            }
        
        # Добавляем настройки X-UI
        json_config["xui"] = {
            "url": config.xui.url,
            "inbound_id": config.xui.inbound_id,
            "version": config.xui.version
        }
        
        # Конвертируем в красивый JSON
        json_str = json.dumps(json_config, indent=2, ensure_ascii=False)
        
        # Отправляем как документ
        json_bytes = BytesIO(json_str.encode('utf-8'))
        
        await callback_query.message.answer_document(
            document=types.BufferedInputFile(json_bytes.getvalue(), filename="connection_config.json"),
            caption="📥 <b>JSON конфигурация подключения</b>\n\n"
                    "Этот файл содержит настройки сервера и параметры подключения.",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка экспорта JSON конфига: {e}")
        await callback_query.message.answer(f"❌ Ошибка: {str(e)}")


@dp.callback_query(lambda c: c.data == "create_backup")
async def create_backup(callback_query: types.CallbackQuery, state: FSMContext):
    """Создать полный бэкап системы — только для администратора."""
    # ── Проверка прав: только главный администратор ────────────────────────────
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    await callback_query.answer()

    # Прогресс-сообщение
    progress_msg = await callback_query.message.answer(
        "⏳ <b>Создаю полный бэкап...</b>\n\n"
        "Собираю данные со всех панелей, это займёт несколько секунд.",
        parse_mode="HTML",
    )

    try:
        from backup_manager import run_backup

        zip_path, summary = await run_backup(
            config.panel_manager, config.common
        )

        # Строим текст отчёта — разбитый по источникам
        _SYSTEM_KEYS = {"config.yaml", "bot_users.db", "apps.yaml"}
        _PANEL_SUFFIXES = (
            " x-ui.db", " inbounds", " clients",
            " xray_config", " routing", " settings", " outbound_subs",
        )
        _CASCADE_SUFFIXES = (
            " cascade system", " cascade interfaces",
            " cascade peers", " cascade settings",
        )

        sys_items     = {k: v for k, v in summary.items() if k in _SYSTEM_KEYS}
        cascade_items = {k: v for k, v in summary.items()
                         if any(k.endswith(s) for s in _CASCADE_SUFFIXES)}
        panel_items   = {k: v for k, v in summary.items()
                         if k not in sys_items and k not in cascade_items}

        # Группируем панельные ключи по алиасу сервера
        from collections import defaultdict as _dd
        _panel_groups: dict = _dd(dict)
        for key, status in panel_items.items():
            for suf in _PANEL_SUFFIXES:
                if key.endswith(suf):
                    alias_key = key[: -len(suf)]
                    short_key = suf.lstrip()
                    _panel_groups[alias_key][short_key] = status
                    break
            else:
                _panel_groups[key]["—"] = status

        # Группируем каскадные ключи по лейблу сервера
        _cascade_groups: dict = _dd(dict)
        for key, status in cascade_items.items():
            for suf in _CASCADE_SUFFIXES:
                if key.endswith(suf):
                    label_key = key[: -len(suf)]
                    short_key = suf.lstrip()
                    _cascade_groups[label_key][short_key] = status
                    break
            else:
                _cascade_groups[key]["—"] = status

        lines = ["<b>📦 Содержимое бэкапа:</b>"]

        lines.append("")
        lines.append("<b>🖥 Система</b>")
        for label, status in sys_items.items():
            lines.append(f"  • {label}: {status}")

        lines.append("")
        lines.append("<b>📊 3X-UI</b>")
        for server, items in _panel_groups.items():
            lines.append(f"   <b>{server}:</b>")
            for short_key, status in items.items():
                lines.append(f"    • {server} {short_key}: {status}")

        lines.append("")
        lines.append("<b>🔗 CASCADE</b>")
        for server, items in _cascade_groups.items():
            lines.append(f"   <b>{server}:</b>")
            for short_key, status in items.items():
                lines.append(f"    • {server} {short_key}: {status}")

        zip_size_kb = zip_path.stat().st_size // 1024 + 1
        caption = (
            f"✅ <b>Полный бэкап создан</b>\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💾 Размер: {zip_size_kb} КБ"
        )

        report = "\n".join(lines)

        # Отправляем ZIP-архив в чат администратора
        zip_bytes = zip_path.read_bytes()
        await callback_query.message.answer_document(
            types.BufferedInputFile(zip_bytes, filename=zip_path.name),
            caption=caption,
            parse_mode="HTML",
        )

        # Отправляем подробный отчёт отдельным сообщением (caption ограничен 1024 символами)
        await callback_query.message.answer(report, parse_mode="HTML")

        logger.info(
            f"[backup] отправлен администратору {callback_query.from_user.id}: "
            f"{zip_path.name} ({zip_size_kb} КБ)"
        )

    except Exception as e:
        logger.error(f"[backup] ошибка: {e}", exc_info=True)
        await callback_query.message.answer(f"❌ Ошибка при создании бэкапа: {e}")

    finally:
        # Удаляем прогресс-сообщение
        try:
            await progress_msg.delete()
        except Exception:
            pass


# ─── helper: получить текущие stats panel0 через psutil ────────────────────
def _get_local_stats() -> dict:
    try:
        import psutil
        cpu   = psutil.cpu_percent(interval=0.3)
        ram   = psutil.virtual_memory()
        disk  = psutil.disk_usage('/')
        return {'cpu': round(cpu, 1), 'ram': round(ram.percent, 1), 'disk': round(disk.percent, 1)}
    except Exception:
        return {}


# ─── helper: форматировать строку ресурса ───────────────────────────────────
def _fmt_resource(label: str, icon: str, value, threshold: float, alert_on: bool) -> str:
    if value is None:
        val_str = "<i>N/A</i>"
        status  = ""
    else:
        val_str = f"<b>{value:.1f}%</b>"
        diff = value - threshold
        if diff >= 0:
            status = f"  <b>🚨 +{diff:.1f}%</b>"
        elif diff >= -5:
            status = "  <b>⚠️ внимание</b>"
        else:
            status = "  ✅"
    alert_str = "✅ вкл" if alert_on else "❌ выкл"
    return f"{icon} {label}: {val_str}{status}\n   └ порог <code>{threshold:.0f}%</code>  ·  {alert_str}\n"


# ─── ЭКРАН 1: список серверов ───────────────────────────────────────────────
@dp.callback_query(lambda c: c.data == "notification_settings")
async def show_notification_settings(callback_query: types.CallbackQuery, state: FSMContext):
    """Экран 1 — список серверов с уведомлениями"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    await callback_query.answer()
    await _refresh_panel_states_now()

    panels = config.panel_manager.get_all_panels()
    check_interval = getattr(config.common, 'panel_check_interval', 30)

    # Получаем кеш удалённых данных если RemoteMonitor запущен
    remote_stats: dict = {}
    if REMOTE_MONITOR_AVAILABLE and _remote_monitor is not None:
        remote_stats = _remote_monitor.last_stats

    # Локальные данные (panel0)
    local_stats = _get_local_stats()

    text = "🔔 <b>Настройки уведомлений</b>\n\nВыберите сервер для настройки мониторинга ресурсов.\n"
    text += f"⏱️ Интервал: <code>{int(check_interval)}</code> сек\n"

    def _badge(v, thr):
        if v is None: return "—"
        if v >= thr: return f"🚨{v:.0f}%"
        if v >= thr - 5: return f"⚠️{v:.0f}%"
        return f"✅{v:.0f}%"

    buttons = []
    for panel_id, panel_cfg in panels.items():
        # Тот же фильтр что в "Подключить": panel0 или v3+
        is_local = (panel_id == "panel0")
        is_v3    = panel_cfg.is_v3() if hasattr(panel_cfg, 'is_v3') else False
        if not is_local and not is_v3:
            continue

        alias      = getattr(panel_cfg, 'alias', panel_id) or panel_id
        settings   = config.users_db.get_panel_notification_settings(panel_id)
        thresholds = config.users_db.get_panel_thresholds(panel_id)

        # Текущие значения ресурсов
        if panel_id == "panel0":
            stats = local_stats
        else:
            stats = remote_stats.get(panel_id, {})

        cpu_val  = stats.get('cpu')
        ram_val  = stats.get('ram')
        disk_val = stats.get('disk')

        cpu_thr  = thresholds.get('cpu_threshold', 95.0)
        ram_thr  = thresholds.get('ram_threshold', 95.0)
        disk_thr = thresholds.get('disk_threshold', 95.0)

        panel_online = get_panel_online_status(panel_id)
        status_icon  = "🟢" if panel_online else "🔴"

        resource_icons = ""
        if settings.get('cpu_alert',          False): resource_icons += "🖥"
        if settings.get('ram_alert',          False): resource_icons += "🧠"
        if settings.get('disk_alert',         False): resource_icons += "💿"
        if settings.get('availability_alert', False): resource_icons += "📡"

        location = getattr(panel_cfg, 'location_label', '') or ''
        loc_part = f" · {location}" if location else ""
        btn_text = f"{status_icon}{resource_icons} {alias}{loc_part}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"notif_panel_{panel_id}")])

    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="notification_settings"),
        InlineKeyboardButton(text="🔙 Назад",    callback_data="back_to_server_status"),
    ])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await bot.send_message(callback_query.message.chat.id, text, parse_mode="HTML", reply_markup=kb)


# ─── ЭКРАН 2: настройки конкретной панели ──────────────────────────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("notif_panel_"))
async def show_panel_notification(callback_query: types.CallbackQuery, state: FSMContext,
                                  _already_answered: bool = False,
                                  _panel_id_override: str = None):
    """Экран 2 — настройки уведомлений конкретной панели"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    if not _already_answered:
        await callback_query.answer()

    panel_id   = _panel_id_override or callback_query.data[len("notif_panel_"):]
    panel_cfg  = config.panel_manager.get_panel(panel_id)
    if not panel_cfg:
        await callback_query.answer("❌ Панель не найдена", show_alert=True)
        return

    alias   = getattr(panel_cfg, 'alias', panel_id) or panel_id
    version = getattr(panel_cfg, 'xui_version', '')
    addr    = getattr(panel_cfg, 'server_address', '') or getattr(panel_cfg, 'server_ip', '') or ''
    transport = (getattr(panel_cfg, 'transport', '') or '').upper()
    security  = (getattr(panel_cfg, 'security', '')  or '').upper()

    settings   = config.users_db.get_panel_notification_settings(panel_id)
    thresholds = config.users_db.get_panel_thresholds(panel_id)

    cpu_on   = settings.get('cpu_alert',          False)
    ram_on   = settings.get('ram_alert',          False)
    disk_on  = settings.get('disk_alert',         False)
    avail_on = settings.get('availability_alert', False)
    cpu_thr  = thresholds.get('cpu_threshold', 95.0)
    ram_thr  = thresholds.get('ram_threshold', 95.0)
    disk_thr = thresholds.get('disk_threshold', 95.0)

    # Текущие значения
    if panel_id == "panel0":
        stats = _get_local_stats()
    elif REMOTE_MONITOR_AVAILABLE and _remote_monitor is not None:
        stats = _remote_monitor.last_stats.get(panel_id, {})
    else:
        stats = {}

    cpu_val  = stats.get('cpu')
    ram_val  = stats.get('ram')
    disk_val = stats.get('disk')
    updated  = stats.get('updated_at', '—') if panel_id != "panel0" else datetime.now().strftime('%H:%M:%S')

    check_interval = getattr(config.common, 'panel_check_interval', 30)

    text  = f"🔔 <b>Уведомления · {panel_id} · {alias}</b>\n"
    text += f"📍 {addr}  v{version}  {transport}/{security}\n\n"
    text += _fmt_resource("CPU",  "💻", cpu_val,  cpu_thr,  cpu_on)
    text += "\n"
    text += _fmt_resource("RAM",  "🧠", ram_val,  ram_thr,  ram_on)
    text += "\n"
    text += _fmt_resource("Диск", "💿", disk_val, disk_thr, disk_on)
    is_remote = (panel_id != "panel0")

    if is_remote:
        text += "\n"
        avail_str = "✅ вкл" if avail_on else "❌ выкл"
        text += f"📡 Доступность: {avail_str}  <i>(алерт после 3 неудач)</i>\n"
    text += f"\n⏱️ Интервал: <code>{int(check_interval)}</code> с  ·  <code>{updated}</code>"

    rows = [
        [
            InlineKeyboardButton(text=f"💻 CPU {'✅ Вкл' if cpu_on else '❌ Выкл'}", callback_data=f"ntoggle_{panel_id}_cpu"),
            InlineKeyboardButton(text=f"✏️ {cpu_thr:.0f}%",  callback_data=f"nedit_{panel_id}_cpu"),
        ],
        [
            InlineKeyboardButton(text=f"🧠 RAM {'✅ Вкл' if ram_on else '❌ Выкл'}", callback_data=f"ntoggle_{panel_id}_ram"),
            InlineKeyboardButton(text=f"✏️ {ram_thr:.0f}%",  callback_data=f"nedit_{panel_id}_ram"),
        ],
        [
            InlineKeyboardButton(text=f"💿 Диск {'✅ Вкл' if disk_on else '❌ Выкл'}", callback_data=f"ntoggle_{panel_id}_disk"),
            InlineKeyboardButton(text=f"✏️ {disk_thr:.0f}%", callback_data=f"nedit_{panel_id}_disk"),
        ],
    ]
    if is_remote:
        rows.append([
            InlineKeyboardButton(text=f"📡 Доступность {'✅ Вкл' if avail_on else '❌ Выкл'}", callback_data=f"ntoggle_{panel_id}_availability"),
        ])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"notif_panel_{panel_id}")])
    rows.append([InlineKeyboardButton(text="🔙 К списку серверов", callback_data="notification_settings")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    try:
        await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await bot.send_message(callback_query.message.chat.id, text, parse_mode="HTML", reply_markup=kb)


# ─── Переключение алертов ───────────────────────────────────────────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("ntoggle_"))
async def toggle_panel_alert(callback_query: types.CallbackQuery, state: FSMContext):
    """ntoggle_{panel_id}_{resource}"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    parts    = callback_query.data.split("_", 2)  # ['ntoggle', panel_id, resource]
    panel_id = parts[1]
    resource = parts[2]  # cpu | ram | disk
    setting  = f"{resource}_alert"
    current  = config.users_db.get_panel_notification_setting(panel_id, setting)
    new_val  = not current
    config.users_db.set_panel_notification_setting(panel_id, setting, new_val)
    names = {'cpu': 'CPU', 'ram': 'RAM', 'disk': 'Диск', 'availability': 'Доступность'}
    await callback_query.answer(f"{'✅' if new_val else '❌'} {names.get(resource, resource)} {'вкл' if new_val else 'выкл'}")
    # Перерисовываем экран (data не мутируем — CallbackQuery frozen в pydantic v2)
    await show_panel_notification(callback_query, state, _already_answered=True, _panel_id_override=panel_id)


# ─── Редактирование порога ──────────────────────────────────────────────────
@dp.callback_query(lambda c: c.data and c.data.startswith("nedit_"))
async def start_edit_panel_threshold(callback_query: types.CallbackQuery, state: FSMContext):
    """nedit_{panel_id}_{resource}"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    parts    = callback_query.data.split("_", 2)
    panel_id = parts[1]
    resource = parts[2]
    names    = {'cpu': '💻 CPU', 'ram': '🧠 RAM', 'disk': '💿 Диск'}
    current  = config.users_db.get_panel_threshold(panel_id, f"{resource}_threshold", 95.0)

    await state.set_state(EditThresholdState.waiting_for_value)
    await state.update_data(threshold_type=resource, panel_id=panel_id)
    await callback_query.message.answer(
        f"⚙️ <b>Порог {names.get(resource, resource)}</b>  ·  <b>{panel_id}</b>\n\n"
        f"Текущее значение: <b>{current:.0f}%</b>\n\n"
        f"Введите новое значение (1–99):",
        parse_mode="HTML"
    )
    await callback_query.answer()


@dp.callback_query(lambda c: c.data == "refresh_server_status")
async def refresh_server_status(callback_query: types.CallbackQuery, state: FSMContext):
    """Обновить статус сервера"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    await callback_query.answer("🔄 Обновление...")
    # Вызываем функцию показа статуса сервера с флагом refresh
    await show_server_status(callback_query, state, is_refresh=True)


@dp.callback_query(lambda c: c.data == "back_to_server_status")
async def back_to_server_status(callback_query: types.CallbackQuery, state: FSMContext):
    """Вернуться к окну состояния сервера"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    await callback_query.answer()
    # Вызываем функцию показа статуса сервера
    await show_server_status(callback_query, state)


@dp.callback_query(lambda c: c.data == "show_users")
async def show_users_list(callback_query: types.CallbackQuery, state: FSMContext, is_refresh: bool = False):
    """Показать список пользователей (только для администратора)"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    await state.clear()

    if not is_refresh:
        await callback_query.answer("⏳ Обновляю список...")

    try:
        users = config.users_db.list_users()
        main_admin = config.users_db.get_main_admin()

        try:
            admin_chat = await bot.get_chat(main_admin)
            admin_name = f"@{admin_chat.username}" if admin_chat.username else str(main_admin)
        except:
            admin_name = str(main_admin)

        text = f"👑 <b>Администратор:</b> {admin_name}\n\n"
        buttons = []

        if users:
            text += "<b>📋 Пользователи:</b>\n"
            for user_id, username, added_at in users:
                is_blocked = config.users_db.is_blocked_by_admin(user_id)
                blocked_status = "🔒 Заблокирован" if is_blocked else "✅ Активен"
                display_name = f"@{username}" if username else f"ID: {user_id}"
                text += f"• {display_name} — {blocked_status} — добавлен {added_at[:10]}\n"
                icon = "🔒" if is_blocked else "👤"
                buttons.append([InlineKeyboardButton(
                    text=f"{icon} {display_name}",
                    callback_data=f"user_card_{user_id}"
                )])
        else:
            text += "Нет добавленных пользователей."

        pending = config.users_db.list_pending_requests()
        if pending:
            buttons.append([InlineKeyboardButton(
                text=f"📋 Запросы на доступ ({len(pending)})",
                callback_data="show_pending_requests"
            )])

        buttons.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_users"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_server_status")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        if is_refresh:
            try:
                await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    logger.error(f"Не удалось отредактировать сообщение пользователей: {e}")
                    await bot.send_message(callback_query.message.chat.id, text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_message(callback_query.message.chat.id, text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка получения списка пользователей: {e}")
        await callback_query.message.answer(f"❌ Ошибка: {str(e)}")


@dp.callback_query(lambda c: c.data == "refresh_users")
async def refresh_users(callback_query: types.CallbackQuery, state: FSMContext):
    """Обновить список пользователей"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    await callback_query.answer("🔄 Обновление...")
    await show_users_list(callback_query, state, is_refresh=True)


@dp.callback_query(lambda c: c.data == "show_pending_requests")
async def show_pending_requests(callback_query: types.CallbackQuery):
    """Показать список ожидающих запросов на доступ"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    await callback_query.answer()

    pending = config.users_db.list_pending_requests()

    if not pending:
        await bot.send_message(
            callback_query.message.chat.id,
            "✅ Нет ожидающих запросов на доступ.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Установить расценки", callback_data="show_pay_prices")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="show_users")],
            ])
        )
        return

    buttons = []
    for uid, requested_at in pending:
        try:
            chat = await bot.get_chat(uid)
            label = f"@{chat.username}" if chat.username else chat.first_name or str(uid)
        except:
            label = str(uid)
        # Иконки: 💳 ожидает ссылку → 📨 запрос отправлен → 🕐 обычный
        if config.users_db.get_pay_awaiting(uid):
            icon = "💳"
        elif config.users_db.get_pay_sent(uid):
            icon = "📨"
        else:
            icon = "🕐"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=f"pending_card_{uid}"
        )])

    buttons.append([InlineKeyboardButton(text="💰 Установить расценки", callback_data="show_pay_prices")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="show_users")])

    await bot.send_message(
        callback_query.message.chat.id,
        f"📋 <b>Запросы на доступ ({len(pending)})</b>\n\nВыберите пользователя:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("pending_card_"))
async def show_pending_card(callback_query: types.CallbackQuery):
    """Показать карточку ожидающего запроса с кнопками решения"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    user_id = int(callback_query.data.split("_", 2)[2])

    # Если запрос уже обработан — сообщить и вернуть в список
    if not config.users_db.has_pending_request(user_id):
        await callback_query.answer("ℹ️ Запрос уже обработан", show_alert=True)
        await show_pending_requests(callback_query)
        return

    await callback_query.answer()

    try:
        chat = await bot.get_chat(user_id)
        username = chat.username
        first_name = chat.first_name or ""
        last_name = chat.last_name or ""
        user_info = f"@{username}" if username else first_name
        user_full_name = f"{first_name} {last_name}".strip()
    except:
        username = None
        user_info = str(user_id)
        user_full_name = str(user_id)

    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Разрешить", callback_data=f"approve_{user_id}")],
        [InlineKeyboardButton(text="🕐 Ключ на 1 час", callback_data=f"temp_1h_{user_id}"),
         InlineKeyboardButton(text="📅 Ключ на 1 день", callback_data=f"temp_1d_{user_id}")],
        [InlineKeyboardButton(text="📅 Ключ на 3 дня", callback_data=f"temp_3d_{user_id}"),
         InlineKeyboardButton(text="📅 Ключ на 7 дней", callback_data=f"temp_7d_{user_id}")],
        [InlineKeyboardButton(text="📅 Ключ на 30 дней", callback_data=f"temp_30d_{user_id}")],
        [InlineKeyboardButton(text="💳 Отправить на оплату", callback_data=f"pay_req_{user_id}")],
        [InlineKeyboardButton(text="🔄 Сброс статуса", callback_data=f"reset_pending_{user_id}")],
        [InlineKeyboardButton(text="❌ Заблокировать", callback_data=f"deny_{user_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="show_pending_requests")]
    ])

    await bot.send_message(
        callback_query.message.chat.id,
        f"🆕 <b>Запрос на доступ</b>\n\n"
        f"👤 Пользователь: {user_info}\n"
        f"📝 Имя: {user_full_name}\n"
        f"🆔 ID: <code>{user_id}</code>",
        reply_markup=admin_keyboard,
        parse_mode="HTML"
    )



# ─── Расценки: экран с текущими ценами ────────────────────────────────────
def _build_pay_prices_keyboard() -> InlineKeyboardMarkup:
    """Строит клавиатуру экрана расценок с текущими ценами."""
    p1h  = _get_pay_price("1h")
    p3d  = _get_pay_price("3d")
    p30d = _get_pay_price("30d")
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"⏱ 1 час: {p1h}",    callback_data="noop"),
            InlineKeyboardButton(text="✏️",                  callback_data="set_price_1h"),
        ],
        [
            InlineKeyboardButton(text=f"📅 3 дня: {p3d}",    callback_data="noop"),
            InlineKeyboardButton(text="✏️",                  callback_data="set_price_3d"),
        ],
        [
            InlineKeyboardButton(text=f"📅 30 дней: {p30d}", callback_data="noop"),
            InlineKeyboardButton(text="✏️",                  callback_data="set_price_30d"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="show_pending_requests")],
    ])


@dp.callback_query(lambda c: c.data == "show_pay_prices")
async def show_pay_prices(callback_query: types.CallbackQuery):
    """Экран управления расценками на периоды доступа."""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    await callback_query.answer()

    p1h  = _get_pay_price("1h")
    p3d  = _get_pay_price("3d")
    p30d = _get_pay_price("30d")

    text = (
        "💰 <b>Расценки на периоды доступа</b>\n\n"
        f"⏱ 1 час:    <b>{p1h}</b>\n"
        f"📅 3 дня:   <b>{p3d}</b>\n"
        f"📅 30 дней: <b>{p30d}</b>\n\n"
        "Нажмите ✏️ рядом с периодом, чтобы изменить цену."
    )
    await bot.send_message(
        callback_query.message.chat.id,
        text,
        reply_markup=_build_pay_prices_keyboard(),
        parse_mode="HTML",
    )


@dp.callback_query(lambda c: c.data == "noop")
async def noop_callback(callback_query: types.CallbackQuery):
    """Пустая кнопка-заглушка (текстовые ячейки в строке расценок)."""
    await callback_query.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("set_price_"))
async def process_set_price(callback_query: types.CallbackQuery, state: FSMContext):
    """Запрашивает у администратора новую цену для выбранного периода."""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    await callback_query.answer()

    period_code = callback_query.data[len("set_price_"):]   # 1h | 3d | 30d
    period_labels = {"1h": "1 час", "3d": "3 дня", "30d": "30 дней"}
    period_text = period_labels.get(period_code, period_code)

    await state.set_state(SetPriceState.waiting_for_price)
    await state.update_data(set_price_period=period_code)

    await callback_query.message.edit_text(
        f"✏️ Введите новую цену для периода <b>{period_text}</b>.\n\n"
        f"Например: <code>150 руб</code> или <code>1.5$</code>\n"
        f"Для отмены нажмите /start",
        parse_mode="HTML",
    )


@dp.callback_query(lambda c: c.data and c.data.startswith('user_card_'))
async def show_user_card(callback_query: types.CallbackQuery, state: FSMContext):
    """Показать карточку пользователя с кнопками управления"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    target_user_id = int(callback_query.data.split('_')[2])
    await callback_query.answer()

    try:
        users = config.users_db.list_users()
        user_row = next((r for r in users if r[0] == target_user_id), None)

        username = user_row[1] if user_row else None
        added_at = user_row[2][:10] if user_row else "—"
        is_blocked = config.users_db.is_blocked_by_admin(target_user_id)
        is_main_admin = (target_user_id == config.users_db.get_main_admin())

        display_name = f"@{username}" if username else f"ID: {target_user_id}"
        status_text = "🔒 Заблокирован" if is_blocked else "✅ Активен"

        text = (
            f"👤 <b>Пользователь:</b> {display_name}\n"
            f"🆔 ID: <code>{target_user_id}</code>\n"
            f"📅 Добавлен: {added_at}\n"
            f"Статус: {status_text}"
        )

        buttons = []
        if not is_main_admin:
            if is_blocked:
                buttons.append([InlineKeyboardButton(text="🔓 Разблокировать", callback_data=f"dounblock_{target_user_id}")])
            else:
                buttons.append([InlineKeyboardButton(text="🔒 Заблокировать", callback_data=f"doblock_{target_user_id}")])
            buttons.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"doremove_{target_user_id}")])

        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="show_users")])

        await callback_query.message.edit_text(
            text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )

    except Exception as e:
        logger.error(f"Ошибка показа карточки пользователя: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data == "back_to_start")
async def back_to_start_menu(callback_query: types.CallbackQuery, state: FSMContext):
    """Вернуться в главное меню /start — всегда отправляет новое сообщение."""
    await _show_main_menu(callback_query, state, edit=False)


async def _refresh_panel_states_now():
    """Выполняет живую проверку всех панелей и Cascade-серверов, обновляет кэши."""
    global _cascade_online
    if _panel_monitor is not None:
        try:
            panels = config.panel_manager.get_all_panels()
            for panel_id, panel_cfg in panels.items():
                try:
                    is_available = await config.panel_manager.check_panel_status(panel_cfg)
                except Exception:
                    is_available = False
                state = _panel_monitor.panel_states.get(panel_id)
                if state is not None:
                    state.is_available = is_available
        except Exception as e:
            logger.warning(f"Ошибка обновления статусов панелей: {e}")
    # Проверяем доступность Cascade-серверов
    try:
        cascade_servers = config.get_cascade_servers()
        for srv in cascade_servers:
            try:
                async with CascadeClient(
                    url=srv.url,
                    api_key=srv.api_key,
                    username=srv.username,
                    password=srv.password,
                    label=srv.label,
                ) as client:
                    _cascade_online[srv.cascade_id] = await client.ping()
            except Exception:
                _cascade_online[srv.cascade_id] = False
    except Exception as e:
        logger.warning(f"Ошибка обновления статусов Cascade: {e}")


@dp.callback_query(lambda c: c.data == "refresh_main_menu")
async def refresh_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    """Обновить главное меню на месте — редактирует текущее сообщение."""
    await _refresh_panel_states_now()
    await _show_main_menu(callback_query, state, edit=True)


async def _show_main_menu(callback_query: types.CallbackQuery, state: FSMContext, edit: bool):
    """Внутренняя функция показа главного меню. edit=True — редактировать, False — новое."""
    user_id = callback_query.from_user.id
    username = callback_query.from_user.username
    first_name = callback_query.from_user.first_name

    await state.clear()
    await callback_query.answer()

    # Обновляем конфигурацию из текущей панели
    try:
        panel_manager = config.panel_manager
        current_panel_id = panel_manager.get_current_panel_id()
        if current_panel_id:
            panel_config = panel_manager.get_panel(current_panel_id)
            if panel_config:
                if hasattr(panel_config, 'transport'):
                    config.vpn.transport = panel_config.transport
                if hasattr(panel_config, 'security'):
                    config.vpn.security = panel_config.security
                logger.info(f"🔄 Конфигурация обновлена из панели {current_panel_id}")
    except Exception as e:
        logger.error(f"Ошибка обновления конфигурации: {e}")

    if not is_allowed(user_id):
        await callback_query.message.edit_text("⛔ Отказано в доступе.")
        return

    if is_admin(user_id):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔑 Создать ключ", callback_data="create_key"),
                InlineKeyboardButton(text="📡 Статус", callback_data="status_servers"),
            ],
            [
                InlineKeyboardButton(text="🗝 Мои ключи", callback_data="cmd_myclients"),
                InlineKeyboardButton(text="📱 Скачать приложение", callback_data="app_download"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Администрирование", callback_data="server_status"),
            ]
        ])
        text = (
            f"👑 Администратор\n\n"
            f""
        )
        if edit:
            await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_message(callback_query.message.chat.id, text, parse_mode="HTML", reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔑 Создать ключ", callback_data="create_key"),
                InlineKeyboardButton(text="📡 Статус", callback_data="status_servers"),
            ],
            [
                InlineKeyboardButton(text="🗝 Мои ключи", callback_data="cmd_myclients"),
                InlineKeyboardButton(text="📱 Скачать приложение", callback_data="app_download"),
            ]
        ])
        text = (
            f"👤 <b>Пользователь:</b> {username or first_name}\n\n"
            f"📲 <b>Как начать:</b>\n"
            f"1. Скачайте приложение\n"
            f"2. Создайте ключ или выберите его из <b>Мои ключи</b>\n"
            f"3. Добавьте ключ в приложении через кнопку <b>+</b> или <b>Добавить</b>"
        )
        if edit:
            await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_message(callback_query.message.chat.id, text, parse_mode="HTML", reply_markup=keyboard)
@dp.callback_query(lambda c: c.data == "status_servers")
async def show_status_servers(callback_query: types.CallbackQuery):
    """Кнопка 'Статус' — живая проверка и отображение доступности серверов."""
    if not is_allowed(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await callback_query.answer()
    await callback_query.message.answer("⏳ Проверяю доступность серверов...", parse_mode="HTML")
    await _refresh_panel_states_now()

    user_id = callback_query.from_user.id
    if is_admin(user_id):
        panels_block = _build_panels_block_admin()
        cascade_block = _build_cascade_block_admin()
        xui_header = "3X-UI:\n" if panels_block else ""
        cascade_header = "CASCADE:\n" if cascade_block else ""
        text = (
            f"{xui_header}{panels_block}"
            f"{cascade_header}{cascade_block}"
        )
    else:
        panels_block = _build_panels_block()
        text = panels_block

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")],
    ])
    await callback_query.message.answer(
        text.strip() or "ℹ️ Нет данных о серверах.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@dp.callback_query(lambda c: c.data == "cmd_new")
async def callback_cmd_new(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Создать ключ'"""
    user_id = callback_query.from_user.id
    if not is_allowed(user_id):
        if config.users_db.has_pending_request(user_id):
            await callback_query.answer()
        else:
            await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    if is_blocked_by_admin(user_id):
        await callback_query.answer("⛔ Вы заблокированы администратором", show_alert=True)
        return
    await callback_query.answer()
    await state.set_state(NewClientState.waiting_for_panel)
    await _refresh_panel_states_now()
    available = get_available_panels()
    online_available = [(pid, pcfg) for pid, pcfg in available if get_panel_online_status(pid)]
    if len(online_available) == 0:
        # Все панели недоступны
        await bot.send_message(
            callback_query.message.chat.id,
            "🔴 <b>Все серверы сейчас недоступны.</b>\n\nСоздание ключей временно невозможно. Попробуйте позже.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ])
        )
        await state.clear()
        return
    if len(online_available) == 1:
        panel_id, panel_cfg = online_available[0]
        await state.update_data(selected_panel_id=panel_id)
        await _send_comment_choice(callback_query.message.chat.id, state, "new", f"📡 Панель: <b>{panel_cfg.alias}</b>")
        return
    await send_panel_select(
        callback_query.message.chat.id,
        "🔑 <b>Создание бессрочного ключа</b>\n\nВыберите сервер:",
        "back_to_start", "new"
    )


@dp.callback_query(lambda c: c.data == "cmd_tempkey")
async def callback_cmd_tempkey(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Временный ключ'"""
    user_id = callback_query.from_user.id
    if not is_allowed(user_id):
        if config.users_db.has_pending_request(user_id):
            await callback_query.answer()
        else:
            await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    if is_blocked_by_admin(user_id):
        await callback_query.answer("⛔ Вы заблокированы администратором", show_alert=True)
        return
    await callback_query.answer()
    await state.set_state(TempKeyState.waiting_for_panel)
    await _refresh_panel_states_now()
    available = get_available_panels()
    online_available = [(pid, pcfg) for pid, pcfg in available if get_panel_online_status(pid)]
    if len(online_available) == 0:
        # Все панели недоступны
        await bot.send_message(
            callback_query.message.chat.id,
            "🔴 <b>Все серверы сейчас недоступны.</b>\n\nСоздание ключей временно невозможно. Попробуйте позже.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ])
        )
        await state.clear()
        return
    if len(online_available) == 1:
        panel_id, panel_cfg = online_available[0]
        await state.update_data(selected_panel_id=panel_id)
        await state.set_state(TempKeyState.waiting_for_duration)
        await _send_duration_select(callback_query.message.chat.id, panel_cfg.alias)
        return
    await send_panel_select(
        callback_query.message.chat.id,
        "⏰ <b>Создание временного ключа</b>\n\nВыберите сервер:",
        "back_to_start", "temp"
    )

@dp.callback_query(lambda c: c.data == "cmd_myclients")
async def callback_cmd_myclients(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Мои ключи'"""
    user_id = callback_query.from_user.id
    
    # Очищаем состояние при открытии нового окна
    await state.clear()
    
    # Очищаем кеш "Все ключи" при переходе в "Мои ключи"
    if user_id in allclients_cache:
        del allclients_cache[user_id]
    
    # Проверка доступа
    if not is_allowed(user_id):
        if config.users_db.has_pending_request(user_id):
            await callback_query.answer()
        else:
            await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    if is_blocked_by_admin(user_id):
        await callback_query.answer("⛔ Вы заблокированы администратором", show_alert=True)
        return
    
    await callback_query.answer()

    # Живая проверка перед сбором ключей
    await _refresh_panel_states_now()

    # Собираем ключи со всех доступных панелей
    try:
        username = callback_query.from_user.username
        if not username:
            await bot.send_message(
                callback_query.message.chat.id,
                "❌ У вас не установлен username в Telegram.\n\nУстановите username в настройках Telegram для использования бота.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ])
            )
            return

        all_clients = []
        offline_panels = []
        available = get_available_panels()
        for panel_id, panel_cfg in available:
            if not get_panel_online_status(panel_id):
                offline_panels.append(panel_cfg.alias or panel_id)
                continue
            try:
                async with make_panel_client(panel_id) as panel_client:
                    panel_clients = await panel_client.get_user_clients_by_username(username)
                    loc = getattr(panel_cfg, 'location_label', '')
                    for c in panel_clients:
                        c['_panel_id'] = panel_id
                        c['_panel_alias'] = panel_cfg.alias
                        c['_panel_location'] = loc
                    all_clients.extend(panel_clients)
            except Exception as pe:
                offline_panels.append(panel_cfg.alias or panel_id)
                logger.warning(f"Не удалось получить ключи с панели {panel_id}: {pe}")

        # Статистика и панели
        active_count = sum(1 for c in all_clients if c['status'] == 'active')
        inactive_count = sum(1 for c in all_clients if c['status'] == 'inactive')
        expired_count = sum(1 for c in all_clients if c['status'] == 'expired')
        # panel_names с локацией: "rus [Москва], yun [Германия]"
        seen_panels = {}
        for c in all_clients:
            pid = c.get('_panel_id', '')
            if pid not in seen_panels:
                alias = c.get('_panel_alias', '')
                loc = c.get('_panel_location', '')
                seen_panels[pid] = f"{alias} [{loc}]" if loc else alias
        panel_names = ", ".join(seen_panels.values()) if seen_panels else "—"
        offline_warn = (
            f"\n⚠️ <i>Недоступны: {', '.join(offline_panels)} — ключи не загружены</i>"
            if offline_panels else ""
        )

        if not all_clients:
            text = f"🔑 <b>Мои ключи (0)</b>\n\n"
            text += f"✅ Активных: 0\n⏸️ Неактивных: 0\n⏰ Просроченных: 0\n\n"
            text += "📭 <i>У вас пока нет ключей.</i>"
            text += offline_warn
            await bot.send_message(
                callback_query.message.chat.id, text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_myclients")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ]),
                parse_mode="HTML"
            )
            return

        buttons = []
        for client in all_clients:
            comment = client.get('comment', '')
            status = client['status']
            panel_alias = client.get('_panel_alias', '')
            panel_loc = client.get('_panel_location', '')
            pid = client.get('_panel_id', '')
            display_text = (comment.replace('Временный ', '') if comment else client['email'])[:20]
            icon = "✅" if status == 'active' else ("⏸️" if status == 'inactive' else "⏰")
            panel_tag = f"{panel_alias} [{panel_loc}]" if panel_loc else panel_alias
            label = f"{icon} {display_text} · {panel_tag}" if panel_tag else f"{icon} {display_text}"
            cb = f"showqr_{pid}:{client['uuid']}" if pid else f"showqr_{client['uuid']}"
            buttons.append([InlineKeyboardButton(text=label, callback_data=cb)])

        buttons.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_myclients"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")
        ])

        text = f"🔑 <b>Мои ключи ({len(all_clients)})</b>\n"
        text += f"📡 {panel_names}\n\n"
        text += f"✅ Активных: {active_count}\n"
        text += f"⏸️ Неактивных: {inactive_count}\n"
        text += f"⏰ Просроченных: {expired_count}\n"
        text += offline_warn + "\n"
        text += "\nВыберите ключ для просмотра:"

        await bot.send_message(
            callback_query.message.chat.id, text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения списка клиентов: {e}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
        ])
        # Всегда отправляем новое сообщение для навигации
        await bot.send_message(
            callback_query.message.chat.id,
            f"❌ Ошибка: {str(e)}",
            reply_markup=keyboard
        )

@dp.callback_query(lambda c: c.data == "refresh_myclients")
async def refresh_myclients(callback_query: types.CallbackQuery, state: FSMContext):
    """Обновить список моих ключей"""
    user_id = callback_query.from_user.id
    
    # Проверка доступа
    if not is_allowed(user_id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    if is_blocked_by_admin(user_id):
        await callback_query.answer("⛔ Вы заблокированы администратором", show_alert=True)
        return
    
    # Очищаем состояние
    await state.clear()
    
    # Очищаем кеш "Все ключи" при обновлении "Мои ключи"
    if user_id in allclients_cache:
        del allclients_cache[user_id]
    
    # Живая проверка перед сбором ключей
    await _refresh_panel_states_now()

    try:
        username = callback_query.from_user.username
        if not username:
            await callback_query.answer("❌ У вас не установлен username", show_alert=True)
            return

        all_clients = []
        offline_panels = []
        available = get_available_panels()
        for panel_id, panel_cfg in available:
            if not get_panel_online_status(panel_id):
                offline_panels.append(panel_cfg.alias or panel_id)
                continue
            try:
                async with make_panel_client(panel_id) as panel_client:
                    panel_clients = await panel_client.get_user_clients_by_username(username)
                    loc = getattr(panel_cfg, 'location_label', '')
                    for c in panel_clients:
                        c['_panel_id'] = panel_id
                        c['_panel_alias'] = panel_cfg.alias
                        c['_panel_location'] = loc
                    all_clients.extend(panel_clients)
            except Exception as pe:
                offline_panels.append(panel_cfg.alias or panel_id)
                logger.warning(f"Не удалось получить ключи с панели {panel_id}: {pe}")

        active_count = sum(1 for c in all_clients if c['status'] == 'active')
        inactive_count = sum(1 for c in all_clients if c['status'] == 'inactive')
        expired_count = sum(1 for c in all_clients if c['status'] == 'expired')
        offline_warn = (
            f"\n⚠️ <i>Недоступны: {', '.join(offline_panels)} — ключи не загружены</i>"
            if offline_panels else ""
        )
        seen_panels = {}
        for c in all_clients:
            pid = c.get('_panel_id', '')
            if pid not in seen_panels:
                alias = c.get('_panel_alias', '')
                loc = c.get('_panel_location', '')
                seen_panels[pid] = f"{alias} [{loc}]" if loc else alias
        panel_names = ", ".join(seen_panels.values()) if seen_panels else "—"

        if not all_clients:
            text = f"🔑 <b>Мои ключи (0)</b>\n\n"
            text += f"✅ Активных: 0\n⏸️ Неактивных: 0\n⏰ Просроченных: 0\n\n"
            text += "📭 <i>У вас пока нет ключей.</i>"
            text += offline_warn
            await callback_query.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_myclients")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
                ]),
                parse_mode="HTML"
            )
            await callback_query.answer("✅ Обновлено", show_alert=False)
            return

        buttons = []
        for client in all_clients:
            comment = client.get('comment', '')
            status = client['status']
            panel_alias = client.get('_panel_alias', '')
            panel_loc = client.get('_panel_location', '')
            pid = client.get('_panel_id', '')
            display_text = (comment.replace('Временный ', '') if comment else client['email'])[:20]
            icon = "✅" if status == 'active' else ("⏸️" if status == 'inactive' else "⏰")
            panel_tag = f"{panel_alias} [{panel_loc}]" if panel_loc else panel_alias
            label = f"{icon} {display_text} · {panel_tag}" if panel_tag else f"{icon} {display_text}"
            cb = f"showqr_{pid}:{client['uuid']}" if pid else f"showqr_{client['uuid']}"
            buttons.append([InlineKeyboardButton(text=label, callback_data=cb)])

        buttons.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_myclients"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")
        ])

        text = f"🔑 <b>Мои ключи ({len(all_clients)})</b>\n"
        text += f"📡 {panel_names}\n\n"
        text += f"✅ Активных: {active_count}\n"
        text += f"⏸️ Неактивных: {inactive_count}\n"
        text += f"⏰ Просроченных: {expired_count}\n"
        text += offline_warn + "\n"
        text += "\nВыберите ключ для просмотра:"

        await callback_query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
        await callback_query.answer("✅ Обновлено", show_alert=False)
        
    except Exception as e:
        # Проверяем, не является ли ошибка "message is not modified"
        if "message is not modified" in str(e):
            await callback_query.answer("✅ Данные актуальны", show_alert=False)
        else:
            logger.error(f"Ошибка обновления моих ключей: {e}")
            await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data == "cmd_allclients")
async def callback_cmd_allclients(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Все ключи' (только для админа)"""
    user_id = callback_query.from_user.id
    
    # Очищаем кеш при переходе в "Все ключи" для принудительного обновления
    if user_id in allclients_cache:
        del allclients_cache[user_id]
    
    # Очищаем состояние при открытии нового окна
    await state.clear()
    
    # Проверка прав администратора
    if not is_admin(user_id):
        await callback_query.answer("⛔ Доступ запрещен. Только для администратора.", show_alert=True)
        return
    
    # Перенаправляем на back_to_allclients для единого отображения
    await back_to_allclients(callback_query)


@dp.callback_query(lambda c: c.data and c.data.startswith('doblock_'))
async def process_doblock_user(callback_query: types.CallbackQuery, state: FSMContext):
    """Заблокировать пользователя"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    user_id = int(callback_query.data.split('_')[1])

    try:
        if config.users_db.block_user(user_id, callback_query.from_user.id):
            try:
                await bot.send_message(user_id, "⛔ Вы заблокированы администратором.")
            except:
                pass
            ok_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ OK", callback_data="show_users")]
            ])
            await callback_query.answer()
            await callback_query.message.edit_text(
                "🔒 Пользователь заблокирован.", reply_markup=ok_keyboard
            )
        else:
            await callback_query.answer("❌ Ошибка при блокировке!", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка блокировки пользователя: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith('dounblock_'))
async def process_dounblock_user(callback_query: types.CallbackQuery, state: FSMContext):
    """Разблокировать пользователя"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    user_id = int(callback_query.data.split('_')[1])

    try:
        if config.users_db.unblock_user(user_id):
            try:
                await bot.send_message(user_id, "✅ Вы разблокированы администратором.")
            except:
                pass
            ok_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ OK", callback_data="show_users")]
            ])
            await callback_query.answer()
            await callback_query.message.edit_text(
                "🔓 Пользователь разблокирован.", reply_markup=ok_keyboard
            )
        else:
            await callback_query.answer("❌ Ошибка при разблокировке!", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка разблокировки пользователя: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith('doremove_'))
async def process_doremove_user(callback_query: types.CallbackQuery, state: FSMContext):
    """Удалить пользователя"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    user_id = int(callback_query.data.split('_')[1])

    if user_id == config.users_db.get_main_admin():
        await callback_query.answer("❌ Нельзя удалить главного администратора!", show_alert=True)
        return

    try:
        if config.users_db.remove_user(user_id):
            try:
                await bot.send_message(user_id, "⛔ Ваш доступ отозван.")
            except:
                pass
            ok_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ OK", callback_data="show_users")]
            ])
            await callback_query.answer()
            await callback_query.message.edit_text(
                "🗑 Пользователь удалён.", reply_markup=ok_keyboard
            )
        else:
            await callback_query.answer("❌ Ошибка при удалении!", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка удаления пользователя: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


# ============================================
# Управление панелями 3x-ui
# ============================================

@dp.callback_query(lambda c: c.data == "show_panels")
async def show_panels_list(callback_query: types.CallbackQuery, state: FSMContext, is_refresh: bool = False):
    """Показать список всех панелей с их статусами"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.message.answer("⛔ Доступ запрещен")
        return
    
    try:
        panel_manager = config.panel_manager
        panels = panel_manager.get_all_panels()
        current_panel_id = panel_manager.get_current_panel_id()
        
        # Диагностическая информация
        logger.info(f"📊 Диагностика панелей:")
        logger.info(f"  - Путь к файлу: {panel_manager.config_path.absolute()}")
        logger.info(f"  - Файл существует: {panel_manager.config_path.exists()}")
        logger.info(f"  - Количество панелей: {len(panels)}")
        logger.info(f"  - Текущая панель: {current_panel_id}")
        logger.info(f"  - Список панелей: {list(panels.keys())}")
        
        if not panels:
            # Дополнительная диагностика
            import os
            cwd = os.getcwd()
            files_in_dir = os.listdir(cwd) if os.path.exists(cwd) else []
            
            diagnostic_text = (
                "📋 <b>Управление панелями</b>\n\n"
                "❌ Панели не настроены.\n\n"
                f"🔍 <b>Диагностика:</b>\n"
                f"• Рабочая директория: <code>{cwd}</code>\n"
                f"• Ищем файл: <code>{panel_manager.config_path.name}</code>\n"
                f"• Полный путь: <code>{panel_manager.config_path.absolute()}</code>\n"
                f"• Файл существует: {'✅ Да' if panel_manager.config_path.exists() else '❌ Нет'}\n\n"
            )
            
            if 'config.yaml' in files_in_dir:
                diagnostic_text += "✅ Файл <code>config.yaml</code> найден в директории\n"
                diagnostic_text += "⚠️ Возможно, ошибка в формате YAML или файл пустой\n\n"
            else:
                diagnostic_text += "❌ Файл <code>config.yaml</code> не найден\n\n"
            
            diagnostic_text += (
                "📝 <b>Решение:</b>\n"
                "1. Скопируйте <code>config.yaml.example</code> в <code>config.yaml</code>\n"
                "2. Настройте параметры панелей в секции <code>panels</code>\n"
                "3. Перезапустите бота\n\n"
                f"💡 Файлы в директории: {len(files_in_dir)}"
            )
            
            # Всегда отправляем новое сообщение для кнопки "Панели" из главного меню
            # Для refresh обновляем текущее сообщение
            if is_refresh:
                await callback_query.message.edit_text(
                    diagnostic_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_server_status")]
                    ])
                )
            else:
                await bot.send_message(
                    callback_query.message.chat.id,
                    diagnostic_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_server_status")]
                    ])
                )
            return
        
        # Проверяем статусы всех панелей
        # Для refresh обновляем текущее сообщение, для нового окна показываем прогресс
        progress_msg = None
        if is_refresh:
            await callback_query.message.edit_text(
                "🔄 Проверка статусов панелей...",
                parse_mode="HTML"
            )
        else:
            # Отправляем новое сообщение с прогрессом
            progress_msg = await bot.send_message(
                callback_query.message.chat.id,
                "🔄 Проверка статусов панелей...",
                parse_mode="HTML"
            )
        
        statuses = await panel_manager.check_all_panels_status()
        
        # Формируем текст со списком панелей
        text = "🔧 <b>Панели 3xui</b>\n\n"

        for panel_id, panel_config in panels.items():
            alias = getattr(panel_config, 'alias', panel_id)
            version = getattr(panel_config, 'xui_version', 'N/A')
            is_current = panel_id == current_panel_id
            is_online = statuses.get(panel_id, False)

            panel_icon = "✅" if is_current else "⏸️"
            online_icon = "🟢 Онлайн" if is_online else "🔴 Оффлайн"

            location = getattr(panel_config, 'location_label', '')
            location_str = f"  |  📍 {location}" if location else ""

            transport = (getattr(panel_config, 'transport', '') or '—').upper()
            security = (getattr(panel_config, 'security', '') or '—').upper()
            url = panel_config.xui_url or ''
            panel_id_str = (
                f"<a href=\"{url}\">{panel_id}</a>" if url else f"<code>{panel_id}</code>"
            )

            text += f"{panel_icon} <b>{alias}</b>  <code>v{version}</code>{location_str}\n"
            text += f"   {online_icon}  |  🆔 {panel_id_str}\n"
            text += f"   🔌 <code>{transport}</code> · <code>{security}</code>\n"
            text += "\n"
        
        # Кнопки управления
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_panels"),
                InlineKeyboardButton(text="🖥️ Сервер", callback_data="select_panel_to_connect")
            ],
            [
                InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_server_status")
            ]
        ])
        
        # Для refresh обновляем сообщение, для нового окна редактируем прогресс-сообщение
        if is_refresh:
            await callback_query.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            if progress_msg:
                await progress_msg.edit_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
        
    except Exception as e:
        logger.error(f"Ошибка отображения панелей: {e}")
        await callback_query.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
            ])
        )


@dp.callback_query(lambda c: c.data == "refresh_panels")
async def refresh_panels_status(callback_query: types.CallbackQuery, state: FSMContext):
    """Обновить статусы всех панелей"""
    await callback_query.answer("🔄 Обновление конфигурации...")
    
    # Перезагружаем config.yaml
    config.reload_config()
    
    # Показываем обновленный список панелей (с флагом refresh)
    await show_panels_list(callback_query, state, is_refresh=True)



def _load_fonts(sizes: list) -> list:
    """
    Загружает TTF-шрифт с поддержкой кириллицы.
    Перебирает несколько известных путей; при неудаче возвращает default.
    """
    from PIL import ImageFont
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    candidates_bold = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]
    import os
    result = []
    for i, sz in enumerate(sizes):
        bold = (i == 0)
        paths = candidates_bold if bold else candidates
        loaded = None
        for p in paths:
            if os.path.exists(p):
                try:
                    loaded = ImageFont.truetype(p, sz)
                    break
                except Exception:
                    continue
        result.append(loaded if loaded else ImageFont.load_default())
    return result


def _cell_color(count: int, mx: int) -> tuple:
    """0 → зелёный, 1..max → розовый→красный."""
    if count == 0:
        return (56, 193, 114)
    # розовый (255,105,150) → красный (210,30,50)
    t = min(count / mx, 1.0) if mx > 0 else 1.0
    r = int(255 + t * (210 - 255))
    g = int(105 + t * (30  - 105))
    b = int(150 + t * (50  - 150))
    return (r, g, b)


def _build_stability_chart(panel_states: dict, panels: dict) -> bytes:
    """
    Тепловая карта: строки = серверы, столбцы = дни (30 дней).
    Ширина ~1400px (высокое качество). Цвет ячейки: зелёный→жёлтый→красный по кол-ву сбоев.
    """
    import io
    import datetime as dt_mod
    from PIL import Image, ImageDraw

    now   = datetime.now()
    today = now.date()
    DAYS  = 30
    day_list = [today - dt_mod.timedelta(days=DAYS - 1 - i) for i in range(DAYS)]

    panel_ids = list(panels.keys())
    if not panel_ids:
        raise ValueError("Нет панелей")
    aliases = {pid: getattr(panels[pid], 'alias', pid) or pid for pid in panel_ids}

    outages_per_day: dict = {}
    day_set = set(day_list)
    for pid in panel_ids:
        day_counts: dict = {}
        ps = panel_states.get(pid)
        if ps:
            for ev in ps.outage_events:
                d = ev.date()
                if d in day_set:
                    day_counts[d] = day_counts.get(d, 0) + 1
        outages_per_day[pid] = day_counts

    all_counts = [c for pid in panel_ids for c in outages_per_day[pid].values()]
    max_count  = max(all_counts) if all_counts else 1

    TARGET_W   = 1400
    ALIAS_W    = 160
    RIGHT_PAD  = 24
    DATE_H     = 56
    TITLE_H    = 64
    BOTTOM_PAD = 60

    available_w = TARGET_W - ALIAS_W - RIGHT_PAD
    CELL = max(30, available_w // DAYS - 4)
    GAP  = 4
    STEP = CELL + GAP
    grid_w = DAYS * STEP - GAP
    img_w  = ALIAS_W + grid_w + RIGHT_PAD

    n_panels = len(panel_ids)
    ROW_H  = CELL + GAP + 4
    img_h  = TITLE_H + DATE_H + n_panels * ROW_H + BOTTOM_PAD

    BG        = (13,  17,  23)
    TEXT_COL  = (201, 209, 217)
    MUTED_COL = (110, 118, 129)
    SEP_COL   = (40,  46,  54)

    img  = Image.new("RGB", (img_w, img_h), color=BG)
    draw = ImageDraw.Draw(img)

    font_bold, font_label, font_small = _load_fonts([22, 18, 15])

    # Заголовок
    period_str = f"{day_list[0].strftime('%d.%m.%Y')} — {day_list[-1].strftime('%d.%m.%Y')}"
    draw.text((ALIAS_W, 10),  "Stability", font=font_bold,  fill=TEXT_COL)
    draw.text((ALIAS_W, 38),  period_str,                 font=font_small, fill=MUTED_COL)

    # Подписи дат сверху
    last_month = None
    MONTH_EN = ['','Jan','Feb','Mar','Apr','May','Jun',
                'Jul','Aug','Sep','Oct','Nov','Dec']
    for col, d in enumerate(day_list):
        cx = ALIAS_W + col * STEP + CELL // 2
        draw.text((cx, TITLE_H + 2), str(d.day), font=font_small, fill=MUTED_COL,
                  anchor="mt" if hasattr(draw, 'textbbox') else None)
        if d.month != last_month:
            last_month = d.month
            draw.text((cx, TITLE_H + 26), MONTH_EN[d.month], font=font_small, fill=TEXT_COL,
                      anchor="mt" if hasattr(draw, 'textbbox') else None)

    # Строки серверов
    for row_i, pid in enumerate(panel_ids):
        alias      = aliases[pid]
        day_counts = outages_per_day[pid]
        row_y      = TITLE_H + DATE_H + row_i * ROW_H

        draw.line([(0, row_y), (img_w, row_y)], fill=SEP_COL, width=1)
        draw.text((8, row_y + ROW_H // 2), alias, font=font_label, fill=TEXT_COL,
                  anchor="lm" if hasattr(draw, 'textbbox') else None)

        for col, d in enumerate(day_list):
            count = day_counts.get(d, 0)
            color = _cell_color(count, max_count)
            cx = ALIAS_W + col * STEP
            cy = row_y + 3
            draw.rounded_rectangle([cx, cy, cx + CELL - 1, cy + CELL - 1], radius=5, fill=color)
            if count > 0 and CELL >= 28:
                lbl = str(count) if count < 100 else "99+"
                draw.text((cx + CELL // 2, cy + CELL // 2), lbl,
                          font=font_small, fill=(0, 0, 0),
                          anchor="mm" if hasattr(draw, 'textbbox') else None)

    # Легенда
    leg_y = TITLE_H + DATE_H + n_panels * ROW_H + 12
    draw.line([(0, leg_y - 6), (img_w, leg_y - 6)], fill=SEP_COL, width=1)
    leg_x = ALIAS_W
    draw.text((leg_x, leg_y + 5), "0", font=font_small, fill=MUTED_COL)
    leg_x += 28
    for s in range(5):
        c = _cell_color(s, 4)
        draw.rounded_rectangle([leg_x, leg_y, leg_x + 22, leg_y + 22], radius=4, fill=c)
        leg_x += 28
    draw.text((leg_x + 6, leg_y + 5), "max", font=font_small, fill=MUTED_COL)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()



def _build_stability_chart_v2(panel_states: dict, panels: dict) -> bytes:
    """
    Тепловая карта v2 (многострочная): каждый сервер — блок из N строк по
    временным слотам (0-4, 4-8, 8-12, 12-16, 16-20, 20-24).
    Столбцы = дни (112 дней / ~16 недель).
    Цвет: серый фон = 0 сбоев, розово-красный градиент = 1+ сбоев.
    Шкалы крупные, текст хорошо читается, цифры в ячейках — чёрные.
    """
    import io
    import datetime as dt_mod
    from PIL import Image, ImageDraw

    SLOT_HOURS = 4          # часов в одном временном слоте
    SLOTS      = 24 // SLOT_HOURS   # = 6 слотов
    SLOT_LABELS = [f"{h:02d}h" for h in range(0, 24, SLOT_HOURS)]  # 00h 04h 08h …

    now   = datetime.now()
    today = now.date()
    DAYS  = 112  # 16 недель
    day_list = [today - dt_mod.timedelta(days=DAYS - 1 - i) for i in range(DAYS)]
    day_set  = set(day_list)

    panel_ids = list(panels.keys())
    if not panel_ids:
        raise ValueError("Нет панелей")
    aliases = {pid: getattr(panels[pid], 'alias', pid) or pid for pid in panel_ids}

    # outages_per_slot[pid][(date, slot_idx)] = count
    outages_per_slot: dict = {}
    for pid in panel_ids:
        slot_counts: dict = {}
        ps = panel_states.get(pid)
        if ps:
            for ev in ps.outage_events:
                d = ev.date()
                if d in day_set:
                    slot_idx = ev.hour // SLOT_HOURS
                    key = (d, slot_idx)
                    slot_counts[key] = slot_counts.get(key, 0) + 1
        outages_per_slot[pid] = slot_counts

    all_counts = [c for pid in panel_ids for c in outages_per_slot[pid].values()]
    max_count  = max(all_counts) if all_counts else 1

    # --- Размеры ---
    CELL       = 16   # px — ширина/высота ячейки
    GAP        = 2    # зазор между ячейками
    STEP       = CELL + GAP

    ALIAS_W    = 110  # ширина колонки с именем сервера
    SLOT_LBL_W = 38   # ширина колонки подписей слотов (00h …)
    LEFT_W     = ALIAS_W + SLOT_LBL_W
    RIGHT_PAD  = 20
    DATE_H     = 48   # высота строки с датами/месяцами
    TITLE_H    = 52
    SERVER_GAP = 14   # разрыв между блоками серверов
    BOTTOM_PAD = 52

    grid_w  = DAYS * STEP - GAP
    img_w   = LEFT_W + grid_w + RIGHT_PAD

    block_h = SLOTS * STEP - GAP  # высота одного блока сервера
    n       = len(panel_ids)
    img_h   = TITLE_H + DATE_H + n * (block_h + SERVER_GAP) - SERVER_GAP + BOTTOM_PAD

    # --- Цвета ---
    BG         = (18,  22,  30)
    TEXT_COL   = (210, 218, 226)
    MUTED_COL  = (100, 110, 122)
    SEP_COL    = (45,  52,  62)
    EMPTY_COL  = (35,  42,  54)   # серый фон пустой ячейки

    def _slot_color(count: int, mx: int) -> tuple:
        """0 → EMPTY_COL (серый), 1+ → розово-красный градиент."""
        if count == 0:
            return EMPTY_COL
        t = min(count / mx, 1.0) if mx > 0 else 1.0
        # нежно-розовый (255,160,160) → ярко-красный (220,30,30)
        r = 255
        g = int(160 - t * 130)
        b = int(160 - t * 130)
        return (r, max(30, g), max(30, b))

    img  = Image.new("RGB", (img_w, img_h), color=BG)
    draw = ImageDraw.Draw(img)

    font_title, font_alias, font_date, font_cell = _load_fonts([18, 14, 12, 10])

    # --- Заголовок ---
    period_str = f"{day_list[0].strftime('%d.%m.%Y')} — {day_list[-1].strftime('%d.%m.%Y')}"
    draw.text((LEFT_W, 8),  "Stability (slots)", font=font_title, fill=TEXT_COL)
    draw.text((LEFT_W, 32), period_str,                 font=font_date,  fill=MUTED_COL)

    # --- Шкала дат сверху ---
    last_month = None
    MONTH_EN = ['','Jan','Feb','Mar','Apr','May','Jun',
                'Jul','Aug','Sep','Oct','Nov','Dec']
    for col, d in enumerate(day_list):
        cx = LEFT_W + col * STEP + CELL // 2
        # число дня — каждые 7 дней (понедельник) или первый
        if d.weekday() == 0 or col == 0:
            draw.text((cx, TITLE_H + 2), str(d.day), font=font_date, fill=MUTED_COL,
                      anchor="mt" if hasattr(draw, 'textbbox') else None)
        # месяц — при смене
        if d.month != last_month:
            last_month = d.month
            draw.text((cx, TITLE_H + 22), MONTH_EN[d.month], font=font_alias, fill=TEXT_COL,
                      anchor="mt" if hasattr(draw, 'textbbox') else None)

    # --- Блоки серверов ---
    for row_i, pid in enumerate(panel_ids):
        alias      = aliases[pid]
        slot_counts = outages_per_slot[pid]
        block_y    = TITLE_H + DATE_H + row_i * (block_h + SERVER_GAP)
        mid_y      = block_y + block_h // 2

        # Разделитель перед блоком
        draw.line([(0, block_y - 4), (img_w, block_y - 4)], fill=SEP_COL, width=1)

        # Имя сервера — по центру блока слева
        draw.text((6, mid_y), alias, font=font_alias, fill=TEXT_COL,
                  anchor="lm" if hasattr(draw, 'textbbox') else None)

        # Подписи слотов
        for s_i, lbl in enumerate(SLOT_LABELS):
            sy = block_y + s_i * STEP + CELL // 2
            draw.text((ALIAS_W + SLOT_LBL_W - 4, sy), lbl, font=font_cell, fill=MUTED_COL,
                      anchor="rm" if hasattr(draw, 'textbbox') else None)

        # Ячейки
        for col, d in enumerate(day_list):
            for s_i in range(SLOTS):
                count = slot_counts.get((d, s_i), 0)
                color = _slot_color(count, max_count)
                cx = LEFT_W + col * STEP
                cy = block_y + s_i * STEP
                draw.rectangle([cx, cy, cx + CELL - 1, cy + CELL - 1], fill=color)
                if count > 0 and CELL >= 14:
                    lbl = str(count) if count < 10 else "9+"
                    draw.text((cx + CELL // 2, cy + CELL // 2), lbl,
                              font=font_cell, fill=(0, 0, 0),
                              anchor="mm" if hasattr(draw, 'textbbox') else None)

    # --- Легенда ---
    leg_y = TITLE_H + DATE_H + n * (block_h + SERVER_GAP) - SERVER_GAP + 10
    draw.line([(0, leg_y - 4), (img_w, leg_y - 4)], fill=SEP_COL, width=1)
    leg_x = LEFT_W
    draw.text((leg_x, leg_y + 3), "0 outages", font=font_date, fill=MUTED_COL)
    leg_x += 22
    for s in range(6):
        c = _slot_color(s, 5)
        draw.rectangle([leg_x, leg_y, leg_x + 14, leg_y + 14], fill=c)
        leg_x += 18
    draw.text((leg_x + 4, leg_y + 3), "max outages", font=font_date, fill=MUTED_COL)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()


def _build_timeline_chart(panel_states: dict, panels: dict) -> bytes:
    """
    Timeline-лента: одна толстая горизонтальная полоска на сервер.
    Ось X = дни (до 112 дней), цвет столбика = кол-во сбоев за день.
    0 сбоев → тёмно-серый, 1+ → розово-красный градиент.
    Справа: суммарный счётчик сбоев. Ось X: числа + метки месяцев.
    """
    import io
    import datetime as dt_mod
    from PIL import Image, ImageDraw

    now   = datetime.now()
    today = now.date()
    DAYS  = 112
    day_list = [today - dt_mod.timedelta(days=DAYS - 1 - i) for i in range(DAYS)]
    day_set  = set(day_list)

    panel_ids = list(panels.keys())
    if not panel_ids:
        raise ValueError("Нет панелей")
    aliases = {pid: getattr(panels[pid], 'alias', pid) or pid for pid in panel_ids}

    # outages_per_day[pid][date] = count
    outages_per_day: dict = {}
    for pid in panel_ids:
        day_counts: dict = {}
        ps = panel_states.get(pid)
        if ps:
            for ev in ps.outage_events:
                d = ev.date()
                if d in day_set:
                    day_counts[d] = day_counts.get(d, 0) + 1
        outages_per_day[pid] = day_counts

    all_counts = [c for pid in panel_ids for c in outages_per_day[pid].values()]
    max_count  = max(all_counts) if all_counts else 1

    # --- Размеры ---
    ALIAS_W    = 120   # колонка с именем сервера
    RIGHT_PAD  = 16
    COUNT_W    = 90    # колонка счётчика справа
    BAND_H     = 36    # высота ленты одного сервера
    BAND_GAP   = 18    # зазор между серверами
    AXIS_H     = 52    # высота оси X снизу
    TITLE_H    = 56    # высота заголовка
    TOP_PAD    = 8
    BOTTOM_PAD = 16

    DAY_W = 11         # ширина столбика одного дня в px
    DAY_GAP = 1        # зазор между столбиками
    DAY_STEP = DAY_W + DAY_GAP

    grid_w = DAYS * DAY_STEP - DAY_GAP
    img_w  = ALIAS_W + grid_w + COUNT_W + RIGHT_PAD
    n      = len(panel_ids)
    img_h  = TOP_PAD + TITLE_H + n * (BAND_H + BAND_GAP) - BAND_GAP + AXIS_H + BOTTOM_PAD

    # --- Цвета ---
    BG          = (18,  22,  30)
    TEXT_COL    = (210, 218, 226)
    MUTED_COL   = (100, 110, 122)
    SEP_COL     = (45,  52,  62)
    EMPTY_COL   = (38,  46,  60)
    GRID_LINE   = (30,  37,  50)
    COUNT_MUTED = (120, 130, 145)

    def _day_color(count: int, mx: int) -> tuple:
        if count == 0:
            return EMPTY_COL
        t = min(count / mx, 1.0) if mx > 0 else 1.0
        # розовый (255,140,140) → ярко-красный (220,25,25)
        g = int(140 - t * 115)
        b = int(140 - t * 115)
        return (255, max(25, g), max(25, b))

    img  = Image.new("RGB", (img_w, img_h), color=BG)
    draw = ImageDraw.Draw(img)

    font_title, font_alias, font_axis, font_count = _load_fonts([19, 15, 12, 13])

    grid_x0 = ALIAS_W  # X-начало сетки

    # --- Заголовок ---
    period_str = f"{day_list[0].strftime('%d.%m.%Y')} — {day_list[-1].strftime('%d.%m.%Y')}"
    draw.text((grid_x0, TOP_PAD + 4),  "Stability Timeline", font=font_title, fill=TEXT_COL)
    draw.text((grid_x0, TOP_PAD + 28), period_str,                  font=font_axis,  fill=MUTED_COL)

    bands_y0 = TOP_PAD + TITLE_H  # Y-начало первой ленты

    # --- Вертикальные линии сетки (каждую неделю) ---
    for col, d in enumerate(day_list):
        if d.weekday() == 0:
            gx = grid_x0 + col * DAY_STEP
            draw.line([(gx, bands_y0), (gx, bands_y0 + n * (BAND_H + BAND_GAP) - BAND_GAP)],
                      fill=GRID_LINE, width=1)

    # --- Ленты серверов ---
    for row_i, pid in enumerate(panel_ids):
        alias      = aliases[pid]
        day_counts = outages_per_day[pid]
        total      = sum(day_counts.values())
        band_y     = bands_y0 + row_i * (BAND_H + BAND_GAP)

        # Имя сервера
        draw.text((6, band_y + BAND_H // 2), alias, font=font_alias, fill=TEXT_COL,
                  anchor="lm" if hasattr(draw, 'textbbox') else None)

        # Фоновая полоска
        draw.rounded_rectangle(
            [grid_x0, band_y, grid_x0 + grid_w, band_y + BAND_H],
            radius=4, fill=EMPTY_COL
        )

        # Столбики дней
        for col, d in enumerate(day_list):
            count = day_counts.get(d, 0)
            if count == 0:
                continue
            color = _day_color(count, max_count)
            cx = grid_x0 + col * DAY_STEP
            # Высота столбика пропорциональна count (минимум 4px)
            bar_h = max(4, int(BAND_H * min(count / max_count, 1.0)))
            cy_top = band_y + BAND_H - bar_h
            draw.rectangle([cx, cy_top, cx + DAY_W - 1, band_y + BAND_H], fill=color)

        # Счётчик справа
        count_x = grid_x0 + grid_w + 12
        if total > 0:
            draw.text((count_x, band_y + BAND_H // 2),
                      f"{total} outages", font=font_count, fill=(220, 100, 100),
                      anchor="lm" if hasattr(draw, 'textbbox') else None)
        else:
            draw.text((count_x, band_y + BAND_H // 2),
                      "ok", font=font_count, fill=(80, 180, 100),
                      anchor="lm" if hasattr(draw, 'textbbox') else None)

        # Разделитель между серверами
        if row_i < n - 1:
            sep_y = band_y + BAND_H + BAND_GAP // 2
            draw.line([(0, sep_y), (img_w, sep_y)], fill=SEP_COL, width=1)

    # --- Ось X (снизу) ---
    axis_y = bands_y0 + n * (BAND_H + BAND_GAP) - BAND_GAP + 6
    draw.line([(grid_x0, axis_y), (grid_x0 + grid_w, axis_y)], fill=SEP_COL, width=1)

    MONTH_EN   = ['','Jan','Feb','Mar','Apr','May','Jun',
                  'Jul','Aug','Sep','Oct','Nov','Dec']
    last_month = None
    for col, d in enumerate(day_list):
        cx = grid_x0 + col * DAY_STEP + DAY_W // 2
        # число каждого понедельника
        if d.weekday() == 0 or col == 0:
            draw.text((cx, axis_y + 4), str(d.day), font=font_axis, fill=MUTED_COL,
                      anchor="mt" if hasattr(draw, 'textbbox') else None)
        # метка месяца при смене
        if d.month != last_month:
            last_month = d.month
            draw.text((cx, axis_y + 22), MONTH_EN[d.month], font=font_alias, fill=TEXT_COL,
                      anchor="mt" if hasattr(draw, 'textbbox') else None)

    # --- Легенда ---
    leg_y = axis_y + 44
    leg_x = grid_x0
    draw.text((leg_x, leg_y), "ok", font=font_axis, fill=MUTED_COL)
    leg_x += 48
    draw.rectangle([leg_x, leg_y - 1, leg_x + 20, leg_y + 11], fill=EMPTY_COL)
    leg_x += 28
    for s in range(5):
        c = _day_color(s + 1, 5)
        draw.rectangle([leg_x, leg_y - 1, leg_x + 20, leg_y + 11], fill=c)
        leg_x += 26
    draw.text((leg_x + 4, leg_y), "many outages", font=font_axis, fill=MUTED_COL)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()


async def _render_servers_screen(callback_query: types.CallbackQuery, is_refresh: bool = False):
    """Рендер экрана 'Серверы': кнопки панелей + метрики текущей панели + статистика сбоев."""
    panel_manager = config.panel_manager
    panels = panel_manager.get_all_panels()
    current_panel_id = panel_manager.get_current_panel_id()

    if not panels:
        await callback_query.answer("❌ Панели не настроены", show_alert=True)
        return

    # Кнопки панелей: panel0 всегда, сетевые только v3+
    keyboard_buttons = []
    for panel_id, panel_config in panels.items():
        is_local_panel = (panel_id == "panel0")
        is_v3_or_higher = panel_config.is_v3() if hasattr(panel_config, 'is_v3') else False

        if not is_local_panel and not is_v3_or_higher:
            logger.debug(f"⏭️ Пропуск панели {panel_id} (v<3, не panel0) — только мониторинг")
            continue

        alias = getattr(panel_config, 'alias', panel_id)
        location = getattr(panel_config, 'location_label', '')
        is_current = panel_id == current_panel_id

        # Индикатор доступности: смотрим в panel_states монитора
        if _panel_monitor:
            ps = _panel_monitor.panel_states.get(panel_id)
            avail_dot = "🟢" if (ps is None or ps.is_available) else "🔴"
        else:
            avail_dot = "🟢"

        location_str = f" · {location}" if location else ""
        status_icon = "✅" if is_current else "⏸️"
        button_text = f"{status_icon} {avail_dot} {alias}{location_str}"

        keyboard_buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"connect_panel:{panel_id}"
            )
        ])

    keyboard_buttons.append([
        InlineKeyboardButton(text="📋 Все VLESS ключи сервера", callback_data="cmd_allclients"),
    ])
    keyboard_buttons.append([
        InlineKeyboardButton(
            text="📄 JSON конфиги",
            callback_data=f"panel_json_configs:{current_panel_id}"
        ),
    ])
    keyboard_buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_servers"),
        InlineKeyboardButton(text="◀️ Назад",    callback_data="server_status"),
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    # Блок текущей панели
    current_panel   = config.get_current_panel()
    panel_alias     = getattr(current_panel, 'alias', current_panel_id) or current_panel_id if current_panel else current_panel_id or '—'
    panel_version   = getattr(current_panel, 'xui_version', '') if current_panel else ''
    panel_location  = getattr(current_panel, 'location_label', '') if current_panel else ''
    panel_transport = (getattr(current_panel, 'transport', '') or '').upper() if current_panel else ''
    panel_security  = (getattr(current_panel, 'security',  '') or '').upper() if current_panel else ''

    panel_block = f"📡 <b>{panel_alias}</b>"
    if panel_version:
        panel_block += f"  <code>v{panel_version}</code>"
    panel_block += "\n"
    if current_panel_id:
        panel_block += f"🆔 <code>{current_panel_id}</code>\n"
    if panel_location:
        panel_block += f"📍 {panel_location}\n"
    if panel_transport or panel_security:
        panel_block += f"🔌 <code>{panel_transport}</code> · <code>{panel_security}</code>\n"

    # Метрики сервера
    metrics_block = ""
    try:
        status = await xui_client.get_server_status()
        if status:
            def _fmt(b):
                if b >= 1024**3: return f"{b/(1024**3):.2f} GB"
                if b >= 1024**2: return f"{b/(1024**2):.2f} MB"
                if b >= 1024:    return f"{b/1024:.2f} KB"
                return f"{b} B"

            cpu      = status.get('cpu', 0)
            mem      = status.get('mem', {})
            mem_cur  = mem.get('current', 0);  mem_tot = mem.get('total', 1)
            disk     = status.get('disk', {})
            disk_cur = disk.get('current', 0); disk_tot = disk.get('total', 1)
            net_io   = status.get('netIO', {})
            xray     = status.get('xray', {})
            xray_state   = xray.get('state', 'unknown')
            xray_version = xray.get('version', 'unknown')
            tcp_count    = status.get('tcpCount', 0)

            try:
                all_clients = await xui_client.get_all_clients()
                tr_up   = sum(c.get('up',   0) for c in all_clients)
                tr_down = sum(c.get('down', 0) for c in all_clients)
            except Exception:
                tr_up = tr_down = 0

            xray_emoji = "✅" if xray_state == "running" else "❌"
            metrics_block = (
                f"\n💻 <b>CPU:</b> {cpu:.1f}%\n"
                f"🧠 <b>RAM:</b> {(mem_cur/mem_tot*100):.1f}% | {_fmt(mem_cur)} / {_fmt(mem_tot)}\n"
                f"💿 <b>Диск:</b> {(disk_cur/disk_tot*100):.1f}% | {_fmt(disk_cur)} / {_fmt(disk_tot)}\n"
                f"🌐 <b>Сеть:</b> ⬆️ {_fmt(net_io.get('up',0))} ⬇️ {_fmt(net_io.get('down',0))}\n"
                f"📊 <b>Трафик:</b> ⬆️ {_fmt(tr_up)} ⬇️ {_fmt(tr_down)}\n"
                f"🔐 <b>Xray:</b> {xray_emoji} {xray_state} <code>v{xray_version}</code>\n"
                f"🔌 <b>TCP:</b> {tcp_count}"
            )
    except Exception as e:
        logger.warning(f"Не удалось получить метрики сервера для экрана Серверы: {e}")

    text = f"🖥️ <b>Сервер</b>\n\n{panel_block}{metrics_block}"
    try:
        await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        # Сообщение не изменилось — игнорируем
        pass

    if is_refresh:
        await callback_query.answer("✅ Обновлено")


@dp.callback_query(lambda c: c.data == "select_panel_to_connect")
async def select_panel_to_connect(callback_query: types.CallbackQuery, state: FSMContext):
    """Выбрать панель для подключения"""
    await callback_query.answer()

    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.message.answer("⛔ Доступ запрещен")
        return

    await _refresh_panel_states_now()

    try:
        await _render_servers_screen(callback_query)
    except Exception as e:
        logger.error(f"Ошибка выбора панели: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data == "refresh_servers")
async def refresh_servers(callback_query: types.CallbackQuery, state: FSMContext):
    """Обновить экран Серверы"""
    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return

    await _refresh_panel_states_now()

    try:
        await _render_servers_screen(callback_query, is_refresh=True)
    except Exception as e:
        logger.error(f"Ошибка обновления экрана серверов: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


def _build_month_heatmap(panel_states: dict, panels: dict, year: int, month: int) -> bytes:
    """
    Тепловая карта за один месяц.
    Строки = серверы, столбцы = дни месяца.
    Ячейки с цифрами числа сбоев, цвет: зелёный→жёлтый→красный.
    """
    import io
    import datetime as dt_mod
    import calendar
    from PIL import Image, ImageDraw

    days_in_month = calendar.monthrange(year, month)[1]
    day_list = [dt_mod.date(year, month, d) for d in range(1, days_in_month + 1)]

    panel_ids = list(panels.keys())
    if not panel_ids:
        raise ValueError("Нет панелей")
    aliases = {pid: getattr(panels[pid], 'alias', pid) or pid for pid in panel_ids}

    outages_per_day: dict = {}
    day_set = set(day_list)
    for pid in panel_ids:
        day_counts: dict = {}
        ps = panel_states.get(pid)
        if ps:
            for ev in ps.outage_events:
                d = ev.date()
                if d in day_set:
                    day_counts[d] = day_counts.get(d, 0) + 1
        outages_per_day[pid] = day_counts

    all_counts = [c for pid in panel_ids for c in outages_per_day[pid].values()]
    max_count  = max(all_counts) if all_counts else 1

    CELL       = 46
    GAP        = 4
    STEP       = CELL + GAP
    ALIAS_W    = 130
    RIGHT_PAD  = 20
    HEADER_H   = 60   # заголовок
    DATE_H     = 36   # строка с числами дней
    ROW_GAP    = 6
    BOTTOM_PAD = 54

    n       = len(panel_ids)
    grid_w  = days_in_month * STEP - GAP
    img_w   = ALIAS_W + grid_w + RIGHT_PAD
    img_h   = HEADER_H + DATE_H + n * (CELL + ROW_GAP) - ROW_GAP + BOTTOM_PAD

    BG        = (18,  22,  30)
    TEXT_COL  = (210, 218, 226)
    MUTED_COL = (100, 110, 122)
    SEP_COL   = (45,  52,  62)

    MONTH_NAMES = ['','Jan','Feb','Mar','Apr','May','Jun',
                   'Jul','Aug','Sep','Oct','Nov','Dec']
    WDAY_SHORT  = ['Mo','Tu','We','Th','Fr','Sa','Su']

    img  = Image.new("RGB", (img_w, img_h), color=BG)
    draw = ImageDraw.Draw(img)
    font_title, font_alias, font_day, font_cell = _load_fonts([20, 16, 13, 14])

    # Заголовок
    title_str  = f"{MONTH_NAMES[month]} {year}"
    draw.text((ALIAS_W, 10), title_str, font=font_title, fill=TEXT_COL)
    draw.text((ALIAS_W, 36), f"{days_in_month} days", font=font_day, fill=MUTED_COL)

    # Шапка — числа дней + день недели
    for col, d in enumerate(day_list):
        cx = ALIAS_W + col * STEP + CELL // 2
        draw.text((cx, HEADER_H + 2), str(d.day), font=font_day, fill=MUTED_COL,
                  anchor="mt" if hasattr(draw, 'textbbox') else None)
        wd = WDAY_SHORT[d.weekday()]
        wd_col = (160, 80, 80) if d.weekday() >= 5 else MUTED_COL
        draw.text((cx, HEADER_H + 18), wd, font=font_day, fill=wd_col,
                  anchor="mt" if hasattr(draw, 'textbbox') else None)

    # Строки серверов
    rows_y0 = HEADER_H + DATE_H
    for row_i, pid in enumerate(panel_ids):
        alias      = aliases[pid]
        day_counts = outages_per_day[pid]
        row_y      = rows_y0 + row_i * (CELL + ROW_GAP)

        draw.line([(0, row_y - ROW_GAP // 2), (img_w, row_y - ROW_GAP // 2)], fill=SEP_COL, width=1)
        draw.text((6, row_y + CELL // 2), alias, font=font_alias, fill=TEXT_COL,
                  anchor="lm" if hasattr(draw, 'textbbox') else None)

        for col, d in enumerate(day_list):
            count = day_counts.get(d, 0)
            color = _cell_color(count, max_count)
            cx = ALIAS_W + col * STEP
            draw.rounded_rectangle([cx, row_y, cx + CELL - 1, row_y + CELL - 1], radius=6, fill=color)
            lbl = str(count) if count < 100 else "99+"
            draw.text((cx + CELL // 2, row_y + CELL // 2), lbl,
                      font=font_cell, fill=(0, 0, 0),
                      anchor="mm" if hasattr(draw, 'textbbox') else None)

    # Легенда
    leg_y = rows_y0 + n * (CELL + ROW_GAP) - ROW_GAP + 12
    draw.line([(0, leg_y - 4), (img_w, leg_y - 4)], fill=SEP_COL, width=1)
    leg_x = ALIAS_W
    draw.text((leg_x, leg_y + 5), "0", font=font_day, fill=MUTED_COL)
    leg_x += 24
    for s in range(5):
        c = _cell_color(s, 4)
        draw.rounded_rectangle([leg_x, leg_y, leg_x + 22, leg_y + 22], radius=4, fill=c)
        leg_x += 28
    draw.text((leg_x + 4, leg_y + 5), "max", font=font_day, fill=MUTED_COL)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()


def _get_available_months(panel_states: dict) -> list:
    """Возвращает список (year, month) за которые есть хоть один сбой, отсортированный по дате."""
    months = set()
    for ps in panel_states.values():
        for ev in ps.outage_events:
            months.add((ev.year, ev.month))
    return sorted(months)


def _month_keyboard(available_months: list, back_cb: str = "select_panel_to_connect") -> InlineKeyboardMarkup:
    """Строит клавиатуру с кнопками месяцев (по 4 в ряд) + кнопка Назад."""
    MONTH_RU = ['','Янв','Фев','Мар','Апр','Май','Июн',
                'Июл','Авг','Сен','Окт','Ноя','Дек']
    rows = []
    row  = []
    for year, month in available_months:
        label = f"{MONTH_RU[month]} {str(year)[-2:]}"
        row.append(InlineKeyboardButton(text=label, callback_data=f"stability_month:{year}-{month:02d}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_combined_chart(panel_states: dict, panels: dict) -> bytes:
    """
    Комбинированный график: два блока в одном изображении.
    Левый  — timeline-лента 60 дней (пиксельный столбик = 1 день).
    Правый — детальная ячеечная карта последних двух полных месяцев
             (крупные ячейки с цифрами, цвет зелёный→жёлтый→красный).
    """
    import io
    import calendar
    import datetime as dt_mod
    from PIL import Image, ImageDraw

    now   = datetime.now()
    today = now.date()

    # --- Левый блок: timeline 60 дней ---
    TL_DAYS    = 60
    TL_DAY_W   = 13
    TL_DAY_GAP = 2
    TL_STEP    = TL_DAY_W + TL_DAY_GAP
    TL_BAND_H  = 34
    TL_BAND_GAP = 14
    TL_ALIAS_W  = 110
    TL_AXIS_H   = 48

    tl_day_list = [today - dt_mod.timedelta(days=TL_DAYS - 1 - i) for i in range(TL_DAYS)]
    tl_day_set  = set(tl_day_list)
    tl_grid_w   = TL_DAYS * TL_STEP - TL_DAY_GAP

    # --- Правый блок: ячеечная карта 2 последних месяца ---
    HM_CELL    = 38
    HM_GAP     = 3
    HM_STEP    = HM_CELL + HM_GAP
    HM_DATE_H  = 34
    HM_ROW_GAP = TL_BAND_GAP

    first_day_this = today.replace(day=1)
    first_day_prev = (first_day_this - dt_mod.timedelta(days=1)).replace(day=1)
    hm_months = [
        (first_day_prev.year, first_day_prev.month),
        (first_day_this.year, first_day_this.month),
    ]
    hm_day_lists = []
    for yr, mo in hm_months:
        dim = calendar.monthrange(yr, mo)[1]
        hm_day_lists.append([dt_mod.date(yr, mo, d) for d in range(1, dim + 1)])

    hm_all_days = [d for dl in hm_day_lists for d in dl]
    hm_day_set  = set(hm_all_days)

    panel_ids = list(panels.keys())
    if not panel_ids:
        raise ValueError("Нет панелей")
    aliases = {pid: getattr(panels[pid], 'alias', pid) or pid for pid in panel_ids}
    n = len(panel_ids)

    # Считаем сбои
    tl_counts = {}
    hm_counts = {}
    for pid in panel_ids:
        tc, hc = {}, {}
        ps = panel_states.get(pid)
        if ps:
            for ev in ps.outage_events:
                d = ev.date()
                if d in tl_day_set:
                    tc[d] = tc.get(d, 0) + 1
                if d in hm_day_set:
                    hc[d] = hc.get(d, 0) + 1
        tl_counts[pid] = tc
        hm_counts[pid] = hc

    tl_max = max((c for pid in panel_ids for c in tl_counts[pid].values()), default=1)
    hm_max = max((c for pid in panel_ids for c in hm_counts[pid].values()), default=1)

    # --- Размеры холста ---
    TITLE_H    = 52
    TOP_PAD    = 8
    DIVIDER_W  = 2
    RIGHT_PAD  = 16
    BOTTOM_PAD = 20
    SEP_PAD    = 28

    hm_months_w  = [len(dl) * HM_STEP - HM_GAP for dl in hm_day_lists]
    hm_total_w   = sum(hm_months_w) + SEP_PAD

    row_h   = max(TL_BAND_H, HM_CELL)
    rows_h  = n * row_h + (n - 1) * TL_BAND_GAP

    RIGHT_X0 = TL_ALIAS_W + tl_grid_w + SEP_PAD // 2 + DIVIDER_W + SEP_PAD // 2
    img_w    = RIGHT_X0 + hm_total_w + RIGHT_PAD
    img_h    = TOP_PAD + TITLE_H + rows_h + TL_AXIS_H + BOTTOM_PAD

    # --- Цвета ---
    BG        = (18,  22,  30)
    TEXT_COL  = (210, 218, 226)
    MUTED_COL = (100, 110, 122)
    SEP_COL   = (45,  52,  62)
    EMPTY_COL = (38,  46,  60)
    GRID_LINE = (30,  37,  50)

    MONTH_EN   = ['','Jan','Feb','Mar','Apr','May','Jun',
                  'Jul','Aug','Sep','Oct','Nov','Dec']
    WDAY_SHORT = ['Mo','Tu','We','Th','Fr','Sa','Su']

    def _tl_color(count, mx):
        if count == 0:
            return EMPTY_COL
        t = min(count / mx, 1.0) if mx > 0 else 1.0
        g = int(140 - t * 115)
        b = int(140 - t * 115)
        return (255, max(25, g), max(25, b))

    img  = Image.new("RGB", (img_w, img_h), color=BG)
    draw = ImageDraw.Draw(img)
    font_title, font_alias, font_axis, font_cell = _load_fonts([18, 15, 12, 13])

    bands_y0 = TOP_PAD + TITLE_H

    # === ЗАГОЛОВКИ ===
    tl_period = f"{tl_day_list[0].strftime('%d.%m')} - {tl_day_list[-1].strftime('%d.%m.%Y')}"
    draw.text((TL_ALIAS_W, TOP_PAD + 4),  "Stability Timeline", font=font_title, fill=TEXT_COL)
    draw.text((TL_ALIAS_W, TOP_PAD + 28), tl_period,             font=font_axis,  fill=MUTED_COL)

    hm_label = f"{MONTH_EN[hm_months[0][1]]} + {MONTH_EN[hm_months[1][1]]} {hm_months[1][0]}"
    draw.text((RIGHT_X0, TOP_PAD + 4),  "Detail (2 months)", font=font_title, fill=TEXT_COL)
    draw.text((RIGHT_X0, TOP_PAD + 28), hm_label,             font=font_axis,  fill=MUTED_COL)

    # === РАЗДЕЛИТЕЛЬ ===
    div_x = TL_ALIAS_W + tl_grid_w + SEP_PAD // 2
    draw.line([(div_x, TOP_PAD), (div_x, img_h - BOTTOM_PAD)], fill=SEP_COL, width=DIVIDER_W)

    # === ПРАВЫЙ БЛОК: шапка дней ===
    hm_x_offsets = []
    cur_x = RIGHT_X0
    for m_i, (yr, mo) in enumerate(hm_months):
        dl = hm_day_lists[m_i]
        hm_x_offsets.append(cur_x)
        for col, d in enumerate(dl):
            cx = cur_x + col * HM_STEP + HM_CELL // 2
            draw.text((cx, bands_y0 - HM_DATE_H + 2), str(d.day), font=font_axis, fill=MUTED_COL,
                      anchor="mt" if hasattr(draw, 'textbbox') else None)
            wd_col = (170, 80, 80) if d.weekday() >= 5 else (60, 70, 90)
            draw.text((cx, bands_y0 - HM_DATE_H + 18), WDAY_SHORT[d.weekday()],
                      font=font_axis, fill=wd_col,
                      anchor="mt" if hasattr(draw, 'textbbox') else None)
        cur_x += len(dl) * HM_STEP - HM_GAP + SEP_PAD

    # === ЛЕВЫЙ БЛОК: вертикальные линии сетки (каждый понедельник) ===
    grid_bot = bands_y0 + rows_h
    for col, d in enumerate(tl_day_list):
        if d.weekday() == 0:
            gx = TL_ALIAS_W + col * TL_STEP
            draw.line([(gx, bands_y0), (gx, grid_bot)], fill=GRID_LINE, width=1)

    # === СТРОКИ СЕРВЕРОВ ===
    for row_i, pid in enumerate(panel_ids):
        alias  = aliases[pid]
        row_y  = bands_y0 + row_i * (row_h + TL_BAND_GAP)

        if row_i > 0:
            draw.line([(0, row_y - TL_BAND_GAP // 2), (img_w, row_y - TL_BAND_GAP // 2)],
                      fill=SEP_COL, width=1)

        # Имя сервера
        draw.text((6, row_y + row_h // 2), alias, font=font_alias, fill=TEXT_COL,
                  anchor="lm" if hasattr(draw, 'textbbox') else None)

        # --- Левый блок: timeline-лента ---
        tc = tl_counts[pid]
        total_tl = sum(tc.values())
        draw.rounded_rectangle(
            [TL_ALIAS_W, row_y, TL_ALIAS_W + tl_grid_w, row_y + TL_BAND_H],
            radius=4, fill=EMPTY_COL
        )
        for col, d in enumerate(tl_day_list):
            count = tc.get(d, 0)
            if count == 0:
                continue
            color = _tl_color(count, tl_max)
            cx    = TL_ALIAS_W + col * TL_STEP
            bar_h = max(4, int(TL_BAND_H * min(count / tl_max, 1.0)))
            draw.rectangle([cx, row_y + TL_BAND_H - bar_h, cx + TL_DAY_W - 1, row_y + TL_BAND_H],
                           fill=color)
        # Счётчик в зоне разделителя
        cnt_x   = TL_ALIAS_W + tl_grid_w + 6
        cnt_col = (220, 100, 100) if total_tl > 0 else (80, 180, 100)
        draw.text((cnt_x, row_y + TL_BAND_H // 2),
                  str(total_tl) if total_tl > 0 else "ok",
                  font=font_axis, fill=cnt_col,
                  anchor="lm" if hasattr(draw, 'textbbox') else None)

        # --- Правый блок: ячеечная карта ---
        hc = hm_counts[pid]
        for m_i in range(len(hm_months)):
            dl  = hm_day_lists[m_i]
            mx0 = hm_x_offsets[m_i]
            for col, d in enumerate(dl):
                count = hc.get(d, 0)
                color = _cell_color(count, hm_max)
                cx    = mx0 + col * HM_STEP
                draw.rounded_rectangle([cx, row_y, cx + HM_CELL - 1, row_y + HM_CELL - 1],
                                       radius=5, fill=color)
                lbl = str(count) if count < 100 else "99+"
                draw.text((cx + HM_CELL // 2, row_y + HM_CELL // 2), lbl,
                          font=font_cell, fill=(0, 0, 0),
                          anchor="mm" if hasattr(draw, 'textbbox') else None)

    # === ЛЕВЫЙ БЛОК: ось X снизу ===
    axis_y     = bands_y0 + rows_h + 4
    last_month = None
    draw.line([(TL_ALIAS_W, axis_y), (TL_ALIAS_W + tl_grid_w, axis_y)], fill=SEP_COL, width=1)
    for col, d in enumerate(tl_day_list):
        cx = TL_ALIAS_W + col * TL_STEP + TL_DAY_W // 2
        if d.weekday() == 0 or col == 0:
            draw.text((cx, axis_y + 4), str(d.day), font=font_axis, fill=MUTED_COL,
                      anchor="mt" if hasattr(draw, 'textbbox') else None)
        if d.month != last_month:
            last_month = d.month
            draw.text((cx, axis_y + 20), MONTH_EN[d.month], font=font_alias, fill=TEXT_COL,
                      anchor="mt" if hasattr(draw, 'textbbox') else None)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()


@dp.callback_query(lambda c: c.data == "stability_chart")
async def show_stability_chart(callback_query: types.CallbackQuery, state: FSMContext):
    """Статистика стабильности: timeline + детальные карты двух месяцев — три отдельных фото"""
    import datetime as dt_mod
    import calendar

    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return

    await callback_query.answer("📊 Строю графики...")

    try:
        panels = config.panel_manager.get_all_panels()
        panel_states = _panel_monitor.panel_states if _panel_monitor else {}

        # Строим все три изображения параллельно
        today = datetime.now().date()
        first_day_this = today.replace(day=1)
        first_day_prev = (first_day_this - dt_mod.timedelta(days=1)).replace(day=1)
        prev_y, prev_m = first_day_prev.year, first_day_prev.month
        this_y, this_m = first_day_this.year, first_day_this.month

        MONTH_EN = ['','Jan','Feb','Mar','Apr','May','Jun',
                    'Jul','Aug','Sep','Oct','Nov','Dec']

        loop = asyncio.get_event_loop()
        tl_bytes, prev_bytes, this_bytes = await asyncio.gather(
            loop.run_in_executor(None, _build_timeline_chart, panel_states, panels),
            loop.run_in_executor(None, _build_month_heatmap, panel_states, panels, prev_y, prev_m),
            loop.run_in_executor(None, _build_month_heatmap, panel_states, panels, this_y, this_m),
        )

        # Текстовая статистика
        stats_lines = []
        for pid, panel_config in panels.items():
            alias = getattr(panel_config, 'alias', pid)
            ps = panel_states.get(pid)
            if ps is None:
                continue
            count = len(ps.outage_events)
            if count == 0:
                stats_lines.append(f"  🟢 {alias}: 0")
            else:
                first_dt = ps.outage_events[0].strftime('%d.%m %H:%M')
                last_dt  = ps.outage_events[-1].strftime('%d.%m %H:%M')
                dot = "🔴" if not ps.is_available else "🟡"
                stats_lines.append(f"  {dot} {alias}: <b>{count}</b>  ({first_dt} — {last_dt})")

        stats_text = "\n".join(stats_lines) if stats_lines else "  данные недоступны"
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="server_status")]
        ])

        # Фото 1: timeline (с текстом статистики)
        await bot.send_photo(
            callback_query.message.chat.id,
            photo=types.BufferedInputFile(tl_bytes, filename="timeline.png"),
            caption=f"📊 <b>Количество случаев недоступности:</b>\n{stats_text}",
            parse_mode="HTML",
        )
        # Фото 2: предыдущий месяц
        await bot.send_photo(
            callback_query.message.chat.id,
            photo=types.BufferedInputFile(prev_bytes, filename=f"month_{prev_y}_{prev_m:02d}.png"),
            caption=f"📅 <b>{MONTH_EN[prev_m]} {prev_y}</b>",
            parse_mode="HTML",
        )
        # Фото 3: текущий месяц (с кнопкой Назад)
        await bot.send_photo(
            callback_query.message.chat.id,
            photo=types.BufferedInputFile(this_bytes, filename=f"month_{this_y}_{this_m:02d}.png"),
            caption=f"📅 <b>{MONTH_EN[this_m]} {this_y}</b>",
            parse_mode="HTML",
            reply_markup=back_kb,
        )
    except Exception as e:
        logger.error(f"Ошибка построения графика стабильности: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data == "stability_chart_v2")
async def show_stability_chart_v2(callback_query: types.CallbackQuery, state: FSMContext):
    """Тепловая карта v2 — разбивка по временным слотам"""
    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return

    await callback_query.answer("📊 Строю график...")

    try:
        panels = config.panel_manager.get_all_panels()
        panel_states = _panel_monitor.panel_states if _panel_monitor else {}

        png_bytes = await asyncio.get_event_loop().run_in_executor(
            None, _build_stability_chart_v2, panel_states, panels
        )

        # Собираем статистику сбоев для подписи
        stats_lines = []
        for pid, panel_config in panels.items():
            alias = getattr(panel_config, 'alias', pid)
            ps = panel_states.get(pid)
            if ps is None:
                continue
            count = len(ps.outage_events)
            if count == 0:
                stats_lines.append(f"  🟢 {alias}: сбоев нет")
            else:
                first_dt = ps.outage_events[0].strftime('%d.%m %H:%M')
                last_dt  = ps.outage_events[-1].strftime('%d.%m %H:%M')
                dot = "🔴" if not ps.is_available else "🟡"
                stats_lines.append(f"  {dot} {alias}: <b>{count}</b> сбоев  ({first_dt} — {last_dt})")

        stats_text = "\n".join(stats_lines) if stats_lines else "  данные недоступны"
        caption = f"🗓 <b>Статистика стабильности (по слотам)</b>\n\n<b>Сбои:</b>\n{stats_text}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="server_status")]
        ])
        await bot.send_photo(
            callback_query.message.chat.id,
            photo=types.BufferedInputFile(png_bytes, filename="stability_v2.png"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка построения графика стабильности v2: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)



@dp.callback_query(lambda c: c.data == "stability_timeline")
async def show_stability_timeline(callback_query: types.CallbackQuery, state: FSMContext):
    """Timeline-лента стабильности серверов"""
    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return

    await callback_query.answer("📈 Строю timeline...")

    try:
        panels = config.panel_manager.get_all_panels()
        panel_states = _panel_monitor.panel_states if _panel_monitor else {}

        png_bytes = await asyncio.get_event_loop().run_in_executor(
            None, _build_timeline_chart, panel_states, panels
        )

        # Статистика сбоев для подписи
        stats_lines = []
        for pid, panel_config in panels.items():
            alias = getattr(panel_config, 'alias', pid)
            ps = panel_states.get(pid)
            if ps is None:
                continue
            count = len(ps.outage_events)
            if count == 0:
                stats_lines.append(f"  🟢 {alias}: сбоев нет")
            else:
                first_dt = ps.outage_events[0].strftime('%d.%m %H:%M')
                last_dt  = ps.outage_events[-1].strftime('%d.%m %H:%M')
                dot = "🔴" if not ps.is_available else "🟡"
                stats_lines.append(f"  {dot} {alias}: <b>{count}</b> сбоев  ({first_dt} — {last_dt})")

        stats_text = "\n".join(stats_lines) if stats_lines else "  данные недоступны"
        caption = f"📈 <b>Timeline стабильности</b>\n\n<b>Сбои:</b>\n{stats_text}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="server_status")]
        ])
        await bot.send_photo(
            callback_query.message.chat.id,
            photo=types.BufferedInputFile(png_bytes, filename="timeline.png"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка построения timeline: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)





@dp.callback_query(lambda c: c.data and c.data.startswith("connect_panel:"))
async def connect_to_panel(callback_query: types.CallbackQuery, state: FSMContext):
    """Подключиться к выбранной панели"""
    global xui_client

    await callback_query.answer()

    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.message.answer("⛔ Доступ запрещен")
        return

    try:
        panel_id = callback_query.data.split(":", 1)[1]
        panel_manager = config.panel_manager
        current_panel_id = panel_manager.get_current_panel_id()

        panel_config = panel_manager.get_panel(panel_id)
        if not panel_config:
            await callback_query.answer("❌ Панель не найдена", show_alert=True)
            return

        alias = getattr(panel_config, 'alias', panel_id)

        # Если это уже активная панель — просто обновляем экран Серверы
        if panel_id == current_panel_id:
            panel_url = getattr(panel_config, 'xui_url', '') or getattr(panel_config, 'url', '')
            if panel_url != config.xui.url:
                logger.warning(f"⚠️ URL не совпадают! Panel: {panel_url}, Current: {config.xui.url}")
                new_xui_config = panel_manager.create_xui_config_from_panel(panel_id)
                if new_xui_config:
                    config.xui = new_xui_config
                    xui_client.update_xui_config(new_xui_config)
                    if not await xui_client.login():
                        await callback_query.answer(f"❌ Не удалось переподключиться к {alias}", show_alert=True)
                        return
                    logger.info(f"✅ Переподключено к панели {alias}")
            await _refresh_panel_states_now()
            await _render_servers_screen(callback_query)
            return

        # Проверяем доступность панели
        is_available = await panel_manager.check_panel_status(panel_config)
        if not is_available:
            await callback_query.answer(f"❌ Панель {alias} недоступна", show_alert=True)
            return

        # Переключаемся на новую панель
        if not panel_manager.switch_panel(panel_id):
            await callback_query.answer(f"❌ Ошибка переключения на панель {alias}", show_alert=True)
            return

        new_xui_config = panel_manager.create_xui_config_from_panel(panel_id)
        if not new_xui_config:
            await callback_query.answer(f"❌ Ошибка создания конфигурации для панели {alias}", show_alert=True)
            panel_manager.switch_panel(current_panel_id)
            return

        config.xui = new_xui_config
        xui_client.update_xui_config(new_xui_config)

        if not await xui_client.login():
            # Откатываемся к предыдущей панели
            if current_panel_id:
                panel_manager.switch_panel(current_panel_id)
                old_config = panel_manager.create_xui_config_from_panel(current_panel_id)
                if old_config:
                    config.xui = old_config
                    xui_client.update_xui_config(old_config)
                    await xui_client.login()
            logger.error(f"❌ Не удалось подключиться к панели: {alias}")
            await callback_query.answer(
                f"❌ Не удалось авторизоваться на {alias}. Откат к предыдущей панели.",
                show_alert=True
            )
            await _refresh_panel_states_now()
            await _render_servers_screen(callback_query)
            return

        logger.info(f"✅ Переключено на панель: {alias} (ID: {panel_id})")

        # Извлекаем параметры панели
        try:
            if await panel_manager.fetch_and_update_panel_settings(panel_id, xui_client):
                config.refresh_vpn_config()
                logger.info(f"✅ VPN конфигурация обновлена: transport={config.vpn.transport}, security={config.vpn.security}")
            else:
                logger.warning(f"⚠️ Не удалось обновить параметры панели {alias}")
        except Exception as e:
            logger.error(f"❌ Ошибка извлечения параметров панели: {e}")

        # Показываем обновлённый экран Серверы
        await _refresh_panel_states_now()
        await _render_servers_screen(callback_query)

    except Exception as e:
        logger.error(f"Ошибка подключения к панели: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)



# ─── JSON конфиги выбранной панели ───────────────────────────────────────────

@dp.callback_query(lambda c: c.data and c.data.startswith("panel_json_configs:"))
async def panel_json_configs(callback_query: types.CallbackQuery, state: FSMContext):
    """Отправляет JSON-файлы текущей панели: inbounds, clients, outbound_subs, routing."""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    await callback_query.answer()

    panel_id = callback_query.data.split(":", 1)[1]
    panel_cfg = config.panel_manager.get_panel(panel_id)
    if not panel_cfg:
        await callback_query.message.answer("❌ Панель не найдена")
        return

    version = panel_cfg.xui_version or ""
    is_v3   = version.startswith("3.") or version == "latest"
    if not is_v3:
        await callback_query.message.answer(
            f"⚠️ Панель <b>{panel_cfg.alias}</b> версии v2 — JSON конфиги через API недоступны.",
            parse_mode="HTML"
        )
        return

    await callback_query.message.answer(
        f"⏳ Получаю JSON конфиги панели <b>{panel_cfg.alias}</b>...",
        parse_mode="HTML"
    )

    from backup_manager import (
        _fetch_inbounds, _fetch_clients,
        _fetch_outbound_subs, _fetch_xray_config,
    )
    import json as _json
    from datetime import datetime as _dt

    alias = (panel_cfg.alias or panel_id).strip()
    ts    = _dt.now().strftime("%Y%m%d_%H%M%S")

    files = [
        ("inbounds",      _fetch_inbounds,      f"{alias}_3xui_inbounds_{ts}.json"),
        ("clients",       _fetch_clients,       f"{alias}_3xui_clients_{ts}.json"),
        ("outbound_subs", _fetch_outbound_subs, f"{alias}_3xui_outbound_subs_{ts}.json"),
        ("routing",       None,                 f"{alias}_3xui_routing_{ts}.json"),   # из xray_config
    ]

    sent = 0
    xray_cfg_cache = None

    for key, fetch_fn, filename in files:
        try:
            if key == "routing":
                # routing извлекаем из xray_config (один запрос на оба)
                if xray_cfg_cache is None:
                    xray_cfg_cache = await _fetch_xray_config(panel_cfg)
                data = (xray_cfg_cache or {}).get("routing") if xray_cfg_cache else None
            else:
                data = await fetch_fn(panel_cfg)
                # кэшируем xray_config если он пришёл через inbounds (не нужно, но на всякий)

            if data is None:
                await callback_query.message.answer(f"⚠️ <code>{filename}</code> — не удалось получить", parse_mode="HTML")
                continue

            json_bytes = _json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            await callback_query.message.answer_document(
                types.BufferedInputFile(json_bytes, filename=filename),
                caption=f"📄 <code>{filename}</code>",
                parse_mode="HTML",
            )
            sent += 1

        except Exception as e:
            logger.error(f"[json_configs] {key} error: {e}")
            await callback_query.message.answer(f"❌ Ошибка при получении <code>{key}</code>: {e}", parse_mode="HTML")

    if sent:
        await callback_query.message.answer(
            f"✅ Отправлено файлов: {sent}/4",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад к серверам", callback_data="select_panel_to_connect")
            ]])
        )


# ---------------------------------------------------------------------------
# Конфигурация приложений (apps.yaml) — загружается при старте и при импорте
# ---------------------------------------------------------------------------
import yaml as _yaml
import os as _os

# apps.yaml хранится в /app/data/ — постоянный volume, не перезаписывается при пересборке образа.
# При первом запуске (файл в data/ отсутствует) копируем дефолтный файл из python/.
_APPS_YAML_PATH = "/app/data/apps.yaml"
_APPS_YAML_DEFAULT = _os.path.join(_os.path.dirname(__file__), "apps.yaml")

def _ensure_apps_yaml():
    """Синхронизирует /app/data/apps.yaml с дефолтным если дефолт новее."""
    if not _os.path.exists(_APPS_YAML_DEFAULT):
        return
    try:
        import shutil as _shutil
        _os.makedirs(_os.path.dirname(_APPS_YAML_PATH), exist_ok=True)
        # Перезаписываем если файла нет или дефолт новее
        if (not _os.path.exists(_APPS_YAML_PATH) or
                _os.path.getmtime(_APPS_YAML_DEFAULT) > _os.path.getmtime(_APPS_YAML_PATH)):
            _shutil.copy2(_APPS_YAML_DEFAULT, _APPS_YAML_PATH)
            logger.info(f"📋 apps.yaml обновлён в {_APPS_YAML_PATH}")
    except Exception as e:
        logger.warning(f"Не удалось синхронизировать apps.yaml: {e}")

_ensure_apps_yaml()

def _load_apps_config() -> list:
    """Загружает список платформ из apps.yaml. Возвращает пустой список при ошибке."""
    try:
        path = _APPS_YAML_PATH if _os.path.exists(_APPS_YAML_PATH) else _APPS_YAML_DEFAULT
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f)
        return data.get("platforms", []) if data else []
    except Exception as e:
        logger.error(f"Ошибка загрузки apps.yaml: {e}")
        return []

def _save_apps_config(raw_bytes: bytes) -> list:
    """Валидирует и сохраняет новый apps.yaml. Возвращает список платформ."""
    data = _yaml.safe_load(raw_bytes)
    if not isinstance(data, dict) or "platforms" not in data:
        raise ValueError("Неверный формат файла: отсутствует ключ 'platforms'")
    platforms = data["platforms"]
    if not isinstance(platforms, list) or len(platforms) == 0:
        raise ValueError("Список платформ пуст")
    for p in platforms:
        if "name" not in p or "apps" not in p:
            raise ValueError(f"Платформа без поля 'name' или 'apps': {p}")
        for app in p["apps"]:
            if "name" not in app or "url" not in app:
                raise ValueError(f"Приложение без поля 'name' или 'url': {app}")
    _os.makedirs(_os.path.dirname(_APPS_YAML_PATH), exist_ok=True)
    with open(_APPS_YAML_PATH, "wb") as f:
        f.write(raw_bytes)
    return platforms

# Живой кэш платформ — обновляется при импорте
_apps_platforms: list = _load_apps_config()


# ---------------------------------------------------------------------------
# Меню "Скачать приложение" — динамически из _apps_platforms
# ---------------------------------------------------------------------------

@dp.callback_query(lambda c: c.data == "app_download")
async def show_download_menu(callback_query: types.CallbackQuery):
    """Меню выбора платформы для скачивания приложения."""
    user_id = callback_query.from_user.id
    logger.info(f"[app_download] user={user_id}, allowed={is_allowed(user_id)}, msg_has_photo={bool(callback_query.message.photo)}")
    if not is_allowed(user_id):
        await callback_query.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback_query.answer()
    rows = [
        [InlineKeyboardButton(text=p["name"], callback_data=p.get("callback", f"app_platform_{i}"))]
        for i, p in enumerate(_apps_platforms)
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    text = "📱 <b>Скачать приложение</b>\n\nВыберите платформу:"
    try:
        # Если предыдущее сообщение — фото (например QR-код), edit_text недоступен
        if callback_query.message.photo:
            await callback_query.message.answer(text, parse_mode="HTML", reply_markup=markup)
        else:
            await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        logger.error(f"[app_download] ошибка отправки: {e}")
        await callback_query.message.answer(text, parse_mode="HTML", reply_markup=markup)


@dp.callback_query(lambda c: c.data and (
    c.data.startswith("app_platform_") or c.data in [
        p.get("callback") for p in _apps_platforms if p.get("callback")
    ]
))
async def show_download_platform(callback_query: types.CallbackQuery):
    """Показывает список приложений выбранной платформы."""
    if not is_allowed(callback_query.from_user.id):
        return
    await callback_query.answer()

    data = callback_query.data
    platform = None

    # Ищем по callback или по индексу
    for i, p in enumerate(_apps_platforms):
        if p.get("callback") == data or f"app_platform_{i}" == data:
            platform = p
            break

    if not platform:
        await callback_query.answer("❌ Платформа не найдена", show_alert=True)
        return

    rows = [
        [InlineKeyboardButton(text=app["name"], url=app["url"])]
        for app in platform["apps"]
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="app_download")])
    await callback_query.message.edit_text(
        f"{platform['name']} — <b>выберите приложение:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


# ---------------------------------------------------------------------------
# Настройка приложений (только для администратора)
# ---------------------------------------------------------------------------

@dp.callback_query(lambda c: c.data == "app_settings")
async def show_app_settings(callback_query: types.CallbackQuery, state: FSMContext):
    """Меню настройки приложений."""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    await callback_query.answer()
    platforms_info = "\n".join(
        f"  • {p['name']}: {len(p.get('apps', []))} прил."
        for p in _apps_platforms
    )
    text = (
        "⚙️ <b>Настройка приложений</b>\n\n"
        f"Текущие платформы:\n{platforms_info}\n\n"
        "Экспорт — скачать текущий <code>apps.yaml</code>.\n"
        "Импорт — отправить изменённый файл для обновления кнопок."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Экспорт apps.yaml", callback_data="export_apps_config")],
        [InlineKeyboardButton(text="📥 Импорт apps.yaml", callback_data="import_apps_config")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="server_status")],
    ])
    await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data == "export_apps_config")
async def export_apps_config(callback_query: types.CallbackQuery):
    """Отправляет текущий apps.yaml администратору."""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    await callback_query.answer("⏳ Формирую файл...")
    try:
        with open(_APPS_YAML_PATH, "rb") as f:
            data = f.read()
        await callback_query.message.answer_document(
            document=types.BufferedInputFile(data, filename="apps.yaml"),
            caption=(
                "📤 <b>Текущий apps.yaml</b>\n\n"
                "Отредактируйте файл и импортируйте обратно через\n"
                "<i>Администрирование → Настройка приложений → Импорт</i>"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка экспорта apps.yaml: {e}")
        await callback_query.message.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "import_apps_config")
async def start_import_apps_config(callback_query: types.CallbackQuery, state: FSMContext):
    """Просит администратора отправить файл apps.yaml."""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    await callback_query.answer()
    await state.set_state(AppsImportState.waiting_for_file)
    await callback_query.message.answer(
        "📥 <b>Импорт apps.yaml</b>\n\n"
        "Отправьте файл <code>apps.yaml</code> в этот чат.\n"
        "Для отмены нажмите /start",
        parse_mode="HTML"
    )


async def main():
    logger.info("🚀 Запуск бота...")
    logger.info(f"👑 Администратор: {config.users_db.get_main_admin()}")

    # Версия определяется в install.sh и читается из .env
    logger.info(f"📋 Версия панели: {config.xui.version}")

    # ── Проверка конфигурации Cascade ────────────────────────────────────────
    cascade_servers = config.get_cascade_servers()
    cascade_stubs   = config.get_cascade_stubs()
    if cascade_servers:
        logger.info(f"✅ Cascade: настроено серверов: {len(cascade_servers)}")
    elif cascade_stubs:
        logger.warning(
            f"⚠️  Cascade: секция cascades: присутствует, но не заполнена "
            f"({', '.join(cascade_stubs)}). AWG 3.0 ключи недоступны."
        )
    else:
        logger.info("ℹ️  Cascade: не настроен (AWG 3.0 ключи недоступны)")

    if await xui_client.login():
        logger.info("✅ Подключение к X-UI установлено")
        
        # Извлекаем и обновляем параметры текущей панели из БД
        try:
            current_panel_id = config.panel_manager.get_current_panel_id()
            if current_panel_id:
                logger.info(f"🔄 Обновление параметров панели {current_panel_id} из БД...")
                if await config.panel_manager.fetch_and_update_panel_settings(current_panel_id, xui_client):
                    logger.info(f"✅ Параметры панели {current_panel_id} обновлены из БД")
                    # Обновляем VPN конфигурацию
                    config.refresh_vpn_config()
                    logger.info(f"✅ VPN конфигурация обновлена: transport={config.vpn.transport}, security={config.vpn.security}")
                else:
                    logger.warning(f"⚠️ Не удалось обновить параметры панели {current_panel_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка обновления параметров панели при запуске: {e}")
        
        # ── Уведомление администратору о состоянии Cascade ───────────────────
        if cascade_stubs and not cascade_servers:
            admin_id = config.users_db.get_main_admin()
            if admin_id:
                try:
                    stubs_str = ", ".join(f"<b>{s}</b>" for s in cascade_stubs)
                    await bot.send_message(
                        admin_id,
                        f"⚠️ <b>AWG 3.0 ключи недоступны</b>\n\n"
                        f"Секция <code>cascades:</code> в config.yaml присутствует, "
                        f"но не заполнена ({stubs_str}).\n\n"
                        f"Заполните <code>url</code> и <code>api_key</code> и перезапустите бот.",
                        parse_mode="HTML",
                    )
                except Exception as _e:
                    logger.warning(f"Не удалось отправить уведомление о Cascade: {_e}")

        # Инициализация и запуск мониторинга панелей и системы
        panel_monitor   = None
        system_monitor  = None
        remote_monitor  = None
        panel_monitoring_task   = None
        system_monitoring_task  = None
        remote_monitoring_task  = None

        try:
            # ── Монитор панелей (доступность) ───────────────────────────────
            panel_monitor = PanelMonitor(
                config_manager=config.panel_manager,
                bot=bot,
                admin_ids=config.common.admin_ids
            )
            global _panel_monitor
            _panel_monitor = panel_monitor

            # ── RemoteMonitor — опрашивает ВСЕ панели (panel0 через psutil, остальные через XUI API)
            # SystemMonitor запускается только как fallback если RemoteMonitor недоступен
            if REMOTE_MONITOR_AVAILABLE:
                remote_monitor = RemoteMonitor(
                    config=config,
                    bot=bot,
                    admin_ids=config.common.admin_ids
                )
                global _remote_monitor
                _remote_monitor = remote_monitor
                logger.info("✅ RemoteMonitor будет мониторить все панели (включая panel0)")
            elif SYSTEM_MONITOR_AVAILABLE:
                # Fallback: только если RemoteMonitor недоступен
                system_monitor = SystemMonitor(
                    config=config,
                    bot=bot,
                    admin_ids=config.common.admin_ids,
                    panel_id="panel0"
                )
                logger.info("⚠️ RemoteMonitor недоступен — используется SystemMonitor для panel0")

            # ── Запуск задач ─────────────────────────────────────────────────
            if panel_monitor.enabled:
                logger.info("🔍 Запуск мониторинга доступности панелей...")
                panel_monitoring_task = asyncio.create_task(panel_monitor.start_monitoring())
                logger.info("✅ Мониторинг панелей запущен")
            else:
                logger.info("⏸️ Мониторинг панелей отключён в конфигурации")

            if REMOTE_MONITOR_AVAILABLE and remote_monitor:
                logger.info("🔍 Запуск RemoteMonitor (все панели по кругу)...")
                remote_monitoring_task = asyncio.create_task(remote_monitor.start_monitoring())
                logger.info("✅ RemoteMonitor запущен")
            elif SYSTEM_MONITOR_AVAILABLE and system_monitor:
                logger.info("🔍 Запуск SystemMonitor (fallback, только panel0)...")
                system_monitoring_task = asyncio.create_task(system_monitor.start_monitoring())
                logger.info("✅ SystemMonitor запущен")
            else:
                logger.info("⏸️ Мониторинг ресурсов недоступен")

            # ── Polling ──────────────────────────────────────────────────────
            await dp.start_polling(bot)

        finally:
            async def _stop(name, monitor, task):
                if monitor and task:
                    logger.info(f"🛑 Остановка {name}...")
                    await monitor.stop_monitoring()
                    if not task.done():
                        try:
                            await asyncio.wait_for(task, timeout=5.0)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            task.cancel()
                    logger.info(f"✅ {name} остановлен")

            await _stop("мониторинга панелей", panel_monitor, panel_monitoring_task)
            await _stop("мониторинга системы", system_monitor, system_monitoring_task)
            await _stop("удалённого мониторинга", remote_monitor, remote_monitoring_task)
    else:
        logger.error("❌ Не удалось подключиться к X-UI")
        return


if __name__ == "__main__":
    asyncio.run(main())