import asyncio
import logging
import random
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
    """Компактный список всех панелей: алиас, локация, transport/security, доступность."""
    panels = config.panel_manager.get_all_panels()
    if not panels:
        return ""
    lines = []
    for panel_id, p in panels.items():
        status = "🟢" if get_panel_online_status(panel_id) else "🔴"
        location = p.location_label or ''
        loc_part = f" · <code>{location}</code>" if location else ""
        transport = getattr(p, 'transport', '') or '—'
        security = getattr(p, 'security', '') or '—'
        lines.append(
            f"{status} <b>{p.alias}</b>{loc_part}\n"
            f"   <code>{transport}</code> · <code>{security}</code>"
        )
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
        line = (
            f"{panel_icon} <b>{p.alias}</b>  <code>v{p.xui_version}</code>{location_str}\n"
            f"   {online_icon}  |  🆔 <code>{panel_id}</code>\n"
            f"   🔌 <code>{transport}</code> · <code>{security}</code>"
        )
        if url:
            line += f"\n   <a href=\"{url}\">{url}</a>"
        lines.append(line)
    return "\n".join(lines) + "\n\n"



async def send_panel_select(chat_id: int, header: str, back_callback: str, key_type: str):
    """Отправляет экран выбора панели.
    key_type: 'new' | 'temp'
    """
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
    await state.set_state(NewClientState.waiting_for_comment)
    location = getattr(panel_cfg, 'location_label', '')
    loc_str = f" [{location}]" if location else ""
    await bot.send_message(
        callback_query.message.chat.id,
        f"📝 Введите комментарий к новому бессрочному ключу\n"
        f"📡 Панель: <b>{panel_cfg.alias}{loc_str}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
        ])
    )


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


class NewClientState(StatesGroup):
    waiting_for_panel = State()
    waiting_for_comment = State()


class TempKeyState(StatesGroup):
    waiting_for_panel = State()
    waiting_for_duration = State()
    waiting_for_comment = State()


class AddUserState(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_username = State()


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
        await message.answer("✅ Создание ключа отменено.")

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
            
            # Живая проверка доступности панелей перед показом меню
            await _refresh_panel_states_now()

            # Показываем меню пользователя с кнопками
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="➕ Создать ключ", callback_data="cmd_new"),
                    InlineKeyboardButton(text="⏱ Временный ключ", callback_data="cmd_tempkey")
                ],
                [
                    InlineKeyboardButton(text="🔑 Мои ключи", callback_data="cmd_myclients")
                ]
            ])

            panels_block = _build_panels_block()
            await message.answer(
                f"👤 <b>Пользователь:</b> {username or first_name}\n\n"
                f"{panels_block}",
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
                    InlineKeyboardButton(text="➕ Создать ключ", callback_data="cmd_new"),
                    InlineKeyboardButton(text="⏱ Временный ключ", callback_data="cmd_tempkey")
                ],
                [
                    InlineKeyboardButton(text="🔑 Мои ключи", callback_data="cmd_myclients"),
                    InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_main_menu"),
                ],
                [
                    InlineKeyboardButton(text="⚙️ Администрирование", callback_data="server_status"),
                ]
            ])

            panels_block = _build_panels_block_admin()
            await message.answer(
                f"👑 Администратор\n\n"
                f"{panels_block}"
                f"📱 Выберите действие:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            # Живая проверка доступности панелей перед показом меню
            await _refresh_panel_states_now()

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="➕ Создать ключ", callback_data="cmd_new"),
                    InlineKeyboardButton(text="⏱ Временный ключ", callback_data="cmd_tempkey")
                ],
                [
                    InlineKeyboardButton(text="🔑 Мои ключи", callback_data="cmd_myclients")
                ]
            ])

            panels_block = _build_panels_block()
            await message.answer(
                f"👤 <b>Пользователь:</b> {username or first_name}\n\n"
                f"{panels_block}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    else:
        # Если запрос уже ожидает решения — молчим (не показываем кнопку повторно)
        if config.users_db.has_pending_request(user_id):
            await message.answer(
                "⏳ Ваш запрос на доступ уже отправлен.\n\nОжидайте решения администратора.",
                parse_mode="HTML"
            )
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Запросить доступ", callback_data="request_access")]
        ])
        await message.answer(
            f"👋 Добро пожаловать, {first_name}!\n\n"
            f"Нажмите кнопку ниже, чтобы отправить запрос на доступ.",
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
        await message.answer("⛔ Вы заблокированы администратором.")
        return
    await state.set_state(NewClientState.waiting_for_panel)
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
        await state.set_state(NewClientState.waiting_for_comment)
        await message.answer(
            f"📝 Введите комментарий к новому бессрочному ключу\n"
            f"📡 Панель: <b>{panel_cfg.alias}</b>",
            parse_mode="HTML"
        )
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
    await bot.send_message(
        callback_query.message.chat.id,
        f"⏰ <b>Временный ключ на {duration_text}</b>\n"
        f"📡 Панель: <b>{panel_alias}</b>\n\n"
        f"📝 Введите комментарий к ключу:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
        ])
    )
    await state.set_state(TempKeyState.waiting_for_comment)


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
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
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
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_server_status")
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

    config.users_db.remove_pending_request(user_id)

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
        await callback_query.answer("⏳ Получаю данные...")
    
    try:
        # Получаем статус сервера
        status = await xui_client.get_server_status()
        
        # Форматируем данные
        def format_bytes(bytes_value):
            """Конвертация байтов в читаемый формат"""
            if bytes_value >= 1024**3:  # GB
                return f"{bytes_value / (1024**3):.2f} GB"
            elif bytes_value >= 1024**2:  # MB
                return f"{bytes_value / (1024**2):.2f} MB"
            elif bytes_value >= 1024:  # KB
                return f"{bytes_value / 1024:.2f} KB"
            else:
                return f"{bytes_value} B"
        
        # Получаем данные текущей панели
        current_panel     = config.get_current_panel()
        current_panel_id  = config.panel_manager.get_current_panel_id()
        panel_alias       = getattr(current_panel, 'alias', current_panel_id) or current_panel_id if current_panel else current_panel_id or '—'
        panel_version     = getattr(current_panel, 'xui_version', '') if current_panel else ''
        panel_location    = getattr(current_panel, 'location_label', '') if current_panel else ''
        panel_transport   = (getattr(current_panel, 'transport', '') or '').upper() if current_panel else ''
        panel_security    = (getattr(current_panel, 'security', '')  or '').upper() if current_panel else ''

        # Формируем сообщение
        message = "<b>⚙️ Администрирование</b>\n\n"

        # Блок текущей панели
        message += f"📡 <b>{panel_alias}</b>"
        if panel_version:
            message += f"  <code>v{panel_version}</code>"
        message += "\n"
        if current_panel_id:
            message += f"🆔 <code>{current_panel_id}</code>\n"
        if panel_location:
            message += f"📍 {panel_location}\n"
        if panel_transport or panel_security:
            message += f"🔌 <code>{panel_transport}</code> · <code>{panel_security}</code>\n"
        message += "\n"

        if status:
            # CPU
            cpu = status.get('cpu', 0)
            
            # Memory
            mem = status.get('mem', {})
            mem_current = mem.get('current', 0)
            mem_total = mem.get('total', 1)
            mem_percent = (mem_current / mem_total * 100) if mem_total > 0 else 0
            
            # Disk
            disk = status.get('disk', {})
            disk_current = disk.get('current', 0)
            disk_total = disk.get('total', 1)
            disk_percent = (disk_current / disk_total * 100) if disk_total > 0 else 0
            
            # Network
            net_io = status.get('netIO', {})
            net_up = net_io.get('up', 0)
            net_down = net_io.get('down', 0)
            
            # Xray
            xray = status.get('xray', {})
            xray_state = xray.get('state', 'unknown')
            xray_version = xray.get('version', 'unknown')
            
            # TCP connections
            tcp_count = status.get('tcpCount', 0)
            
            # Получаем общий трафик всех клиентов
            try:
                all_clients = await xui_client.get_all_clients()
                total_traffic_up = sum(c.get('up', 0) for c in all_clients)
                total_traffic_down = sum(c.get('down', 0) for c in all_clients)
            except Exception as e:
                logger.error(f"Ошибка получения трафика клиентов: {e}")
                total_traffic_up = 0
                total_traffic_down = 0

            xray_emoji = "✅" if xray_state == "running" else "❌"
            message += (
                f"💻 <b>CPU:</b> {cpu:.1f}%\n"
                f"🧠 <b>RAM:</b> {mem_percent:.1f}% | {format_bytes(mem_current)} / {format_bytes(mem_total)}\n"
                f"💿 <b>Диск:</b> {disk_percent:.1f}% | {format_bytes(disk_current)} / {format_bytes(disk_total)}\n"
                f"🌐 <b>Сеть:</b> ⬆️ {format_bytes(net_up)} ⬇️ {format_bytes(net_down)}\n"
                f"📊 <b>Трафик:</b> ⬆️ {format_bytes(total_traffic_up)} ⬇️ {format_bytes(total_traffic_down)}\n"
                f"🔐 <b>Xray:</b> {xray_emoji} {xray_state} <code>v{xray_version}</code>\n"
                f"🔌 <b>TCP:</b> {tcp_count}"
            )
        
        
        # Добавляем кнопки в два ряда
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_server_status"),
                InlineKeyboardButton(text="💾 Бэкап", callback_data="create_backup")
            ],
            [
                InlineKeyboardButton(text="🔔 Уведомления", callback_data="notification_settings"),
                InlineKeyboardButton(text="📥 JSON конфиг", callback_data="export_json_config")
            ],
            [
                InlineKeyboardButton(text="📋 Все ключи", callback_data="cmd_allclients"),
                InlineKeyboardButton(text="👥 Пользователи", callback_data="show_users")
            ],
            [
                InlineKeyboardButton(text="🖥️ Серверы", callback_data="select_panel_to_connect")
            ],
            [
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
    """Создать бэкап базы данных"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    await callback_query.answer("⏳ Создаю бэкап...")
    
    try:
        # Скачиваем бэкап
        backup_data = await xui_client.download_backup()
        
        if not backup_data:
            await callback_query.message.answer("❌ Не удалось создать бэкап")
            return
        
        # Создаем файл для отправки
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"x-ui_backup_{timestamp}.db"
        
        # Отправляем файл пользователю
        backup_file = types.BufferedInputFile(backup_data, filename=filename)
        await callback_query.message.answer_document(
            backup_file,
            caption=f"✅ Бэкап базы данных создан\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        logger.info(f"Бэкап создан администратором {callback_query.from_user.id}")
        
    except Exception as e:
        logger.error(f"Ошибка создания бэкапа: {e}")
        await callback_query.message.answer(f"❌ Ошибка: {str(e)}")


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

        alerts_on = sum([
            settings.get('cpu_alert',  False),
            settings.get('ram_alert',  False),
            settings.get('disk_alert', False),
        ])
        monitor_icon = "🔔" if alerts_on > 0 else ""

        location = getattr(panel_cfg, 'location_label', '') or ''
        loc_part = f" · {location}" if location else ""
        btn_text = f"{status_icon}{monitor_icon} {alias}{loc_part}"
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
                [InlineKeyboardButton(text="🔙 Назад", callback_data="show_users")]
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
        buttons.append([InlineKeyboardButton(
            text=f"🕐 {label}",
            callback_data=f"pending_card_{uid}"
        )])

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
    """Выполняет живую проверку всех панелей и обновляет кэш panel_states монитора.
    Вызывается при нажатии кнопки 🔄 Обновить — чтобы статус был реальным, а не устаревшим."""
    if _panel_monitor is None:
        return
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
                InlineKeyboardButton(text="➕ Создать ключ", callback_data="cmd_new"),
                InlineKeyboardButton(text="⏱ Временный ключ", callback_data="cmd_tempkey")
            ],
            [
                InlineKeyboardButton(text="🔑 Мои ключи", callback_data="cmd_myclients"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_main_menu"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Администрирование", callback_data="server_status"),
            ]
        ])
        panels_block = _build_panels_block_admin()
        text = f"👑 Администратор\n\n{panels_block}📱 Выберите действие:"
        if edit:
            await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_message(callback_query.message.chat.id, text, parse_mode="HTML", reply_markup=keyboard)
    else:
        # Живая проверка доступности панелей перед показом меню
        await _refresh_panel_states_now()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Создать ключ", callback_data="cmd_new"),
                InlineKeyboardButton(text="⏱ Временный ключ", callback_data="cmd_tempkey")
            ],
            [
                InlineKeyboardButton(text="🔑 Мои ключи", callback_data="cmd_myclients")
            ]
        ])
        panels_block = _build_panels_block()
        text = (
            f"👤 <b>Пользователь:</b> {username or first_name}\n\n"
            f"{panels_block}"
        )
        if edit:
            await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_message(callback_query.message.chat.id, text, parse_mode="HTML", reply_markup=keyboard)
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
        await state.set_state(NewClientState.waiting_for_comment)
        await bot.send_message(
            callback_query.message.chat.id,
            f"📝 Введите комментарий к новому бессрочному ключу\n"
            f"📡 Панель: <b>{panel_cfg.alias}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ])
        )
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
                await bot.send_message(user_id, "⛔ Ваш доступ отозван администратором.")
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

            text += f"{panel_icon} <b>{alias}</b>  <code>v{version}</code>{location_str}\n"
            text += f"   {online_icon}  |  🆔 <code>{panel_id}</code>\n"
            text += f"   🔌 <code>{transport}</code> · <code>{security}</code>\n"
            if panel_config.xui_url:
                text += f"   <code>{panel_config.xui_url}</code>\n"
            text += "\n"
        
        # Кнопки управления
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_panels"),
                InlineKeyboardButton(text="🖥️ Серверы", callback_data="select_panel_to_connect")
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


async def _render_servers_screen(callback_query: types.CallbackQuery, is_refresh: bool = False):
    """Рендер экрана 'Серверы': кнопки панелей + статистика сбоев."""
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
        InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_servers"),
        InlineKeyboardButton(text="◀️ Назад",    callback_data="server_status"),
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    # Статистика сбоев
    stats_lines = []
    if _panel_monitor:
        for pid, panel_config in panels.items():
            alias = getattr(panel_config, 'alias', pid)
            ps = _panel_monitor.panel_states.get(pid)
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

    if stats_lines:
        stats_block = "📊 <b>Статистика сбоев:</b>\n" + "\n".join(stats_lines)
    else:
        stats_block = "📊 Статистика сбоев пока недоступна"

    text = f"🖥️ <b>Серверы</b>\n\n{stats_block}"
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

    try:
        await _render_servers_screen(callback_query, is_refresh=True)
    except Exception as e:
        logger.error(f"Ошибка обновления экрана серверов: {e}")
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
        
        # Если это текущая панель, проверяем подключение и показываем статистику
        if panel_id == current_panel_id:
            await callback_query.message.edit_text(
                f"🔄 Проверка подключения к панели <b>{alias}</b>...",
                parse_mode="HTML"
            )
            
            # Проверяем, что бот действительно подключен к этой панели
            # Сравниваем URL из config с URL из panel_config
            panel_url = getattr(panel_config, 'xui_url', '') or getattr(panel_config, 'url', '')
            current_url = config.xui.url
            
            if panel_url != current_url:
                # URL не совпадают - нужно переподключиться
                logger.warning(f"⚠️ URL не совпадают! Panel: {panel_url}, Current: {current_url}")
                logger.info(f"🔄 Переподключение к панели {alias}...")
                
                # Создаем новый XUIConfig из панели
                new_xui_config = panel_manager.create_xui_config_from_panel(panel_id)
                if new_xui_config:
                    config.xui = new_xui_config
                    xui_client.update_xui_config(new_xui_config)
                    
                    # Переподключаемся
                    if not await xui_client.login():
                        await callback_query.message.edit_text(
                            f"❌ <b>Ошибка переподключения к панели {alias}</b>\n\n"
                            "Не удалось авторизоваться.",
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="◀️ Назад", callback_data="server_status")]
                            ])
                        )
                        return
                    
                    logger.info(f"✅ Переподключено к панели {alias}")
            
            await callback_query.message.edit_text(
                f"🔄 Получение статистики панели <b>{alias}</b>...",
                parse_mode="HTML"
            )
            
            try:
                all_clients = await xui_client.get_all_clients()
                
                total_clients = len(all_clients)
                active_clients = sum(1 for c in all_clients if c.get('enable', False))
                inactive_clients = total_clients - active_clients
                
                # Получаем количество онлайн клиентов
                online_clients = await xui_client.get_online_clients_count()
                
                # Подсчет трафика
                total_traffic_up = sum(c.get('up', 0) for c in all_clients)
                total_traffic_down = sum(c.get('down', 0) for c in all_clients)
                total_traffic = total_traffic_up + total_traffic_down
                
                def format_bytes(bytes_val):
                    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                        if bytes_val < 1024.0:
                            return f"{bytes_val:.2f} {unit}"
                        bytes_val /= 1024.0
                    return f"{bytes_val:.2f} PB"
                
                location = getattr(panel_config, 'location_label', '')
                location_line = f"• Местонахождение: <b>{location}</b>\n" if location else ""
                stats_text = (
                    f"🟢 <b>Текущая панель: {alias}</b>\n\n"
                    f"🔐 <b>Информация о панели:</b>\n"
                    f"{location_line}"
                    f"• URL: <code>{getattr(panel_config, 'xui_url', 'N/A') or getattr(panel_config, 'url', 'N/A')}</code>\n"
                    f"• Версия: <code>{getattr(panel_config, 'xui_version', 'N/A') or getattr(panel_config, 'version', 'N/A')}</code>\n"
                    f"• Inbound ID: <code>{getattr(panel_config, 'inbound_id', 'N/A')}</code>\n\n"
                    f"📊 <b>Статистика ключей:</b>\n"
                    f"• Всего ключей: <b>{total_clients}</b>\n"
                    f"• Активных: <b>{active_clients}</b> \n"
                    f"• Неактивных: <b>{inactive_clients}</b> \n"
                    f"• 🟢 Онлайн: <b>{online_clients}</b>\n\n"
                    f"📈 <b>Трафик:</b>\n"
                    f"• Загружено: <code>{format_bytes(total_traffic_up)}</code>\n"
                    f"• Скачано: <code>{format_bytes(total_traffic_down)}</code>\n"
                    f"• Всего: <code>{format_bytes(total_traffic)}</code>"
                )
            except Exception as e:
                logger.error(f"Ошибка получения статистики: {e}")
                location_line2 = f"📍 Местонахождение: <b>{location}</b>\n" if location else ""
                stats_text = (
                    f"🟢 <b>Текущая панель: {alias}</b>\n\n"
                    f"{location_line2}"
                    f"🔐 URL: <code>{getattr(panel_config, 'xui_url', 'N/A') or getattr(panel_config, 'url', 'N/A')}</code>\n"
                    f"📋 Версия: <code>{getattr(panel_config, 'xui_version', 'N/A') or getattr(panel_config, 'version', 'N/A')}</code>\n"
                    f"🆔 Inbound ID: <code>{getattr(panel_config, 'inbound_id', 'N/A')}</code>\n\n"
                    f"⚠️ Не удалось получить статистику ключей"
                )
            
            await callback_query.message.edit_text(
                stats_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="server_status")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_start")]
                ])
            )
            return
        
        await callback_query.message.edit_text(
            f"🔄 Подключение к панели <b>{alias}</b>...\n\n"
            "⏳ Проверка доступности...",
            parse_mode="HTML"
        )
        
        # Проверяем доступность панели
        is_available = await panel_manager.check_panel_status(panel_config)
        
        if not is_available:
            await callback_query.message.edit_text(
                f"❌ <b>Панель {alias} недоступна</b>\n\n"
                "Проверьте настройки подключения и доступность сервера.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="server_status")]
                ])
            )
            return
        
        # Переключаемся на новую панель
        if panel_manager.switch_panel(panel_id):
            # Создаем новый XUIConfig из панели
            new_xui_config = panel_manager.create_xui_config_from_panel(panel_id)
            
            if new_xui_config:
                # Обновляем конфигурацию в config
                config.xui = new_xui_config
                
                # Обновляем XUIClient
                xui_client.update_xui_config(new_xui_config)
                
                # Пытаемся подключиться к новой панели
                await callback_query.message.edit_text(
                    f"🔄 Подключение к панели <b>{alias}</b>...\n\n"
                    "⏳ Авторизация...",
                    parse_mode="HTML"
                )
                
                if await xui_client.login():
                    logger.info(f"✅ Переключено на панель: {alias} (ID: {panel_id})")
                    
                    # Извлекаем и сохраняем параметры панели
                    await callback_query.message.edit_text(
                        f"🔄 Подключение к панели <b>{alias}</b>...\n\n"
                        "⏳ Извлечение параметров панели...",
                        parse_mode="HTML"
                    )
                    
                    try:
                        if await panel_manager.fetch_and_update_panel_settings(panel_id, xui_client):
                            logger.info(f"✅ Параметры панели {alias} обновлены")
                            # Обновляем VPN конфигурацию в config
                            config.refresh_vpn_config()
                            logger.info(f"✅ VPN конфигурация обновлена: transport={config.vpn.transport}, security={config.vpn.security}")
                        else:
                            logger.warning(f"⚠️ Не удалось обновить параметры панели {alias}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка извлечения параметров панели: {e}")
                    
                    # Получаем статистику по ключам
                    try:
                        all_clients = await xui_client.get_all_clients()
                        
                        total_clients = len(all_clients)
                        active_clients = sum(1 for c in all_clients if c.get('enable', False))
                        inactive_clients = total_clients - active_clients
                        
                        # Получаем количество онлайн клиентов
                        online_clients = await xui_client.get_online_clients_count()
                        
                        # Подсчет трафика
                        total_traffic_up = sum(c.get('up', 0) for c in all_clients)
                        total_traffic_down = sum(c.get('down', 0) for c in all_clients)
                        total_traffic = total_traffic_up + total_traffic_down
                        
                        def format_bytes(bytes_val):
                            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                                if bytes_val < 1024.0:
                                    return f"{bytes_val:.2f} {unit}"
                                bytes_val /= 1024.0
                            return f"{bytes_val:.2f} PB"
                        
                        loc = getattr(panel_config, 'location_label', '')
                        loc_line = f"• Местонахождение: <b>{loc}</b>\n" if loc else ""
                        stats_text = (
                            f"✅ <b>Успешно подключено к панели {alias}</b>\n\n"
                            f"🔐 <b>Информация о панели:</b>\n"
                            f"{loc_line}"
                            f"• URL: <code>{new_xui_config.url}</code>\n"
                            f"• Версия: <code>{new_xui_config.version}</code>\n"
                            f"• Inbound ID: <code>{new_xui_config.inbound_id}</code>\n\n"
                            f"📊 <b>Статистика ключей:</b>\n"
                            f"• Всего ключей: <b>{total_clients}</b>\n"
                            f"• Активных: <b>{active_clients}</b> \n"
                            f"• Неактивных: <b>{inactive_clients}</b> \n"
                            f"• 🟢 Онлайн: <b>{online_clients}</b>\n\n"
                            f"📈 <b>Трафик:</b>\n"
                            f"• Загружено: <code>{format_bytes(total_traffic_up)}</code>\n"
                            f"• Скачано: <code>{format_bytes(total_traffic_down)}</code>\n"
                            f"• Всего: <code>{format_bytes(total_traffic)}</code>"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка получения статистики: {e}")
                        loc_line2 = f"📍 Местонахождение: <b>{loc}</b>\n" if loc else ""
                        stats_text = (
                            f"✅ <b>Успешно подключено к панели {alias}</b>\n\n"
                            f"{loc_line2}"
                            f"🔐 URL: <code>{new_xui_config.url}</code>\n"
                            f"📋 Версия: <code>{new_xui_config.version}</code>\n"
                            f"🆔 Inbound ID: <code>{new_xui_config.inbound_id}</code>\n\n"
                            f"⚠️ Не удалось получить статистику ключей"
                        )
                    
                    await callback_query.message.edit_text(
                        stats_text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="◀️ Назад", callback_data="server_status")],
                            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_start")]
                        ])
                    )
                else:
                    # Откатываемся к предыдущей панели
                    if current_panel_id:
                        panel_manager.switch_panel(current_panel_id)
                        old_config = panel_manager.create_xui_config_from_panel(current_panel_id)
                        if old_config:
                            config.xui = old_config
                            xui_client.update_xui_config(old_config)
                            await xui_client.login()
                    
                    logger.error(f"❌ Не удалось подключиться к панели: {alias}")
                    
                    await callback_query.message.edit_text(
                        f"❌ <b>Ошибка подключения к панели {alias}</b>\n\n"
                        "Не удалось авторизоваться. Проверьте учетные данные.\n"
                        "Возвращено подключение к предыдущей панели.",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="◀️ Назад", callback_data="server_status")]
                        ])
                    )
            else:
                await callback_query.message.edit_text(
                    f"❌ Ошибка создания конфигурации для панели {alias}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="server_status")]
                    ])
                )
        else:
            await callback_query.message.edit_text(
                f"❌ Ошибка переключения на панель {alias}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="server_status")]
                ])
            )
        
    except Exception as e:
        logger.error(f"Ошибка подключения к панели: {e}")
        await callback_query.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="server_status")]
            ])
        )



async def main():
    logger.info("🚀 Запуск бота...")
    logger.info(f"👑 Администратор: {config.users_db.get_main_admin()}")

    # Версия определяется в install.sh и читается из .env
    logger.info(f"📋 Версия панели: {config.xui.version}")

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