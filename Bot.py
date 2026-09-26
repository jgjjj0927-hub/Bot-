import asyncio
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from threading import Thread
from flask import Flask

import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===== КОНФИГ =====
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "96266")
DB_PATH = os.getenv("DB_PATH", "shop.db")
BACKUP_DIR = "backups"
BACKUP_INTERVAL_MIN = 30

START_TIME = time.time()

# ===== ЛОГИ =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ===== ПРОВЕРКИ =====
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в Environment Variables")
if ADMIN_ID == 0:
    logger.warning("ADMIN_ID = 0. Админ-панель будет недоступна.")

os.makedirs(BACKUP_DIR, exist_ok=True)

# ===== BOT & DISPATCHER =====
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# ===== FLASK =====
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_flask():
    try:
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), use_reloader=False)
    except Exception as e:
        logger.error(f"Flask error: {e}")

# ===== КЛАВИАТУРЫ =====
BUTTONS = {
    "🛒 Заказать", "🔍 Поиск", "🧺 Корзина", "👤 Профиль", "🆘 Поддержка",
    "➕ Добавить товар", "📦 Товары", "🗑 Удалить товар",
    "📋 Заказы", "💬 Тикеты", "📊 Состояние бота", "💾 Бэкап",
    "📢 Рассылка", "🔙 Выйти",
}
BUTTONS_LIST = list(BUTTONS)

def main_menu():
    kb = [
        [KeyboardButton(text="🛒 Заказать"), KeyboardButton(text="🔍 Поиск")],
        [KeyboardButton(text="🧺 Корзина"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🆘 Поддержка")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def admin_menu():
    kb = [
        [KeyboardButton(text="➕ Добавить товар")],
        [KeyboardButton(text="📦 Товары"), KeyboardButton(text="🗑 Удалить товар")],
        [KeyboardButton(text="📋 Заказы"), KeyboardButton(text="💬 Тикеты")],
        [KeyboardButton(text="📢 Рассылка"), KeyboardButton(text="📊 Состояние бота")],
        [KeyboardButton(text="💾 Бэкап"), KeyboardButton(text="🔙 Выйти")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ===== СОСТОЯНИЯ =====
user_states = {}
admin_sessions = {}
temp_product = {}
temp_broadcast = {}

# ===== ИНИЦИАЛИЗАЦИЯ БД =====
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS products
                            (id INTEGER PRIMARY KEY AUTOINCREMENT,
                             name TEXT, price INTEGER, description TEXT, category TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS orders
                            (id INTEGER PRIMARY KEY AUTOINCREMENT,
                             user_id INTEGER, product TEXT, total INTEGER, status TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS cart
                            (id INTEGER PRIMARY KEY AUTOINCREMENT,
                             user_id INTEGER, product_id INTEGER)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS tickets
                            (id INTEGER PRIMARY KEY AUTOINCREMENT,
                             user_id INTEGER, message TEXT, answer TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS users
                            (user_id INTEGER PRIMARY KEY,
                             username TEXT, full_name TEXT, first_seen TEXT)''')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cart_user ON cart(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)")
        await db.commit()

# ===== ХЕЛПЕРЫ =====
def safe_username(user: types.User) -> str:
    return f"@{user.username}" if user.username else f"id{user.id}"

def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID

def is_admin_state(user_id: int) -> bool:
    return is_admin(user_id) and admin_sessions.get(user_id, False)

async def save_user(user: types.User):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, username, full_name, first_seen) VALUES (?, ?, ?, ?)",
                (user.id, user.username or "", user.full_name or "", datetime.now().isoformat())
            )
            await db.execute(
                "UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
                (user.username or "", user.full_name or "", user.id)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"save_user error: {e}")

async def fetch_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('''
            SELECT user_id FROM users
            UNION
            SELECT user_id FROM orders
            UNION
            SELECT user_id FROM tickets
        ''')
        rows = await cur.fetchall()
        await cur.close()
        return [r[0] for r in rows if r[0] != ADMIN_ID]

async def fetch_products():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name, price, category FROM products")
        rows = await cur.fetchall()
        await cur.close()
        return rows

async def fetch_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT name, price, description FROM products WHERE id = ?", (product_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        return row

def build_catalog_keyboard(products):
    builder = InlineKeyboardBuilder()
    for p in products:
        builder.add(InlineKeyboardButton(
            text=f"{p[1]} — {p[2]} ₽",
            callback_data=f"view_{p[0]}"
        ))
    builder.adjust(1)
    return builder.as_markup()

# ===== БЭКАП =====
async def send_backup(reason: str = "ручной"):
    if not is_admin(ADMIN_ID):
        return False
    if not os.path.exists(DB_PATH):
        return False
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_name = f"{BACKUP_DIR}/shop_{timestamp}.db"
        shutil.copy2(DB_PATH, backup_name)

        size_kb = round(os.path.getsize(backup_name) / 1024, 1)
        await bot.send_document(
            ADMIN_ID,
            FSInputFile(backup_name),
            caption=(
                f"💾 <b>Бэкап базы данных</b>\n"
                f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
                f"📌 Причина: {reason}\n"
                f"📦 Размер: {size_kb} KB"
            )
        )
        logger.info(f"Бэкап отправлен: {backup_name}")

        files = sorted(
            [f for f in os.listdir(BACKUP_DIR) if f.startswith("shop_")],
            reverse=True
        )
        for old in files[10:]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except Exception:
                pass
        return True
    except Exception as e:
        logger.error(f"Ошибка бэкапа: {e}")
        return False

async def auto_backup_loop():
    await asyncio.sleep(60)
    while True:
        try:
            await send_backup(f"авто (раз в {BACKUP_INTERVAL_MIN} мин)")
        except Exception as e:
            logger.error(f"Автобэкап упал: {e}")
        await asyncio.sleep(BACKUP_INTERVAL_MIN * 60)

# ===== РАССЫЛКА =====
async def run_broadcast(admin_id: int, draft: dict, users: list):
    sent = 0
    blocked = 0
    failed = 0

    for uid in users:
        try:
            if draft["type"] == "text":
                await bot.send_message(uid, draft["text"])
            elif draft["type"] == "photo":
                await bot.send_photo(uid, draft["photo_id"], caption=draft.get("caption") or None)
            elif draft["type"] == "video":
                await bot.send_video(uid, draft["video_id"], caption=draft.get("caption") or None)
            sent += 1
        except TelegramRetryAfter as e:
            logger.warning(f"Flood wait {e.retry_after}s")
            await asyncio.sleep(e.retry_after + 1)
            try:
                if draft["type"] == "text":
                    await bot.send_message(uid, draft["text"])
                elif draft["type"] == "photo":
                    await bot.send_photo(uid, draft["photo_id"], caption=draft.get("caption") or None)
                elif draft["type"] == "video":
                    await bot.send_video(uid, draft["video_id"], caption=draft.get("caption") or None)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "chat not found" in err or "deactivated" in err:
                blocked += 1
            else:
                failed += 1
                logger.warning(f"Рассылка {uid} не удалась: {e}")
        await asyncio.sleep(0.05)

    temp_broadcast.pop(admin_id, None)

    try:
        await bot.send_message(
            admin_id,
            f"📢 <b>Рассылка завершена</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Доставлено: {sent}\n"
            f"🚫 Заблокировали: {blocked}\n"
            f"❌ Ошибок: {failed}\n"
            f"👥 Всего: {len(users)}"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить отчёт: {e}")

# ===== СТАРТ =====
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_states.pop(message.from_user.id, None)
    await save_user(message.from_user)
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n"
        f"Это S Mod Shop — магазин вейпов.\n"
        f"Выбери действие:",
        reply_markup=main_menu()
    )

# ===== КАТАЛОГ =====
@dp.message(F.text == "🛒 Заказать")
async def catalog(message: types.Message):
    await save_user(message.from_user)
    products = await fetch_products()
    if not products:
        await message.answer("Пока товаров нет. Загляни позже!")
        return
    await message.answer("📦 Выбери товар:", reply_markup=build_catalog_keyboard(products))

@dp.callback_query(F.data == "back_catalog")
async def back_catalog(callback: types.CallbackQuery):
    products = await fetch_products()
    if not products:
        await callback.message.edit_text("Пока товаров нет.")
        await callback.answer()
        return
    await callback.message.edit_text("📦 Выбери товар:", reply_markup=build_catalog_keyboard(products))
    await callback.answer()

@dp.callback_query(F.data.startswith("view_"))
async def view_product(callback: types.CallbackQuery):
    try:
        product_id = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return

    p = await fetch_product(product_id)
    if not p:
        await callback.answer("Товар не найден", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🛒 Купить", callback_data=f"buy_{product_id}"))
    builder.add(InlineKeyboardButton(text="🧺 В корзину", callback_data=f"cart_{product_id}"))
    builder.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_catalog"))

    await callback.message.edit_text(
        f"📦 {p[0]}\n💰 Цена: {p[1]} ₽\n📝 {p[2]}",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    try:
        product_id = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return

    p = await fetch_product(product_id)
    if not p:
        await callback.answer("Товар не найден", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders (user_id, product, total, status) VALUES (?, ?, ?, ?)",
            (callback.from_user.id, p[0], p[1], "новый")
        )
        await db.commit()

    await callback.message.answer(f"✅ Заказ оформлен: {p[0]} за {p[1]} ₽.")
    if is_admin(ADMIN_ID):
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🛒 Новый заказ!\n"
                f"Клиент: {callback.from_user.full_name} ({safe_username(callback.from_user)})\n"
                f"🆔 ID: <code>{callback.from_user.id}</code>\n"
                f"Товар: {p[0]}\nЦена: {p[1]} ₽\n\n"
                f"<a href='tg://user?id={callback.from_user.id}'>💬 Написать клиенту</a>"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}")
    await callback.answer()

@dp.callback_query(F.data.startswith("cart_"))
async def add_to_cart(callback: types.CallbackQuery):
    try:
        product_id = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return

    p = await fetch_product(product_id)
    if not p:
        await callback.answer("Товар не найден", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM cart WHERE user_id = ? AND product_id = ?",
            (callback.from_user.id, product_id)
        )
        existing = await cur.fetchone()
        await cur.close()
        if existing:
            await callback.answer("Уже в корзине_user", show_alert=True)
           .id return
        await db.execute(
            "INSERT, INTO cart (user))
_id, product_id) VALUES (?, ?)",
                   (callback.from_user.id, product await_id)
        )
        await db.commit()

    db await callback.answer("✅.commit Добавлено в корзину", show()
_alert=True)

# =====    КОРЗИНА await =====
@dp.message(F.text == " callback🧺 Корзина")
async def show_cart.message(message: types.Message):
   .edit await save_user(message.from_user_text)
    async with aiosqlite(".connect(DB_PATH) as db🧺:
        cur = await db.execute('''SELECT Кор c.id, p.name, p.price FROM cart c
                                  JOIN products p ON c.product_id = p.id
                                  WHERE c.user_id =зи ?''', (message.from_user.id,))
        items = awaitна cur.fetchall()
        await cur.close()

    if очи not items:
щ        await message.answer("🧺 Кореназина пу.")
ста.")
        return

    text =    "🧺 Твоя кор awaitзина:\n"
    total = 0
 callback    for i in items:
        text += f."#{i[0]} {answeri[1]} — {i()

[2]} ₽\n"
@        total += i[2]
dp    text += f"\.calln💰 Итого: {totalback} ₽"

    builder = In_querylineKeyboardBuilder()
    builder(F.add(InlineKeyboardButton(text="✅ Оформить заказ",.data callback_data="checkout"))
    builder.add(InlineKeyboardButton(text ==="🗑 Очистить "",check callback_data="clear_cart"))
    builderout.adjust(1)
    await message")
.answer(text, reply_markup=builderasync.as_markup())

@dp def.callback_query(F.data == "clear_cart checkout")
async def clear_cart(callback(c: types.CallbackQuery):
    asyncallback with aiosqlite.connect(DB_PATH:) as db:
        await db types.execute("DELETE FROM cart WHERE user.C_id = ?", (callback.fromallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('''SELECT p.name, p.price FROM cart c
                                  JOIN products p ON c.product_id = p.id
                                  WHERE c.user_id = ?''', (callback.from_user.id,))
        items = await cur.fetchall()
        await cur.close()

        if not items:
            await callback.answer("Корзина пуста", show_alert=True)
            return

        products_text = ", ".join([f"{i[0]} ({i[1]} ₽)" for i in items])
        total = sum(i[1] for i in items)

        await db.execute(
            "INSERT INTO orders (user_id, product, total, status) VALUES (?, ?, ?, ?)",
            (callback.from_user.id, products_text, total, "новый")
        )
        await db.execute("DELETE FROM cart WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()

    await callback.message.answer(f"✅ Заказ оформлен на {total} ₽:\n{products_text}")
    if is_admin(ADMIN_ID):
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🛒 Новый заказ (корзина)!\n"
                f"Клиент: {callback.from_user.full_name} ({safe_username(callback.from_user)})\n"
                f"🆔 ID: <code>{callback.from_user.id}</code>\n"
                f"Товары: {products_text}\n"
                f"Итого: {total} ₽\n\n"
                f"<a href='tg://user?id={callback.from_user.id}'>💬 Написать клиенту</a>"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}")
    await callback.answer()

# ===== ПОИСК, ПРОФИЛЬ, ПОДДЕРЖКА =====
@dp.message(F.text == "🔍 Поиск")
async def search_start(message: types.Message):
    await save_user(message.from_user)
    user_states[message.from_user.id] = "awaiting_search"
    await message.answer("🔍 Напиши название товара для поиска:")

@dp.message(F.text == "👤 Профиль")
async def profile(message: types.Message):
    await save_user(message.from_user)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (message.from_user.id,))
        row = await cur.fetchone()
        await cur.close()
        count = row[0]
    await message.answer(
        f"👤 Твой профиль\n"
        f"ID: {message.from_user.id}\n"
        f"Имя: {message.from_user.full_name}\n"
        f"Заказов: {count}"
    )

@dp.message(F.text == "🆘 Поддержка")
async def support(message: types.Message):
    await save_user(message.from_user)
    user_states[message.from_user.id] = "awaiting_support"
    await message.answer("🆘 Напиши свой вопрос, я передам админу.")

# ===== БЭКАП =====
@dp.message(Command("backup"))
async def backup_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("💾 Готовлю бэкап...")
    ok = await send_backup("ручной (/backup)")
    if not ok:
        await message.answer("❌ Не удалось сделать бэкап.")

@dp.message(F.text == "💾 Бэкап")
async def backup_button(message: types.Message):
    if not is_admin_state(message.from_user.id):
        await message.answer("⛔ Сначала авторизуйся: /admin")
        return
    await message.answer("💾 Готовлю бэкап...")
    ok = await send_backup("ручной (кнопка)")
    if not ok:
        await message.answer("❌ Не удалось сделать бэкап.")

@dp.message(Command("restore"))
async def restore_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.answer("❌ Ответь на файл shop.db командой /restore")
        return

    doc = message.reply_to_message.document
    if not doc.file_name.endswith(".db"):
        await message.answer("❌ Файл должен быть .db")
        return

    try:
        file = await bot.get_file(doc.file_id)
        tmp_path = "restore_tmp.db"
        await bot.download_file(file.file_path, tmp_path)

        async with aiosqlite.connect(tmp_path) as test_db:
            cur = await test_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = await cur.fetchall()
            await cur.close()
        if not tables:
            await message.answer("❌ Файл пустой или не SQLite")
            os.remove(tmp_path)
            return

        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, f"{DB_PATH}.old")
        shutil.move(tmp_path, DB_PATH)

        await message.answer(
            f"✅ База восстановлена!\n"
            f"📦 Таблиц: {len(tables)}\n"
            f"Старая база сохранена как {DB_PATH}.old"
        )
        logger.info("База восстановлена из бэкапа")
    except Exception as e:
        await message.answer(f"❌ Ошибка восстановления: {e}")
        logger.error(f"Ошибка restore: {e}")

# ===== РАССЫЛКА =====
@dp.message(F.text == "📢 Рассылка")
async def broadcast_start(message: types.Message):
    if not is_admin_state(message.from_user.id):
        await message.answer("⛔ Сначала авторизуйся: /admin")
        return
    user_states[message.from_user.id] = "broadcast_content"
    await message.answer(
        "📢 <b>Рассылка</b>\n\n"
        "Отправь текст, фото или видео — я разошлю это всем, "
        "кто когда-либо запускал бота.\n\n"
        "❌ Отмена: /cancel"
    )

@dp.message(Command("cancel"))
async def cancel_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    user_states.pop(message.from_user.id, None)
    temp_broadcast.pop(message.from_user.id, None)
    await message.answer("Отменено.", reply_markup=admin_menu())

@dp.callback_query(F.data == "broadcast_send")
async def broadcast_send(callback: types.CallbackQuery):
    if not is_admin_state(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    draft = temp_broadcast.get(callback.from_user.id)
    if not draft:
        await callback.answer("Черновик потерян", show_alert=True)
        return

    users = await fetch_all_users()
    if not users:
        await callback.message.edit_text("❌ Нет пользователей для рассылки.")
        await callback.answer()
        return

    await callback.message.edit_text(
        f"📢 Рассылка запущена для {len(users)} пользователей...\n"
        f"Отчёт придёт по завершении."
    )
    await callback.answer()
    asyncio.create_task(run_broadcast(callback.from_user.id, draft, users))

@dp.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: types.CallbackQuery):
    temp_broadcast.pop(callback.from_user.id, None)
    user_states.pop(callback.from_user.id, None)
    await callback.message.edit_text("❌ Рассылка отменена.")
    await callback.answer()

# ===== АДМИНКА =====
@dp.message(Command("admin"))
async def admin_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У тебя нет доступа.")
        return
    user_states[message.from_user.id] = "awaiting_password"
    await message.answer("🔐 Введи пароль:")

@dp.message(F.text == "🔙 Выйти")
async def exit_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    admin_sessions.pop(message.from_user.id, None)
    user_states.pop(message.from_user.id, None)
    await message.answer("Вышел из админки.", reply_markup=main_menu())

@dp.message(F.text == "➕ Добавить товар")
async def add_product(message: types.Message):
    if not is_admin_state(message.from_user.id):
        await message.answer("⛔ Сначала авторизуйся: /admin")
        return
    user_states[message.from_user.id] = "product_name"
    await message.answer("📝 Введи название товара:")

@dp.message(F.text == "🗑 Удалить товар")
async def delete_product_prompt(message: types.Message):
    if not is_admin_state(message.from_user.id):
        await message.answer("⛔ Сначала авторизуйся: /admin")
        return
    products = await fetch_products()
    if not products:
        await message.answer("Товаров нет.")
        return
    text = "🗑 Выбери ID для удаления:\n" + "\n".join([f"#{p[0]} {p[1]}" for p in products])
    user_states[message.from_user.id] = "delete_product"
    await message.answer(text + "\n\nВведи ID:")

@dp.message(F.text == "📦 Товары")
async def list_products(message: types.Message):
    if not is_admin_state(message.from_user.id):
        await message.answer("⛔ Сначала авторизуйся: /admin")
        return
    products = await fetch_products()
    if not products:
        await message.answer("Товаров нет.")
        return
    text = "📦 Товары:\n" + "\n".join([f"#{p[0]} {p[1]} — {p[2]} ₽ ({p[3]})" for p in products])
    await message.answer(text)

@dp.message(F.text == "📋 Заказы")
async def list_orders(message: types.Message):
    if not is_admin_state(message.from_user.id):
        await message.answer("⛔ Сначала авторизуйся: /admin")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, user_id, product, total, status FROM orders ORDER BY id DESC LIMIT 20")
        orders = await cur.fetchall()
        await cur.close()
    if not orders:
        await message.answer("Заказов нет.")
        return
    text = "📋 Последние заказы:\n" + "\n".join(
        [f"#{o[0]} | user {o[1]} | {o[2]} | {o[3]} ₽ | {o[4]}" for o in orders]
    )
    await message.answer(text)

@dp.message(F.text == "💬 Тикеты")
async def list_tickets(message: types.Message):
    if not is_admin_state(message.from_user.id):
        await message.answer("⛔ Сначала авторизуйся: /admin")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, user_id, message, answer FROM tickets ORDER BY id DESC LIMIT 20")
        tickets = await cur.fetchall()
        await cur.close()
    if not tickets:
        await message.answer("Тикетов нет.")
        return
    text = "💬 Тикеты:\n"
    for t in tickets:
        text += f"#{t[0]} | user {t[1]}\n❓ {t[2]}\n💬 {t[3] or '—'}\n\n"
    await message.answer(text)

@dp.message(F.text == "📊 Состояние бота")
async def bot_status(message: types.Message):
    if not is_admin_state(message.from_user.id):
        await message.answer("⛔ Сначала авторизуйся: /admin")
        return
    try:
        ram_used = "н/д"
        if sys.platform != "win32":
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            ram_used = f"{round(usage.ru_maxrss / 1024, 1)} MB"

        uptime = int(time.time() - START_TIME)
        hours = uptime // 3600
        minutes = (uptime % 3600) // 60

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT COUNT(*) FROM products")
            products = (await cur.fetchone())[0]
            await cur.close()
            cur = await db.execute("SELECT COUNT(*) FROM orders")
            orders = (await cur.fetchone())[0]
            await cur.close()
            cur = await db.execute("SELECT COUNT(*) FROM tickets")
            tickets = (await cur.fetchone())[0]
            await cur.close()
            cur = await db.execute("SELECT COUNT(*) FROM users")
            users_count = (await cur.fetchone())[0]
            await cur.close()

        db_size = round(os.path.getsize(DB_PATH) / 1024, 1) if os.path.exists(DB_PATH) else 0

        text = (
            f"📊 Состояние бота\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 RAM: {ram_used}\n"
            f"⏱ Uptime: {hours}ч {minutes}мин\n"
            f"💾 БД: {db_size} KB\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Юзеров: {users_count}\n"
            f"📦 Товаров: {products}\n"
            f"📋 Заказов: {orders}\n"
            f"💬 Тикетов: {tickets}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Бот работает стабильно"
        )
        await message.answer(text)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("answer"))
async def answer_ticket(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = message.text.split(" ", 2)
        ticket_id = int(parts[1])
        answer_text = parts[2]
    except (IndexError, ValueError):
        await message.answer("Формат: /answer ID текст")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM tickets WHERE id = ?", (ticket_id,))
        row = await cur.fetchone()
        await cur.close()
        if not row:
            await message.answer("Тикет не найден")
            return
        await db.execute("UPDATE tickets SET answer = ? WHERE id = ?", (answer_text, ticket_id))
        await db.commit()

    try:
        await bot.send_message(row[0], f"📩 Ответ от админа:\n{answer_text}")
        await message.answer("✅ Ответ отправлен.")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить: {e}")

# ===== МЕДИА (только для рассылки) =====
@dp.message(F.photo | F.video)
async def handle_media(message: types.Message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    if state != "broadcast_content":
        return
    if not is_admin_state(user_id):
        return

    if message.photo:
        draft = {"type": "photo", "photo_id": message.photo[-1].file_id,
                 "caption": message.caption or ""}
    else:
        draft = {"type": "video", "video_id": message.video.file_id,
                 "caption": message.caption or ""}

    temp_broadcast[user_id] = draft
    user_states.pop(user_id, None)

    users = await fetch_all_users()
    if draft["type"]
