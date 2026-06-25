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
import sqlite3
from config import config
from utils import XUIClient, generate_vless_link, get_client_link, setup_logging

setup_logging(config.logging)
logger = logging.getLogger(__name__)

bot = Bot(token=config.bot.token)
dp = Dispatcher()

xui_client = XUIClient(config)


class NewClientState(StatesGroup):
    waiting_for_comment = State()


class TempKeyState(StatesGroup):
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
        # Проверяем есть ли у пользователя активные ключи в X-UI
        has_keys = await xui_client.has_active_keys(username)
        
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
                    InlineKeyboardButton(text="➕ Создать ключ", callback_data="cmd_new"),
                    InlineKeyboardButton(text="⏱ Временный ключ", callback_data="cmd_tempkey")
                ],
                [
                    InlineKeyboardButton(text="🔑 Мои ключи", callback_data="cmd_myclients")
                ]
            ])
            
            # Получаем информацию о текущей панели
            current_panel = config.get_current_panel()
            panel_alias = current_panel.alias if current_panel else "N/A"
            
            await message.answer(
                f"👤 Добро пожаловать, {first_name}!\n\n"
                f"✅ <b>У вас обнаружены активные ключи.</b>\n"
                f"Доступ предоставлен автоматически.\n"
                f"📡 <b>Панель:</b> <code>{panel_alias}</code>\n\n"
                f"🔐 <b>Настройки подключения:</b>\n"
                f"• Transport: <code>{config.vpn.transport}</code>\n"
                f"• Security: <code>{config.vpn.security}</code>\n\n"
                f"📱 Выберите действие:",
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
                    InlineKeyboardButton(text="📋 Все ключи", callback_data="cmd_allclients")
                ],
                [
                    InlineKeyboardButton(text="🖥️ Сервер", callback_data="server_status"),
                    InlineKeyboardButton(text="👥 Пользователи", callback_data="show_users")
                ],
                [
                    InlineKeyboardButton(text="🔧 Панели", callback_data="show_panels")
                ]
            ])
            
            # Получаем информацию о текущей панели
            current_panel = config.get_current_panel()
            panel_info = ""
            
            # Используем актуальные данные из config.vpn (обновляются через refresh_vpn_config)
            transport = config.vpn.transport if hasattr(config, 'vpn') and config.vpn else "N/A"
            security = config.vpn.security if hasattr(config, 'vpn') and config.vpn else "N/A"
            
            if current_panel:
                alias = current_panel.alias
                is_local = current_panel.is_local
                xui_version = current_panel.xui_version
                xui_url = current_panel.xui_url
                
                panel_info = (
                    f"\n📋 <b>Панель:</b>\n"
                    f"• Alias: <code>{alias}</code>\n"
                    f"• Local: <code>{'Да' if is_local else 'Нет'}</code>\n"
                    f"• Version: <code>{xui_version}</code>\n"
                    f"• URL: <code>{xui_url}</code>\n"
                )
            
            await message.answer(
                f"👑 Администратор\n {username or first_name}\n\n"
                f"🔐 <b>Настройки подключения:</b>\n"
                f"• Transport: <code>{transport}</code>\n"
                f"• Security: <code>{security}</code>"
                f"{panel_info}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="➕ Создать ключ", callback_data="cmd_new"),
                    InlineKeyboardButton(text="⏱ Временный ключ", callback_data="cmd_tempkey")
                ],
                [
                    InlineKeyboardButton(text="🔑 Мои ключи", callback_data="cmd_myclients")
                ]
            ])
            
            # Получаем информацию о текущей панели
            current_panel = config.get_current_panel()
            panel_alias = current_panel.alias if current_panel else "N/A"
            
            await message.answer(
                f"👤 <b>Пользователь:</b> {username or first_name}\n"
                f"📡 <b>Панель:</b> <code>{panel_alias}</code>\n\n"
                f"🔐 <b>Настройки подключения:</b>\n"
                f"• Transport: <code>{config.vpn.transport}</code>\n"
                f"• Security: <code>{config.vpn.security}</code>\n\n"
                f"📱 Выберите действие:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    else:
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
        await message.answer("⛔ Доступ запрещен. Пожалуйста, сначала выполните /start")
        return

    if is_blocked_by_admin(message.from_user.id):
        await message.answer("⛔ Вы заблокированы администратором.")
        return

    await message.answer(
        "📝 Введите комментарий к подключению:\n\n",
        parse_mode="HTML"
    )
    await state.set_state(NewClientState.waiting_for_comment)


@dp.message(NewClientState.waiting_for_comment)
async def process_new_comment(message: Message, state: FSMContext):
    comment = message.text.strip()

    # Проверка на недопустимые символы
    if comment.startswith('/'):
        await message.answer(
            "❌ Недопустимый символ! Комментарий не может начинаться с '/'. Пожалуйста, введите комментарий заново либо вернитесь в главное меню /start")
        return

    if len(comment) > 50:
        await message.answer("❌ Комментарий слишком длинный (максимум 50 символов). Попробуйте снова:")
        return

    await state.update_data(comment=comment)

    username = message.from_user.username
    if not username:
        username = message.from_user.first_name.lower().replace(" ", "_")

    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    email = f"{username}_{random_suffix}"

    status_msg = await message.answer(f"🔄 Ожидайте...")

    result = await xui_client.add_client(email, 0, 3650, comment)

    if result['success']:
        # Универсальная генерация ссылки для v2 и v3
        vless_link = await get_client_link(xui_client, email, result['uuid'], config.vpn, config.xui.inbound_id)
        if not vless_link:
            await status_msg.edit_text(f"❌ Ошибка получения ссылки")
            await state.clear()
            return

        # Удаляем сообщение о создании
        await bot.delete_message(message.chat.id, status_msg.message_id)
        
        # Отправляем информацию с кнопками
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔑 Показать ключ", callback_data=f"showlink_{result['uuid']}"),
                InlineKeyboardButton(text="📱 Показать QR", callback_data=f"showqr_{result['uuid']}")
            ],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")]
        ])
        
        await message.answer(
            f"🔑 <b>Бессрочный ключ</b>\n\n"
            f"📝 Комментарий: {comment}",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    else:
        await status_msg.edit_text(f"❌ Ошибка: {result.get('error')}")

    await state.clear()


@dp.message(Command("tempkey"))
async def cmd_temp_key(message: Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Доступ запрещен. Пожалуйста, сначала выполните /start")
        return

    if is_blocked_by_admin(message.from_user.id):
        await message.answer("⛔ Вы заблокированы администратором.")
        return

    # Показываем меню выбора срока
    buttons = [
        [InlineKeyboardButton(text="🕐 1 час", callback_data="tempkey_1h")],
        [InlineKeyboardButton(text="📅 1 день", callback_data="tempkey_1d")],
        [InlineKeyboardButton(text="📅 3 дня", callback_data="tempkey_3d")],
        [InlineKeyboardButton(text="📅 7 дней", callback_data="tempkey_7d")],
        [InlineKeyboardButton(text="📅 30 дней", callback_data="tempkey_30d")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await message.answer(
        "⏰ <b>Создание временного ключа</b>\n\n"
        "Выберите срок действия ключа:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@dp.callback_query(lambda c: c.data and c.data.startswith('tempkey_') and not c.data.startswith('tempkey_comment_'))
async def process_tempkey_duration(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработка выбора срока для временного ключа"""
    duration = callback_query.data.split('_')[1]  # 1h, 1d, 3d, 7d, 30d
    
    # Сохраняем выбранный срок
    await state.update_data(temp_duration=duration)
    
    # Определяем текст срока
    duration_map = {
        '1h': '1 час',
        '1d': '1 день',
        '3d': '3 дня',
        '7d': '7 дней',
        '30d': '30 дней'
    }
    duration_text = duration_map.get(duration, '1 день')
    
    await callback_query.message.edit_text(
        f"⏰ <b>Временный ключ на {duration_text}</b>\n\n"
        f"📝 Введите комментарий к ключу (например: 'Для телефона', 'Тестовый' и т.д.):\n\n"
        f"Вернуться в главное меню /start",
        parse_mode="HTML"
    )
    await state.set_state(TempKeyState.waiting_for_comment)
    await callback_query.answer()


@dp.message(TempKeyState.waiting_for_comment)
async def process_tempkey_comment(message: Message, state: FSMContext):
    comment = message.text.strip()

    # Проверка на недопустимые символы
    if comment.startswith('/'):
        await message.answer(
            "❌ Недопустимый символ! Комментарий не может начинаться с '/'. Пожалуйста, введите комментарий заново либо вернитесь в главное меню /start")
        return

    if len(comment) > 50:
        await message.answer("❌ Комментарий слишком длинный (максимум 50 символов). Попробуйте снова:")
        return

    # Получаем сохраненный срок
    data = await state.get_data()
    duration = data.get('temp_duration', '1d')
    
    # Определяем количество дней
    duration_map = {
        '1h': (1/24, '1 час'),
        '1d': (1, '1 день'),
        '3d': (3, '3 дня'),
        '7d': (7, '7 дней'),
        '30d': (30, '30 дней')
    }
    days, duration_text = duration_map.get(duration, (1, '1 день'))

    username = message.from_user.username
    if not username:
        username = message.from_user.first_name.lower().replace(" ", "_")

    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    email = f"temp_{username}_{random_suffix}"

    status_msg = await message.answer(f"🔄 Создаю временный ключ на {duration_text}...")

    result = await xui_client.add_client(email, 0, days, f"{comment} (Временный {duration_text})")

    if result['success']:
        vless_link = await get_client_link(xui_client, email, result['uuid'], config.vpn, config.xui.inbound_id)
        if not vless_link:
            await status_msg.edit_text(f"❌ Ошибка получения ссылки")
            return

        # Удаляем сообщение о создании
        await bot.delete_message(message.chat.id, status_msg.message_id)
        
        # Отправляем информацию с кнопками
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔑 Показать ключ", callback_data=f"showlink_{result['uuid']}"),
                InlineKeyboardButton(text="📱 Показать QR", callback_data=f"showqr_{result['uuid']}")
            ],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_start")]
        ])
        
        await message.answer(
            f"⏰ <b>Временный ключ на {duration_text}</b>\n\n"
            f"📝 Комментарий: {comment}",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    else:
        await status_msg.edit_text(f"❌ Ошибка: {result.get('error')}")

    await state.clear()


@dp.message(Command("myclients"))
async def cmd_my_clients(message: Message):
    if not is_allowed(message.from_user.id):
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
            display_text = f"{comment[:25]}"
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
    
    await callback_query.message.edit_text(
        f"🔑 <b>Информация о ключе</b>\n\n"
        f"Статус: {status_text}\n"
        f"📝 Комментарий: {comment if comment else 'Без комментария'}",
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
        
        # Подсчитываем статистику
        total_count = len(all_clients)
        active_count = sum(1 for c in all_clients if c['status'] == 'active')
        inactive_count = sum(1 for c in all_clients if c['status'] == 'inactive')
        expired_count = sum(1 for c in all_clients if c['status'] == 'expired')
        
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
            
            # Подсчитываем трафик клиента
            client_traffic = client.get('up', 0) + client.get('down', 0)
            traffic_mb = client_traffic / (1024**2)  # Переводим в MB
            
            # Формируем текст кнопки (короче для двух колонок)
            if comment:
                button_text = f"{email[:10]}-{comment[:10]}"
            else:
                button_text = email[:20]
            
            # Добавляем расход трафика
            if traffic_mb >= 1:
                button_text += f" ({traffic_mb:.0f}MB)"
            
            # Добавляем иконку статуса
            if client['status'] == 'active':
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
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")
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
        text += f"📧 <b>Email:</b> <code>{client['email']}</code>\n"
        text += f"📝 <b>Комментарий:</b> {client['comment'] if client['comment'] else 'Не указан'}\n"
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
        text += f"📧 <b>Email:</b> <code>{client['email']}</code>\n"
        text += f"📝 <b>Комментарий:</b> {client['comment'] if client['comment'] else 'Не указан'}\n"
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
        caption = f"""📱 <b>QR-код для подключения</b>

🔑 <b>VLESS-ссылка:</b>
<code>{vless_link}</code>

💬 <b>Комментарий:</b> {comment}"""
        
        # Добавляем кнопку "В главное меню"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
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
        
        # Перенаправляем на back_to_allclients для отображения обновленных данных
        await back_to_allclients(callback_query)
        
    except Exception as e:
        logger.error(f"Ошибка обновления списка ключей: {e}")
        # Проверяем, не является ли ошибка "message is not modified"
        if "message is not modified" in str(e):
            await callback_query.answer("✅ Данные актуальны", show_alert=False)
        else:
            await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)



@dp.callback_query(lambda c: c.data == "back_to_allclients")
async def back_to_allclients(callback_query: types.CallbackQuery):
    """Вернуться к списку всех ключей"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    try:
        import time
        user_id = callback_query.from_user.id
        
        # Проверяем кеш
        should_refresh = True
        all_clients = []
        if user_id in allclients_cache:
            cache_time = allclients_cache[user_id]['time']
            if time.time() - cache_time < 30:  # Кеш действителен 30 секунд
                should_refresh = False
                all_clients = allclients_cache[user_id]['data']
        
        # Обновляем данные если нужно
        if should_refresh:
            all_clients = await xui_client.get_all_clients()
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
            text = panel_info
            text += f"🔑 Всего ключей: 0\n"
            text += f"✅ Активных: 0\n"
            text += f"⏸️ Неактивных: 0\n"
            text += f"⏰ Просроченных: 0\n"
            text += f"📊 Расход трафика: 0 B\n\n"
            text += "📭 <i>Нет ключей в системе</i>"
            
            # Добавляем кнопки "Обновить" и "Назад"
            buttons = [
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_allclients")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ]
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            await callback_query.answer()
            return
        
        # Подсчитываем статистику для существующих ключей
        total_count = len(all_clients)
        active_count = sum(1 for c in all_clients if c['status'] == 'active')
        inactive_count = sum(1 for c in all_clients if c['status'] == 'inactive')
        expired_count = sum(1 for c in all_clients if c['status'] == 'expired')
        
        
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
        text = panel_info
        text += f"🔑 Всего ключей: {total_count}\n"
        text += f"✅ Активных: {active_count}\n"
        text += f"⏸️ Неактивных: {inactive_count}\n"
        text += f"⏰ Просроченных: {expired_count}\n"
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
            
            # Подсчитываем трафик клиента
            client_traffic = client.get('up', 0) + client.get('down', 0)
            traffic_mb = client_traffic / (1024**2)  # Переводим в MB
            
            # Формируем текст кнопки (короче для двух колонок)
            if comment:
                button_text = f"{email[:10]}-{comment[:10]}"
            else:
                button_text = email[:20]
            
            # Добавляем расход трафика
            if traffic_mb >= 1:
                button_text += f" ({traffic_mb:.0f}MB)"
            
            # Добавляем иконку статуса
            if client['status'] == 'active':
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
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Ошибка возврата к списку: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not is_allowed(message.from_user.id):
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
        
        await callback_query.message.edit_text(result_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка при очистке: {e}")
        await callback_query.message.edit_text(f"❌ Ошибка при очистке: {str(e)}")
    
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
                f"📧 Email: {email}\n"
                f"🆔 UUID: <code>{result['uuid']}</code>",
                parse_mode="HTML"
            )
        except Exception as e:
            await callback_query.message.edit_text(
                f"⚠️ Ключ создан, но не удалось отправить пользователю {user_info}.\n\n"
                f"Возможно, пользователь заблокировал бота.\n\n"
                f"📧 Email: {email}\n"
                f"🆔 UUID: <code>{result['uuid']}</code>",
                parse_mode="HTML"
            )
    else:
        await callback_query.message.edit_text(
            f"❌ Ошибка при создании ключа: {result.get('error')}"
        )

    await callback_query.answer()


@dp.message()
async def handle_unknown(message: Message):
    user_id = message.from_user.id

    if is_blocked_by_admin(user_id):
        await message.answer("⛔ Вы заблокированы администратором. Обратитесь к администратору.")
        return

    if is_flood_blocked(user_id):
        await message.answer("⛔ Вы временно заблокированы за флуд. Попробуйте позже.")
        return

    if not is_allowed(user_id):
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
async def show_server_status(callback_query: types.CallbackQuery, state: FSMContext):
    """Показать состояние сервера (только для администратора)"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    # Очищаем состояние при открытии нового окна
    await state.clear()
    
    await callback_query.answer("⏳ Получаю данные...")
    
    try:
        # Получаем статус сервера
        status = await xui_client.get_server_status()
        
        if not status:
            await callback_query.message.answer("❌ Не удалось получить статус сервера")
            return
        
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
        
        # Формируем сообщение
        message = "🖥️ <b>Сервер</b>\n\n"
        
        message += f"💻 <b>CPU:</b> {cpu:.1f}%\n\n"
        
        message += f"🧠 <b>RAM:</b> {mem_percent:.1f}%\n"
        message += f"   └ {format_bytes(mem_current)} / {format_bytes(mem_total)}\n\n"
        
        message += f"💿 <b>Диск:</b> {disk_percent:.1f}%\n"
        message += f"   └ {format_bytes(disk_current)} / {format_bytes(disk_total)}\n\n"
        
        message += f"🌐 <b>Сеть:</b>\n"
        message += f"   ⬆️ Отправлено: {format_bytes(net_up)}\n"
        message += f"   ⬇️ Получено: {format_bytes(net_down)}\n\n"
        
        # Статус Xray с эмодзи
        xray_emoji = "✅" if xray_state == "running" else "❌"
        message += f"🔐 <b>Xray:</b> {xray_emoji} {xray_state}\n"
        message += f"   └ Версия: {xray_version}\n\n"
        
        message += f"🔌 <b>TCP соединений:</b> {tcp_count}"
        
        # Добавляем кнопки в два ряда
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="server_status"),
                InlineKeyboardButton(text="💾 Бэкап", callback_data="create_backup")
            ],
            [
                InlineKeyboardButton(text="🔔 Уведомления", callback_data="notification_settings"),
                InlineKeyboardButton(text="📥 JSON конфиг", callback_data="export_json_config")
            ],
            [
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")
            ]
        ])
        
        # Если это обновление существующего сообщения, редактируем его
        # Иначе отправляем новое
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


@dp.callback_query(lambda c: c.data == "notification_settings")
async def show_notification_settings(callback_query: types.CallbackQuery, state: FSMContext):
    """Показать настройки уведомлений"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    await callback_query.answer()
    
    # Получаем текущие настройки
    settings = config.users_db.get_all_notification_settings()
    cpu_alert = settings.get('cpu_alert', False)
    ram_alert = settings.get('ram_alert', False)
    disk_alert = settings.get('disk_alert', False)
    
    # Формируем сообщение
    message = "🔔 <b>Настройки уведомлений</b>\n\n"
    message += f"💻 Загрузка CPU {'✅' if cpu_alert else '❌'}\n"
    message += f"   └ Уведомление при загрузке > 95%\n\n"
    message += f"🧠 Загрузка RAM {'✅' if ram_alert else '❌'}\n"
    message += f"   └ Уведомление при загрузке > 95%\n\n"
    message += f"💿 Заполнение диска {'✅' if disk_alert else '❌'}\n"
    message += f"   └ Уведомление при заполнении > 95%\n\n"
    message += "Нажмите на переключатель для изменения настройки"
    
    # Создаем клавиатуру с переключателями
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💻 CPU {'✅ Вкл' if cpu_alert else '❌ Выкл'}",
            callback_data="toggle_cpu_alert"
        )],
        [InlineKeyboardButton(
            text=f"🧠 RAM {'✅ Вкл' if ram_alert else '❌ Выкл'}",
            callback_data="toggle_ram_alert"
        )],
        [InlineKeyboardButton(
            text=f"💿 Диск {'✅ Вкл' if disk_alert else '❌ Выкл'}",
            callback_data="toggle_disk_alert"
        )],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_server_status")]
    ])
    
    try:
        await callback_query.message.edit_text(
            message,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except:
        await callback_query.message.answer(
            message,
            parse_mode="HTML",
            reply_markup=keyboard
        )


@dp.callback_query(lambda c: c.data == "toggle_cpu_alert")
async def toggle_cpu_alert(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключить уведомление о загрузке CPU"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    # Получаем текущее состояние и переключаем
    current = config.users_db.get_notification_setting('cpu_alert')
    new_state = not current
    config.users_db.set_notification_setting('cpu_alert', new_state)
    
    await callback_query.answer(
        f"✅ Уведомления о CPU {'включены' if new_state else 'выключены'}",
        show_alert=True
    )
    
    # Обновляем окно настроек
    await show_notification_settings(callback_query, state)


@dp.callback_query(lambda c: c.data == "toggle_ram_alert")
async def toggle_ram_alert(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключить уведомление о загрузке RAM"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    # Получаем текущее состояние и переключаем
    current = config.users_db.get_notification_setting('ram_alert')
    new_state = not current
    config.users_db.set_notification_setting('ram_alert', new_state)
    
    await callback_query.answer(
        f"✅ Уведомления о RAM {'включены' if new_state else 'выключены'}",
        show_alert=True
    )
    
    # Обновляем окно настроек
    await show_notification_settings(callback_query, state)


@dp.callback_query(lambda c: c.data == "toggle_disk_alert")
async def toggle_disk_alert(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключить уведомление о заполнении диска"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    # Получаем текущее состояние и переключаем
    current = config.users_db.get_notification_setting('disk_alert')
    new_state = not current
    config.users_db.set_notification_setting('disk_alert', new_state)
    
    await callback_query.answer(
        f"✅ Уведомления о диске {'включены' if new_state else 'выключены'}",
        show_alert=True
    )
    
    # Обновляем окно настроек
    await show_notification_settings(callback_query, state)


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
async def show_users_list(callback_query: types.CallbackQuery, state: FSMContext):
    """Показать список пользователей (только для администратора)"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    # Очищаем состояние при открытии нового окна
    await state.clear()
    
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
        buttons = [
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="show_users")]
        ]
        
        # Показываем кнопки действий только если есть пользователи
        if users:
            buttons.extend([
                [InlineKeyboardButton(text="🔒 Заблокировать", callback_data="action_block")],
                [InlineKeyboardButton(text="🔓 Разблокировать", callback_data="action_unblock")],
                [InlineKeyboardButton(text="🗑 Удалить", callback_data="action_remove")]
            ])
        
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Редактируем существующее сообщение
        try:
            await callback_query.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        except Exception as e:
            # Логируем ошибку, но не создаем новое сообщение
            logger.error(f"Не удалось отредактировать сообщение: {e}")
            await callback_query.answer("❌ Не удалось обновить", show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка получения списка пользователей: {e}")
        await callback_query.message.answer(f"❌ Ошибка: {str(e)}")

@dp.callback_query(lambda c: c.data == "back_to_start")
async def back_to_start_menu(callback_query: types.CallbackQuery, state: FSMContext):
    """Вернуться в главное меню /start"""
    user_id = callback_query.from_user.id
    username = callback_query.from_user.username
    first_name = callback_query.from_user.first_name
    
    # Очищаем состояние при возврате в главное меню
    await state.clear()
    
    await callback_query.answer()
    
    # Обновляем конфигурацию из текущей панели
    try:
        panel_manager = config.panel_manager
        current_panel_id = panel_manager.get_current_panel_id()
        
        if current_panel_id:
            panel_config = panel_manager.get_panel(current_panel_id)
            if panel_config:
                # Обновляем transport и security из панели
                if hasattr(panel_config, 'transport'):
                    config.vpn.transport = panel_config.transport
                if hasattr(panel_config, 'security'):
                    config.vpn.security = panel_config.security
                
                logger.info(f"🔄 Конфигурация обновлена из панели {current_panel_id}")
    except Exception as e:
        logger.error(f"Ошибка обновления конфигурации: {e}")
    
    # Проверяем права доступа
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
                InlineKeyboardButton(text="📋 Все ключи", callback_data="cmd_allclients")
            ],
            [
                InlineKeyboardButton(text="🖥️ Сервер", callback_data="server_status"),
                InlineKeyboardButton(text="👥 Пользователи", callback_data="show_users")
            ],
            [
                InlineKeyboardButton(text="🔧 Панели", callback_data="show_panels")
            ]
        ])
        
        # Получаем информацию о текущей панели
        current_panel = config.get_current_panel()
        panel_info = ""
        
        # Используем актуальные данные из config.vpn (обновляются через refresh_vpn_config)
        transport = config.vpn.transport if hasattr(config, 'vpn') and config.vpn else "N/A"
        security = config.vpn.security if hasattr(config, 'vpn') and config.vpn else "N/A"
        
        if current_panel:
            alias = current_panel.alias
            is_local = current_panel.is_local
            xui_version = current_panel.xui_version
            xui_url = current_panel.xui_url
            
            panel_info = (
                f"\n📋 <b>Панель:</b>\n"
                f"• Alias: <code>{alias}</code>\n"
                f"• Local: <code>{'Да' if is_local else 'Нет'}</code>\n"
                f"• Version: <code>{xui_version}</code>\n"
                f"• URL: <code>{xui_url}</code>\n"
            )
        
        text = (
            f"👑 Администратор\n {username or first_name}\n\n"
            f"🔐 <b>Настройки подключения:</b>\n"
            f"• Transport: <code>{transport}</code>\n"
            f"• Security: <code>{security}</code>"
            f"{panel_info}"
        )
        
        try:
            await callback_query.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        except:
            await callback_query.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Создать ключ", callback_data="cmd_new"),
                InlineKeyboardButton(text="⏱ Временный ключ", callback_data="cmd_tempkey")
            ],
            [
                InlineKeyboardButton(text="🔑 Мои ключи", callback_data="cmd_myclients")
            ]
        ])
        
        # Получаем информацию о текущей панели
        current_panel = config.get_current_panel()
        panel_alias = current_panel.alias if current_panel else "N/A"
        
        text = (
            f"👤 <b>Пользователь:</b> {username or first_name}\n"
            f"📡 <b>Панель:</b> <code>{panel_alias}</code>\n\n"
            f"🔐 <b>Настройки подключения:</b>\n"
            f"• Transport: <code>{config.vpn.transport}</code>\n"
            f"• Security: <code>{config.vpn.security}</code>\n\n"
            f"📱 Выберите действие:"
        )
        
        try:
            await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except:
            await callback_query.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
@dp.callback_query(lambda c: c.data == "cmd_new")
async def callback_cmd_new(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Создать ключ'"""
    user_id = callback_query.from_user.id
    
    # Проверка доступа
    if not is_allowed(user_id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    if is_blocked_by_admin(user_id):
        await callback_query.answer("⛔ Вы заблокированы администратором", show_alert=True)
        return
    
    await callback_query.answer()
    
    # Редактируем текущее сообщение
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
    ])
    
    try:
        await callback_query.message.edit_text(
            "📝 Введите комментарий к подключению:\n\n",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except:
        await bot.send_message(
            callback_query.message.chat.id,
            "📝 Введите комментарий к подключению:\n\n",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    
    await state.set_state(NewClientState.waiting_for_comment)

@dp.callback_query(lambda c: c.data == "cmd_tempkey")
async def callback_cmd_tempkey(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Временный ключ'"""
    user_id = callback_query.from_user.id
    
    # Проверка доступа
    if not is_allowed(user_id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    if is_blocked_by_admin(user_id):
        await callback_query.answer("⛔ Вы заблокированы администратором", show_alert=True)
        return
    
    await callback_query.answer()
    
    # Редактируем текущее сообщение
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
    ])
    
    try:
        await callback_query.message.edit_text(
            "📝 Введите комментарий к подключению:\n\n",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except:
        await bot.send_message(
            callback_query.message.chat.id,
            "📝 Введите комментарий к подключению:\n\n",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    
    await state.set_state(TempKeyState.waiting_for_comment)

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
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    if is_blocked_by_admin(user_id):
        await callback_query.answer("⛔ Вы заблокированы администратором", show_alert=True)
        return
    
    await callback_query.answer()
    
    # Вызываем функционал команды /myclients
    try:
        username = callback_query.from_user.username
        if not username:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ])
            try:
                await callback_query.message.edit_text(
                    "❌ У вас не установлен username в Telegram.\n\nУстановите username в настройках Telegram для использования бота.",
                    reply_markup=keyboard
                )
            except:
                await bot.send_message(
                    callback_query.message.chat.id,
                    "❌ У вас не установлен username в Telegram.\n\nУстановите username в настройках Telegram для использования бота.",
                    reply_markup=keyboard
                )
            return
        
        # Получаем ключи пользователя из X-UI по username
        clients = await xui_client.get_user_clients_by_username(username)
        
        # Подсчитываем статистику
        if not clients:
            # Показываем полное окно даже если ключей нет
            text = f"🔑 <b>Мои ключи (0)</b>\n\n"
            text += f"✅ Активных: 0\n"
            text += f"⏸️ Неактивных: 0\n"
            text += f"⏰ Просроченных: 0\n\n"
            text += "📭 <i>У вас пока нет ключей.</i>\n\n"
            
            # Добавляем кнопки "Обновить" и "Назад"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_myclients")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ])
            try:
                await callback_query.message.edit_text(
                    text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except:
                await bot.send_message(
                    callback_query.message.chat.id,
                    text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            return
        
        # Подсчитываем статистику для существующих ключей
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
                display_text = f"{comment[:25]}"
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
        
        # Добавляем кнопки "Обновить" и "Назад"
        buttons.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_myclients"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        text = f"🔑 <b>Мои ключи ({len(clients)})</b>\n\n"
        text += f"✅ Активных: {active_count}\n"
        text += f"⏸️ Неактивных: {inactive_count}\n"
        text += f"⏰ Просроченных: {expired_count}\n\n"
        text += "Выберите ключ для просмотра:"
        
        try:
            await callback_query.message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except:
            await bot.send_message(
                callback_query.message.chat.id,
                text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        
    except Exception as e:
        logger.error(f"Ошибка получения списка клиентов: {e}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
        ])
        try:
            await callback_query.message.edit_text(
                f"❌ Ошибка: {str(e)}",
                reply_markup=keyboard
            )
        except:
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
        
        # Получаем ключи пользователя из X-UI по username
        clients = await xui_client.get_user_clients_by_username(username)
        
        # Подсчитываем статистику
        if not clients:
            # Показываем полное окно даже если ключей нет
            text = f"🔑 <b>Мои ключи (0)</b>\n\n"
            text += f"✅ Активных: 0\n"
            text += f"⏸️ Неактивных: 0\n"
            text += f"⏰ Просроченных: 0\n\n"
            text += "📭 <i>У вас пока нет ключей.</i>\n\n"
            
            # Добавляем кнопки "Обновить" и "Назад"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_myclients")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
            ])
            
            await callback_query.message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await callback_query.answer("✅ Обновлено", show_alert=False)
            return
        
        # Подсчитываем статистику для существующих ключей
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
                display_text = f"{comment[:25]}"
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
        
        # Добавляем кнопки "Обновить" и "Назад"
        buttons.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_myclients"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        text = f"🔑 <b>Мои ключи ({len(clients)})</b>\n\n"
        text += f"✅ Активных: {active_count}\n"
        text += f"⏸️ Неактивных: {inactive_count}\n"
        text += f"⏰ Просроченных: {expired_count}\n\n"
        text += "Выберите ключ для просмотра:"
        
        await callback_query.message.edit_text(
            text,
            reply_markup=keyboard,
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


@dp.callback_query(lambda c: c.data == "action_block")
async def action_block_user(callback_query: types.CallbackQuery):
    """Показать список пользователей для блокировки"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    await callback_query.answer()
    
    try:
        users = config.users_db.list_users()
        
        if not users:
            await callback_query.answer("Нет пользователей для блокировки", show_alert=True)
            return
        
        # Фильтруем только активных пользователей
        active_users = [(uid, uname, added) for uid, uname, added in users if not config.users_db.is_blocked_by_admin(uid)]
        
        if not active_users:
            await callback_query.answer("Все пользователи уже заблокированы", show_alert=True)
            return
        
        # Создаем кнопки для каждого пользователя
        buttons = []
        for user_id, username, _ in active_users:
            try:
                chat = await bot.get_chat(user_id)
                user_name = f"@{chat.username}" if chat.username else f"ID: {user_id}"
            except:
                user_name = username if username else f"ID: {user_id}"
            
            buttons.append([InlineKeyboardButton(text=f"🔒 {user_name}", callback_data=f"doblock_{user_id}")])
        
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="show_users")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback_query.message.edit_text(
            "🔒 <b>Выберите пользователя для блокировки:</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка показа списка для блокировки: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data == "action_unblock")
async def action_unblock_user(callback_query: types.CallbackQuery):
    """Показать список заблокированных пользователей для разблокировки"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    await callback_query.answer()
    
    try:
        # Получаем всех заблокированных пользователей
        with sqlite3.connect(config.users_db.db_path) as conn:
            cursor = conn.execute("SELECT user_id FROM blocked_users")
            blocked_ids = [row[0] for row in cursor.fetchall()]
        
        if not blocked_ids:
            await callback_query.answer("Нет заблокированных пользователей", show_alert=True)
            return
        
        # Создаем кнопки для каждого заблокированного пользователя
        buttons = []
        for user_id in blocked_ids:
            try:
                chat = await bot.get_chat(user_id)
                user_name = f"@{chat.username}" if chat.username else f"ID: {user_id}"
            except:
                user_name = f"ID: {user_id}"
            
            buttons.append([InlineKeyboardButton(text=f"🔓 {user_name}", callback_data=f"dounblock_{user_id}")])
        
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="show_users")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback_query.message.edit_text(
            "🔓 <b>Выберите пользователя для разблокировки:</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка показа списка для разблокировки: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data == "action_remove")
async def action_remove_user(callback_query: types.CallbackQuery):
    """Показать список пользователей для удаления"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    await callback_query.answer()
    
    try:
        users = config.users_db.list_users()
        
        if not users:
            await callback_query.answer("Нет пользователей для удаления", show_alert=True)
            return
        
        # Создаем кнопки для каждого пользователя
        buttons = []
        for user_id, username, _ in users:
            # Пропускаем главного администратора
            if user_id == config.users_db.get_main_admin():
                continue
            
            try:
                chat = await bot.get_chat(user_id)
                user_name = f"@{chat.username}" if chat.username else f"ID: {user_id}"
            except:
                user_name = username if username else f"ID: {user_id}"
            
            buttons.append([InlineKeyboardButton(text=f"🗑 {user_name}", callback_data=f"doremove_{user_id}")])
        
        if not buttons:
            await callback_query.answer("Нет пользователей для удаления", show_alert=True)
            return
        
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="show_users")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback_query.message.edit_text(
            "🗑 <b>Выберите пользователя для удаления:</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка показа списка для удаления: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith('doblock_'))
async def process_doblock_user(callback_query: types.CallbackQuery, state: FSMContext):
    """Заблокировать пользователя и вернуться в меню пользователей"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    user_id = int(callback_query.data.split('_')[1])
    
    try:
        if config.users_db.block_user(user_id, callback_query.from_user.id):
            await callback_query.answer("✅ Пользователь заблокирован")
            try:
                await bot.send_message(user_id, "⛔ Вы заблокированы администратором.")
            except:
                pass
        else:
            await callback_query.answer("❌ Ошибка при блокировке!", show_alert=True)
            return
        
        # Возвращаемся в меню пользователей
        callback_query.data = "show_users"
        await show_users_list(callback_query, state)
        
    except Exception as e:
        logger.error(f"Ошибка блокировки пользователя: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith('dounblock_'))
async def process_dounblock_user(callback_query: types.CallbackQuery, state: FSMContext):
    """Разблокировать пользователя и вернуться в меню пользователей"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    user_id = int(callback_query.data.split('_')[1])
    
    try:
        if config.users_db.unblock_user(user_id):
            await callback_query.answer("✅ Пользователь разблокирован")
            try:
                await bot.send_message(user_id, "✅ Вы разблокированы администратором.")
            except:
                pass
        else:
            await callback_query.answer("❌ Ошибка при разблокировке!", show_alert=True)
            return
        
        # Возвращаемся в меню пользователей
        callback_query.data = "show_users"
        await show_users_list(callback_query, state)
        
    except Exception as e:
        logger.error(f"Ошибка разблокировки пользователя: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith('doremove_'))
async def process_doremove_user(callback_query: types.CallbackQuery, state: FSMContext):
    """Удалить пользователя и вернуться в меню пользователей"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return
    
    user_id = int(callback_query.data.split('_')[1])
    
    # Проверка на удаление главного администратора
    if user_id == config.users_db.get_main_admin():
        await callback_query.answer("❌ Нельзя удалить главного администратора!", show_alert=True)
        return
    
    try:
        if config.users_db.remove_user(user_id):
            await callback_query.answer("✅ Пользователь удален")
            try:
                await bot.send_message(user_id, "⛔ Ваш доступ отозван администратором.")
            except:
                pass
        else:
            await callback_query.answer("❌ Ошибка при удалении!", show_alert=True)
            return
        
        # Возвращаемся в меню пользователей
        callback_query.data = "show_users"
        await show_users_list(callback_query, state)
        
    except Exception as e:
        logger.error(f"Ошибка удаления пользователя: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


# ============================================
# Управление панелями 3x-ui
# ============================================

@dp.callback_query(lambda c: c.data == "show_panels")
async def show_panels_list(callback_query: types.CallbackQuery, state: FSMContext):
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
            
            await callback_query.message.edit_text(
                diagnostic_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
                ])
            )
            return
        
        # Проверяем статусы всех панелей
        await callback_query.message.edit_text(
            "🔄 Проверка статусов панелей...",
            parse_mode="HTML"
        )
        
        statuses = await panel_manager.check_all_panels_status()
        
        # Формируем текст со списком панелей
        text = "🔧 <b>Управление панелями</b>\n\n"
        text += "📋 <b>Список панелей:</b>\n\n"
        
        for panel_id, panel_config in panels.items():
            alias = getattr(panel_config, 'alias', panel_id)
            is_current = panel_id == current_panel_id
            is_online = statuses.get(panel_id, False)
            
            # Иконки статуса
            current_icon = "🟢" if is_current else "⚪"
            status_icon = "✅" if is_online else "❌"
            status_text = "Доступна" if is_online else "Недоступна"
            
            text += f"{current_icon} <b>{alias}</b>\n"
            text += f"   {status_icon} {status_text}"
            if is_current:
                text += " (Текущая)"
            text += f"\n   ID: <code>{panel_id}</code>\n\n"
        
        # Кнопки управления
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_panels"),
                InlineKeyboardButton(text="🔌 Подключить", callback_data="select_panel_to_connect")
            ],
            [
                InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")
            ]
        ])
        
        await callback_query.message.edit_text(
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
    await callback_query.answer("🔄 Обновление статусов...")
    
    # Просто вызываем show_panels_list напрямую
    await show_panels_list(callback_query, state)


@dp.callback_query(lambda c: c.data == "select_panel_to_connect")
async def select_panel_to_connect(callback_query: types.CallbackQuery, state: FSMContext):
    """Выбрать панель для подключения"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.message.answer("⛔ Доступ запрещен")
        return
    
    try:
        panel_manager = config.panel_manager
        panels = panel_manager.get_all_panels()
        current_panel_id = panel_manager.get_current_panel_id()
        
        if not panels:
            await callback_query.answer("❌ Панели не настроены", show_alert=True)
            return
        
        # Формируем кнопки для выбора панели
        keyboard_buttons = []
        for panel_id, panel_config in panels.items():
            alias = getattr(panel_config, 'alias', panel_id)
            is_current = panel_id == current_panel_id
            
            button_text = f"{'🟢' if is_current else '⚪'} {alias}"
            if is_current:
                button_text += " (Текущая)"
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"connect_panel:{panel_id}"
                )
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="◀️ Назад", callback_data="show_panels")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(
            "🔌 <b>Выберите панель для подключения:</b>\n\n"
            "⚠️ При переключении текущая панель будет сохранена.",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка выбора панели: {e}")
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
                                [InlineKeyboardButton(text="◀️ Назад", callback_data="show_panels")]
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
                
                stats_text = (
                    f"🟢 <b>Текущая панель: {alias}</b>\n\n"
                    f"🔐 <b>Информация о панели:</b>\n"
                    f"• URL: <code>{getattr(panel_config, 'xui_url', 'N/A') or getattr(panel_config, 'url', 'N/A')}</code>\n"
                    f"• Версия: <code>{getattr(panel_config, 'xui_version', 'N/A') or getattr(panel_config, 'version', 'N/A')}</code>\n"
                    f"• Inbound ID: <code>{getattr(panel_config, 'inbound_id', 'N/A')}</code>\n\n"
                    f"📊 <b>Статистика ключей:</b>\n"
                    f"• Всего ключей: <b>{total_clients}</b>\n"
                    f"• Активных: <b>{active_clients}</b> ✅\n"
                    f"• Неактивных: <b>{inactive_clients}</b> ❌\n\n"
                    f"📈 <b>Трафик:</b>\n"
                    f"• Загружено: <code>{format_bytes(total_traffic_up)}</code>\n"
                    f"• Скачано: <code>{format_bytes(total_traffic_down)}</code>\n"
                    f"• Всего: <code>{format_bytes(total_traffic)}</code>"
                )
            except Exception as e:
                logger.error(f"Ошибка получения статистики: {e}")
                stats_text = (
                    f"🟢 <b>Текущая панель: {alias}</b>\n\n"
                    f"🔐 URL: <code>{getattr(panel_config, 'xui_url', 'N/A') or getattr(panel_config, 'url', 'N/A')}</code>\n"
                    f"📋 Версия: <code>{getattr(panel_config, 'xui_version', 'N/A') or getattr(panel_config, 'version', 'N/A')}</code>\n"
                    f"🆔 Inbound ID: <code>{getattr(panel_config, 'inbound_id', 'N/A')}</code>\n\n"
                    f"⚠️ Не удалось получить статистику ключей"
                )
            
            await callback_query.message.edit_text(
                stats_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ К списку панелей", callback_data="show_panels")],
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
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="show_panels")]
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
                        
                        stats_text = (
                            f"✅ <b>Успешно подключено к панели {alias}</b>\n\n"
                            f"🔐 <b>Информация о панели:</b>\n"
                            f"• URL: <code>{new_xui_config.url}</code>\n"
                            f"• Версия: <code>{new_xui_config.version}</code>\n"
                            f"• Inbound ID: <code>{new_xui_config.inbound_id}</code>\n\n"
                            f"📊 <b>Статистика ключей:</b>\n"
                            f"• Всего ключей: <b>{total_clients}</b>\n"
                            f"• Активных: <b>{active_clients}</b> ✅\n"
                            f"• Неактивных: <b>{inactive_clients}</b> ❌\n\n"
                            f"📈 <b>Трафик:</b>\n"
                            f"• Загружено: <code>{format_bytes(total_traffic_up)}</code>\n"
                            f"• Скачано: <code>{format_bytes(total_traffic_down)}</code>\n"
                            f"• Всего: <code>{format_bytes(total_traffic)}</code>"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка получения статистики: {e}")
                        stats_text = (
                            f"✅ <b>Успешно подключено к панели {alias}</b>\n\n"
                            f"🔐 URL: <code>{new_xui_config.url}</code>\n"
                            f"📋 Версия: <code>{new_xui_config.version}</code>\n"
                            f"🆔 Inbound ID: <code>{new_xui_config.inbound_id}</code>\n\n"
                            f"⚠️ Не удалось получить статистику ключей"
                        )
                    
                    await callback_query.message.edit_text(
                        stats_text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="◀️ К списку панелей", callback_data="show_panels")],
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
                            [InlineKeyboardButton(text="◀️ Назад", callback_data="show_panels")]
                        ])
                    )
            else:
                await callback_query.message.edit_text(
                    f"❌ Ошибка создания конфигурации для панели {alias}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="show_panels")]
                    ])
                )
        else:
            await callback_query.message.edit_text(
                f"❌ Ошибка переключения на панель {alias}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="show_panels")]
                ])
            )
        
    except Exception as e:
        logger.error(f"Ошибка подключения к панели: {e}")
        await callback_query.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="show_panels")]
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
        
        await dp.start_polling(bot)
    else:
        logger.error("❌ Не удалось подключиться к X-UI")
        return


if __name__ == "__main__":
    asyncio.run(main())