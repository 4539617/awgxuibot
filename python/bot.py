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
from utils import XUIClient, generate_vless_link, setup_logging

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
            
            # Уведомляем администратора
            if was_user_before:
                # Возвращение пользователя
                try:
                    await bot.send_message(
                        admin_id,
                        f"🔄 <b>Возвращение пользователя!</b>\n\n"
                        f"👤 Пользователь: @{username}\n"
                        f"📝 Имя: {first_name}\n"
                        f"🆔 ID: <code>{user_id}</code>\n\n"
                        f"У пользователя обнаружены активные ключи.\n"
                        f"Доступ восстановлен автоматически.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу: {e}")
            else:
                # Новый пользователь с ключами
                try:
                    await bot.send_message(
                        admin_id,
                        f"🆕 <b>Автодобавление пользователя!</b>\n\n"
                        f"👤 Пользователь: @{username}\n"
                        f"📝 Имя: {first_name}\n"
                        f"🆔 ID: <code>{user_id}</code>\n\n"
                        f"У пользователя обнаружены активные ключи в системе.\n"
                        f"Доступ предоставлен автоматически.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу: {e}")
            
            # Показываем меню пользователя
            await message.answer(
                f"👤 Добро пожаловать, {first_name}!\n\n"
                f"У вас обнаружены активные ключи.\n"
                f"Доступ предоставлен автоматически.\n\n"
                f"Команды:\n"
                f"/new - Создать ключ\n"
                f"/myclients - Мои ключи\n"
                f"/help - Помощь",
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
            await message.answer(
                f"👑 Администратор\n {username or first_name}\n\n"
                f"🔐 <b>Настройки подключения:</b>\n"
                f"• Transport: <code>{config.vpn.transport}</code>\n"
                f"• Security: <code>{config.vpn.security}</code>\n\n"
                f"Команды:\n"
                f"/new - Создать ключ\n"
                f"/tempkey - Временный ключ\n"
                f"/myclients - Мои ключи\n"
                f"/allclients - Все ключи\n"
                f"/users - Список пользователей\n"
                f"/blockuser - Заблокировать пользователя\n"
                f"/unblockuser - Разблокировать пользователя\n"
                f"/removeuser - Удалить пользователя\n"
                f"/help - Помощь",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f"👤 Пользователь\n {username or first_name}\n\n"
                f"Команды:\n"
                f"/new - Создать ключ\n"
                f"/tempkey - Временный ключ\n"
                f"/myclients - Мои ключи\n"
                f"/help - Помощь",
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
        "📖 Вернуться в главное меню /start \n\n"
        "⚠️ Одно устройство - один ключ.\n\n"
        " \n\n"
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
        # Больше не записываем в user_clients - ключи берутся из X-UI
        vless_link = generate_vless_link(result['uuid'], email, config.vpn, config.xui.inbound_id)

        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(vless_link)
        qr.make()
        qr_img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        await bot.delete_message(message.chat.id, status_msg.message_id)
        await message.answer_photo(
            photo=types.BufferedInputFile(buffer.getvalue(), filename="vless.png"),
            caption=f"\n\n📝 {comment}",
            parse_mode="HTML"
        )
        await message.answer(
            f"<code>{vless_link}</code>",
            parse_mode="HTML"
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
        vless_link = generate_vless_link(result['uuid'], email, config.vpn, config.xui.inbound_id)

        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(vless_link)
        qr.make()
        qr_img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        await bot.delete_message(message.chat.id, status_msg.message_id)
        await message.answer_photo(
            photo=types.BufferedInputFile(buffer.getvalue(), filename="vless.png"),
            caption=f"⏰ <b>Временный ключ на {duration_text}</b>\n\n📝 {comment}",
            parse_mode="HTML"
        )
        await message.answer(
            f"<code>{vless_link}</code>",
            parse_mode="HTML"
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
        await message.answer("📭 У вас пока нет ключей.\n\nИспользуйте /new для создания.")
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

    # Генерируем VLESS ссылку
    vless_link = generate_vless_link(client_uuid, email, config.vpn, config.xui.inbound_id)

    # Генерируем QR-код
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(vless_link)
    qr.make()
    qr_img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    qr_img.save(buffer, format="PNG")
    buffer.seek(0)

    # Определяем статус с иконкой
    if status == 'active':
        status_text = "✅ Активен"
    elif status == 'inactive':
        status_text = "⏸️ Неактивен (выключен)"
    else:  # expired
        status_text = "⏰ Просрочен"

    # Отправляем QR-код
    await callback_query.message.answer_photo(
        photo=types.BufferedInputFile(buffer.getvalue(), filename="vless.png"),
        caption=f"{status_text}\n📝 <b>{comment if comment else 'Без комментария'}</b>",
        parse_mode="HTML"
    )
    
    # Отправляем текст ключа
    await callback_query.message.answer(
        f"<code>{vless_link}</code>",
        parse_mode="HTML"
    )
    await callback_query.answer()


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

    await message.answer(text, parse_mode="HTML")


@dp.message(Command("blockuser"))
async def cmd_block_user(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Отказано в доступе.")
        return

    users = config.users_db.list_users()
    if not users:
        await message.answer("📭 Список пользователей пуст.")
        return

    buttons = []
    for user_id, username, _ in users:
        if config.users_db.is_blocked_by_admin(user_id):
            continue
        if username:
            button_text = f"@{username}"
        else:
            try:
                chat = await bot.get_chat(user_id)
                button_text = f"@{chat.username}" if chat.username else str(user_id)
            except:
                button_text = str(user_id)
        buttons.append([InlineKeyboardButton(text=f"🔒 {button_text}", callback_data=f"block_{user_id}")])

    if not buttons:
        await message.answer("📭 Нет активных пользователей для блокировки.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("👥 Выберите пользователя для блокировки:", reply_markup=keyboard)


@dp.message(Command("unblockuser"))
async def cmd_unblock_user(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Отказано в доступе.")
        return

    # Получаем всех заблокированных пользователей
    with sqlite3.connect(config.users_db.db_path) as conn:
        cursor = conn.execute("SELECT user_id FROM blocked_users")
        blocked_ids = [row[0] for row in cursor.fetchall()]

    if not blocked_ids:
        await message.answer("📭 Нет заблокированных пользователей.")
        return

    buttons = []
    for user_id in blocked_ids:
        try:
            chat = await bot.get_chat(user_id)
            button_text = f"@{chat.username}" if chat.username else str(user_id)
        except:
            button_text = str(user_id)
        buttons.append([InlineKeyboardButton(text=f"🔓 {button_text}", callback_data=f"unblock_{user_id}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("👥 Выберите пользователя для разблокировки:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data and c.data.startswith('block_'))
async def process_block_user(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    user_id = int(callback_query.data.split('_')[1])

    if config.users_db.block_user(user_id, callback_query.from_user.id):
        await callback_query.message.edit_text(f"✅ Пользователь заблокирован.")
        try:
            await bot.send_message(user_id, "⛔ Вы заблокированы администратором.")
        except:
            pass
    else:
        await callback_query.message.edit_text("❌ Ошибка при блокировке!")
    await callback_query.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith('unblock_'))
async def process_unblock_user(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    user_id = int(callback_query.data.split('_')[1])

    if config.users_db.unblock_user(user_id):
        await callback_query.message.edit_text(f"✅ Пользователь разблокирован.")
        try:
            await bot.send_message(user_id, "✅ Вы разблокированы администратором.")
        except:
            pass
    else:
        await callback_query.message.edit_text("❌ Ошибка при разблокировке!")
    await callback_query.answer()


@dp.message(Command("removeuser"))
async def cmd_remove_user(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Отказано в доступе.")
        return

    users = config.users_db.list_users()
    if not users:
        await message.answer("📭 Список пользователей пуст.")
        return

    buttons = []
    for user_id, username, _ in users:
        if username:
            button_text = f"@{username}"
        else:
            try:
                chat = await bot.get_chat(user_id)
                button_text = f"@{chat.username}" if chat.username else str(user_id)
            except:
                button_text = str(user_id)
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"remove_{user_id}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("👥 Выберите пользователя для удаления:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data and c.data.startswith('remove_'))
async def process_remove_user(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Отказано в доступе", show_alert=True)
        return

    user_id = int(callback_query.data.split('_')[1])

    if user_id == config.users_db.get_main_admin():
        await callback_query.answer("❌ Нельзя удалить главного администратора!", show_alert=True)
        return

    try:
        chat = await bot.get_chat(user_id)
        user_name = f"@{chat.username}" if chat.username else str(user_id)
    except:
        user_name = str(user_id)

    if config.users_db.remove_user(user_id):
        await callback_query.message.edit_text(f"✅ Пользователь {user_name} удален.")
        try:
            await bot.send_message(user_id, "⛔ Ваш доступ отозван администратором.")
        except:
            pass
    else:
        await callback_query.message.edit_text("❌ Ошибка при удалении!")
    await callback_query.answer()


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
        
        # Формируем текст статистики
        text = f"📊 <b>Статистика ключей</b>\n\n"
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
        
        # Создаем кнопки для каждого клиента
        buttons = []
        for client in clients_to_show:
            email = client['email']
            comment = client['comment']
            
            # Подсчитываем трафик клиента
            client_traffic = client.get('up', 0) + client.get('down', 0)
            traffic_mb = client_traffic / (1024**2)  # Переводим в MB
            
            # Формируем текст кнопки
            if comment:
                button_text = f"{email[:15]} - {comment[:15]}"
            else:
                button_text = email[:30]
            
            # Добавляем расход трафика
            if traffic_mb >= 1:
                button_text += f" ({traffic_mb:.0f} MB)"
            
            # Добавляем иконку статуса
            if client['status'] == 'active':
                button_text = f"✅ {button_text}"
            elif client['status'] == 'inactive':
                button_text = f"⏸️ {button_text}"
            else:  # expired
                button_text = f"⏰ {button_text}"
            
            buttons.append([
                InlineKeyboardButton(text=button_text, callback_data=f"allclient_{client['uuid']}")
            ])
        
        # Добавляем кнопку очистки если есть просроченные ключи
        if expired_count > 0:
            buttons.append([
                InlineKeyboardButton(text=f"🧹 Очистить просроченные ({expired_count})", callback_data="cleanup_expired")
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
        
        # Кнопка "Показать ключ"
        buttons.append([InlineKeyboardButton(text="🔑 Показать ключ", callback_data=f"showkey_{client_uuid}")])
        
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
        vless_link = generate_vless_link(client['uuid'], client['email'], config.vpn, config.xui.inbound_id)
        
        # Генерируем QR-код
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(vless_link)
        qr.make()
        qr_img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)
        
        # Отправляем QR-код
        await callback_query.message.answer_photo(
            photo=types.BufferedInputFile(buffer.getvalue(), filename="vless.png"),
            caption=f"🔑 <b>Ключ:</b> {client['email']}\n📝 {client['comment'] if client['comment'] else 'Без комментария'}",
            parse_mode="HTML"
        )
        
        # Отправляем текст ключа
        await callback_query.message.answer(
            f"<code>{vless_link}</code>",
            parse_mode="HTML"
        )
        
        await callback_query.answer("✅ Ключ отправлен")
        
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
        # Включаем клиента
        success = await xui_client.update_client_status(client_uuid, True)
        
        if success:
            await callback_query.answer("✅ Ключ включен")
            # Обновляем информацию о клиенте
            callback_query.data = f"allclient_{client_uuid}"
            await show_all_client_details(callback_query)
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
        # Выключаем клиента
        success = await xui_client.update_client_status(client_uuid, False)
        
        if success:
            await callback_query.answer("⏸️ Ключ выключен")
            # Обновляем информацию о клиенте
            callback_query.data = f"allclient_{client_uuid}"
            await show_all_client_details(callback_query)
        else:
            await callback_query.answer("❌ Ошибка выключения ключа", show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка выключения клиента: {e}")
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
        
        if not all_clients:
            await callback_query.message.edit_text("📭 Нет ключей в системе.")
            await callback_query.answer()
            return
        
        # Подсчитываем статистику
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
        text = f"📊 <b>Статистика ключей</b>\n\n"
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
        
        # Создаем кнопки для каждого клиента
        buttons = []
        for client in clients_to_show:
            email = client['email']
            comment = client['comment']
            
            # Подсчитываем трафик клиента
            client_traffic = client.get('up', 0) + client.get('down', 0)
            traffic_mb = client_traffic / (1024**2)  # Переводим в MB
            
            # Формируем текст кнопки
            if comment:
                button_text = f"{email[:15]} - {comment[:15]}"
            else:
                button_text = email[:30]
            
            # Добавляем расход трафика
            if traffic_mb >= 1:
                button_text += f" ({traffic_mb:.0f} MB)"
            
            # Добавляем иконку статуса
            if client['status'] == 'active':
                button_text = f"✅ {button_text}"
            elif client['status'] == 'inactive':
                button_text = f"⏸️ {button_text}"
            else:  # expired
                button_text = f"⏰ {button_text}"
            
            buttons.append([
                InlineKeyboardButton(text=button_text, callback_data=f"allclient_{client['uuid']}")
            ])
        
        # Добавляем кнопку очистки если есть просроченные ключи
        if expired_count > 0:
            buttons.append([
                InlineKeyboardButton(text=f"🧹 Очистить просроченные ({expired_count})", callback_data="cleanup_expired")
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
/blockuser - Заблокировать пользователя
/unblockuser - Разблокировать пользователя
/removeuser - Удалить пользователя
/help - Помощь

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
        vless_link = generate_vless_link(result['uuid'], email, config.vpn, config.xui.inbound_id)

        # Генерируем QR-код
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(vless_link)
        qr.make()
        qr_img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        # Отправляем ключ пользователю
        try:
            await bot.send_photo(
                user_id,
                photo=types.BufferedInputFile(buffer.getvalue(), filename="vless.png"),
                caption=f"🎁 <b>Временный ключ на {duration_text}</b>\n\n"
                        f"⏰ Ключ действителен: {duration_text}\n"
                        f"⚠️ После истечения срока ключ будет деактивирован",
                parse_mode="HTML"
            )
            await bot.send_message(
                user_id,
                f"<code>{vless_link}</code>",
                parse_mode="HTML"
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


async def main():
    logger.info("🚀 Запуск бота...")
    logger.info(f"👑 Администратор: {config.users_db.get_main_admin()}")

    if await xui_client.login():
        logger.info("✅ Подключение к X-UI установлено")
        await dp.start_polling(bot)
    else:
        logger.error("❌ Не удалось подключиться к X-UI")
        return


if __name__ == "__main__":
    asyncio.run(main())