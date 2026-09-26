import asyncio
import logging
import os
import sys
import time
from threading import Thread
from flask import Flask

import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===== КОНФИГ =====
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "96266")
DB_PATH = "shop.db"

START_TIME = time.time()

# ===== ЛОГИ =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ===== ПРОВЕРКИ =====
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в Environment Variables")
if ADMIN_ID == 0:
    logger.warning("ADMIN_ID = 0. Админ-панель будет недоступна.")

# ===== FLASK (для Render Web Service) =====
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_flask():
    try:
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
    except Exception as e:
        logger.error(f"Flask error: {e}")

# ===== КЛАВИАТУРЫ =====
BUTTONS = {
    "🛒 Заказать", "🔍 Поиск", "🧺 Корзина", "👤 Профиль", "🆘 Поддержка",
    "➕ Добавить товар", "📦 Товары", "🗑 Удалить товар",
    "📋 Заказы", "💬 Тикеты", "📊 Состояние бота", "🔙 Выйти",
}

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
        [KeyboardButton(text="📊 Состояние бота"), KeyboardButton(text="🔙 Выйти")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ===== СОСТОЯНИЯ =====
user_states = {}
temp_product = {}

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
        await db.commit()

# ===== ХЕЛПЕРЫ =====
def safe_username(user: types.User) -> str:
    return f"@{user.username}" if user.username else f"id{user.id}"

async def fetch_products():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name, price, category FROM products") as cur:
            return await cur.fetchall()

async def fetch_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT name, price, description FROM products WHERE id = ?", (product_id,)
        ) as cur:
            return await cur.fetchone()

def build_catalog_keyboard(products):
    builder = InlineKeyboardBuilder()
    for p in products:
        builder.add(InlineKeyboardButton(
            text=f"{p[1]} — {p[2]} ₽",
            callback_data=f"view_{p[0]}"
        ))
    builder.adjust(1)
    return builder.as_markup()

# ===== СТАРТ =====
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_states.pop(message.from_user.id, None)
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n"
        f"Это S Mod Shop — магазин вейпов.\n"
        f"Выбери действие:",
        reply_markup=main_menu()
    )

# ===== КАТАЛОГ =====
@dp.message(F.text == "🛒 Заказать")
async def catalog(message: types.Message):
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
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🛒 Новый заказ!\n"
                f"Клиент: {callback.from_user.full_name} ({safe_username(callback.from_user)})\n"
                f"Товар: {p[0]}\nЦена: {p[1]} ₽"
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
        async with db.execute(
            "SELECT id FROM cart WHERE user_id = ? AND product_id = ?",
            (callback.from_user.id, product_id)
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            await callback.answer("Уже в корзине", show_alert=True)
            return
        await db.execute(
            "INSERT INTO cart (user_id, product_id) VALUES (?, ?)",
            (callback.from_user.id, product_id)
        )
        await db.commit()

    await callback.answer("✅ Добавлено в корзину", show_alert=True)

# ===== КОРЗИНА =====
@dp.message(F.text == "🧺 Корзина")
async def show_cart(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''SELECT c.id, p.name, p.price FROM cart c
                                 JOIN products p ON c.product_id = p.id
                                 WHERE c.user_id = ?''', (message.from_user.id,)) as cur:
            items = await cur.fetchall()

    if not items:
        await message.answer("🧺 Корзина пуста.")
        return

    text = "🧺 Твоя корзина:\n"
    total = 0
    for i in items:
        text += f"#{i[0]} {i[1]} — {i[2]} ₽\n"
        total += i[2]
    text += f"\n💰 Итого: {total} ₽"

    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout"))
    builder.add(InlineKeyboardButton(text="🗑 Очистить", callback_data="clear_cart"))
    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "clear_cart")
async def clear_cart(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM cart WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.message.edit_text("🧺 Корзина очищена.")
    await callback.answer()

@dp.callback_query(F.data == "checkout")
async def checkout(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''SELECT p.name, p.price FROM cart c
                                 JOIN products p ON c.product_id = p.id
                                 WHERE c.user_id = ?''', (callback.from_user.id,)) as cur:
            items = await cur.fetchall()

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
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🛒 Новый заказ (корзина)!\n"
                f"Клиент: {callback.from_user.full_name} ({safe_username(callback.from_user)})\n"
                f"Товары: {products_text}\n"
                f"Итого: {total} ₽"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}")
    await callback.answer()

# ===== ПОИСК, ПРОФИЛЬ, ПОДДЕРЖКА =====
@dp.message(F.text == "🔍 Поиск")
async def search_start(message: types.Message):
    user_states[message.from_user.id] = "awaiting_search"
    await message.answer("🔍 Напиши название товара для поиска:")

@dp.message(F.text == "👤 Профиль")
async def profile(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (message.from_user.id,)) as cur:
            count = (await cur.fetchone())[0]
    await message.answer(
        f"👤 Твой профиль\n"
        f"ID: {message.from_user.id}\n"
        f"Имя: {message.from_user.full_name}\n"
        f"Заказов: {count}"
    )

@dp.message(F.text == "🆘 Поддержка")
async def support(message: types.Message):
    user_states[message.from_user.id] = "awaiting_support"
    await message.answer("🆘 Напиши свой вопрос, я передам админу.")

# ===== АДМИНКА =====
@dp.message(Command("admin"))
async def admin_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У тебя нет доступа.")
        return
    user_states[message.from_user.id] = "awaiting_password"
    await message.answer("🔐 Введи пароль:")

@dp.message(F.text == "🔙 Выйти")
async def exit_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    user_states.pop(message.from_user.id, None)
    await message.answer("Вышел из админки.", reply_markup=main_menu())

@dp.message(F.text == "➕ Добавить товар")
async def add_product(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    user_states[message.from_user.id] = "product_name"
    await message.answer("📝 Введи название товара:")

@dp.message(F.text == "🗑 Удалить товар")
async def delete_product_prompt(message: types.Message):
    if message.from_user.id != ADMIN_ID:
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
    if message.from_user.id != ADMIN_ID:
        return
    products = await fetch_products()
    if not products:
        await message.answer("Товаров нет.")
        return
    text = "📦 Товары:\n" + "\n".join([f"#{p[0]} {p[1]} — {p[2]} ₽ ({p[3]})" for p in products])
    await message.answer(text)

@dp.message(F.text == "📋 Заказы")
async def list_orders(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, user_id, product, total, status FROM orders ORDER BY id DESC LIMIT 20") as cur:
            orders = await cur.fetchall()
    if not orders:
        await message.answer("Заказов нет.")
        return
    text = "📋 Последние заказы:\n" + "\n".join(
        [f"#{o[0]} | user {o[1]} | {o[2]} | {o[3]} ₽ | {o[4]}" for o in orders]
    )
    await message.answer(text)

@dp.message(F.text == "💬 Тикеты")
async def list_tickets(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, user_id, message, answer FROM tickets ORDER BY id DESC LIMIT 20") as cur:
            tickets = await cur.fetchall()
    if not tickets:
        await message.answer("Тикетов нет.")
        return
    text = "💬 Тикеты:\n"
    for t in tickets:
        text += f"#{t[0]} | user {t[1]}\n❓ {t[2]}\n💬 {t[3] or '—'}\n\n"
    await message.answer(text)

@dp.message(F.text == "📊 Состояние бота")
async def bot_status(message: types.Message):
    if message.from_user.id != ADMIN_ID:
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
            async with db.execute("SELECT COUNT(*) FROM products") as cur:
                products = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM orders") as cur:
                orders = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM tickets") as cur:
                tickets = (await cur.fetchone())[0]

        text = (
            f"📊 Состояние бота\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 RAM: {ram_used}\n"
            f"⏱ Uptime: {hours}ч {minutes}мин\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
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
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split(" ", 2)
        ticket_id = int(parts[1])
        answer_text = parts[2]
    except (IndexError, ValueError):
        await message.answer("Формат: /answer ID текст")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM tickets WHERE id = ?", (ticket_id,)) as cur:
            row = await cur.fetchone()
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

# ===== ВВОД ТЕКСТА (В САМОМ КОНЦЕ!) =====
@dp.message(F.text & ~F.text.startswith("/") & ~F.text.in_(BUTTONS))
async def handle_input(message: types.Message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    # Пароль
    if state == "awaiting_password":
        if message.text.strip() == ADMIN_PASSWORD:
            user_states[user_id] = "admin"
            await message.answer("🔧 Доступ разрешён. Админ-панель:", reply_markup=admin_menu())
        else:
            user_states.pop(user_id, None)
            await message.answer("❌ Неверный пароль.")
        return

    # Добавление товара
    if state == "product_name":
        temp_product[user_id] = {"name": message.text}
        user_states[user_id] = "product_price"
        await message.answer("💰 Введи цену (только число):")
        return

    if state == "product_price":
        if not message.text.strip().isdigit():
            await message.answer("❌ Цена должна быть числом. Попробуй снова:")
            return
        temp_product[user_id]["price"] = int(message.text.strip())
        user_states[user_id] = "product_desc"
        await message.answer("📝 Введи описание:")
        return

    if state == "product_desc":
        temp_product[user_id]["desc"] = message.text
        user_states[user_id] = "product_category"
        await message.answer("📂 Введи категорию (моды, жидкости, аксессуары):")
        return

    if state == "product_category":
        temp_product[user_id]["category"] = message.text
        p = temp_product.pop(user_id)
        user_states[user_id] = "admin"
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO products (name, price, description, category) VALUES (?, ?, ?, ?)",
                    (p["name"], p["price"], p["desc"], p["category"])
                )
                await db.commit()
            await message.answer(f"✅ Товар '{p['name']}' добавлен!", reply_markup=admin_menu())
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}", reply_markup=admin_menu())
        return

    # Удаление товара
    if state == "delete_product":
        user_states[user_id] = "admin"
        if not message.text.strip().isdigit():
            await message.answer("❌ ID должен быть числом.", reply_markup=admin_menu())
            return
        product_id = int(message.text.strip())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
            await db.execute("DELETE FROM cart WHERE product_id = ?", (product_id,))
            await db.commit()
        await message.answer(f"✅ Товар #{product_id} удалён.", reply_markup=admin_menu())
        return

    # Поиск
    if state == "awaiting_search":
        user_states.pop(user_id, None)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, name, price FROM products WHERE name LIKE ?",
                (f"%{message.text}%",)
            ) as cur:
                results = await cur.fetchall()
        if not results:
            await message.answer("❌ Ничего не найдено.")
            return
        text = "🔍 Найдено:\n" + "\n".join([f"#{r[0]} {r[1]} — {r[2]} ₽" for r in results])
        await message.answer(text)
        return

    # Поддержка
    if state == "awaiting_support":
        user_states.pop(user_id, None)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "INSERT INTO tickets (user_id, message) VALUES (?, ?)",
                (user_id, message.text)
            ) as cur:
                ticket_id = cur.lastrowid
            await db.commit()
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"🆘 Тикет #{ticket_id}\n"
                    f"От: {message.from_user.full_name} ({safe_username(message.from_user)})\n"
                    f"Сообщение: {message.text}\n\n"
                    f"Ответь: /answer {ticket_id} текст"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить тикет админу: {e}")
        await message.answer("✅ Сообщение отправлено. Жди ответа.")
        return

# ===== ГЛАВНАЯ =====
async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    Thread(target=run_flask, daemon=True).start()
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
