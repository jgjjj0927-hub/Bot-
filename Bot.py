import asyncio
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# ===== БАЗА ДАННЫХ =====
conn = sqlite3.connect('shop.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS products
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price TEXT, description TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS orders
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product TEXT, status TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS tickets
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, message TEXT, answer TEXT)''')
conn.commit()

# ===== КЛАВИАТУРЫ =====
def main_menu():
    kb = [
        [KeyboardButton(text="🛒 Заказать")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🆘 Поддержка")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def admin_menu():
    kb = [
        [KeyboardButton(text="➕ Добавить товар")],
        [KeyboardButton(text="📦 Товары"), KeyboardButton(text="📋 Заказы")],
        [KeyboardButton(text="💬 Тикеты"), KeyboardButton(text="🔙 Выйти")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ===== ЗАПУСК =====
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\nЭто S Mod Shop.\nВыбери действие:",
        reply_markup=main_menu()
    )

# ===== ЗАКАЗАТЬ =====
@dp.message(F.text == "🛒 Заказать")
async def catalog(message: types.Message):
    cursor.execute("SELECT id, name, price FROM products")
    products = cursor.fetchall()
    if not products:
        await message.answer("Пока товаров нет. Загляни позже!")
        return

    builder = InlineKeyboardBuilder()
    for p in products:
        builder.add(InlineKeyboardButton(
            text=f"{p[1]} — {p[2]} ₽",
            callback_data=f"buy_{p[0]}"
        ))
    builder.adjust(1)
    await message.answer("Выбери товар:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    product_id = callback.data.split("_")[1]
    cursor.execute("SELECT name, price FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    cursor.execute("INSERT INTO orders (user_id, product, status) VALUES (?, ?, ?)",
                   (callback.from_user.id, product[0], "новый"))
    conn.commit()

    await callback.message.answer(
        f"✅ Заказ оформлен: {product[0]} за {product[1]} ₽.\n"
        f"Админ свяжется с тобой."
    )
    # Уведомляем админа
    await bot.send_message(
        ADMIN_ID,
        f"🛒 Новый заказ!\nКлиент: {callback.from_user.full_name} (@{callback.from_user.username})\n"
        f"Товар: {product[0]}\nЦена: {product[1]} ₽"
    )
    await callback.answer()

# ===== ПРОФИЛЬ =====
@dp.message(F.text == "👤 Профиль")
async def profile(message: types.Message):
    cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (message.from_user.id,))
    count = cursor.fetchone()[0]
    await message.answer(
        f"👤 Твой профиль\n"
        f"ID: {message.from_user.id}\n"
        f"Имя: {message.from_user.full_name}\n"
        f"Заказов: {count}"
    )

# ===== ПОДДЕРЖКА =====
@dp.message(F.text == "🆘 Поддержка")
async def support(message: types.Message):
    await message.answer("Напиши свой вопрос, я передам админу. Жди ответа здесь.")

@dp.message(F.text & ~F.text.startswith("/") & ~F.text.in_({"🛒 Заказать", "👤 Профиль", "🆘 Поддержка",
    "➕ Добавить товар", "📦 Товары", "📋 Заказы", "💬 Тикеты", "🔙 Выйти"}))
async def forward_to_admin(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    cursor.execute("INSERT INTO tickets (user_id, message) VALUES (?, ?)",
                   (message.from_user.id, message.text))
    conn.commit()
    ticket_id = cursor.lastrowid

    await bot.send_message(
        ADMIN_ID,
        f"🆘 Новый тикет #{ticket_id}\n"
        f"От: {message.from_user.full_name} (@{message.from_user.username})\n"
        f"Сообщение: {message.text}\n\n"
        f"Ответь командой: /answer {ticket_id} текст_ответа"
    )
    await message.answer("✅ Сообщение отправлено. Жди ответа.")

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

    cursor.execute("SELECT user_id FROM tickets WHERE id = ?", (ticket_id,))
    row = cursor.fetchone()
    if not row:
        await message.answer("Тикет не найден")
        return

    cursor.execute("UPDATE tickets SET answer = ? WHERE id = ?", (answer_text, ticket_id))
    conn.commit()

    await bot.send_message(row[0], f"📩 Ответ от админа:\n{answer_text}")
    await message.answer("✅ Ответ отправлен.")

# ===== АДМИН-ПАНЕЛЬ =====
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔧 Админ-панель:", reply_markup=admin_menu())

@dp.message(F.text == "🔙 Выйти")
async def exit_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Вышел из админки.", reply_markup=main_menu())

@dp.message(F.text == "➕ Добавить товар")
async def add_product_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Отправь товар в формате:\nНазвание | Цена | Описание")

@dp.message(F.text.contains("|"))
async def add_product(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split("|")
        name, price, desc = parts[0].strip(), parts[1].strip(), parts[2].strip()
        cursor.execute("INSERT INTO products (name, price, description) VALUES (?, ?, ?)",
                       (name, price, desc))
        conn.commit()
        await message.answer(f"✅ Товар '{name}' добавлен!")
    except Exception:
        await message.answer("❌ Ошибка. Формат: Название | Цена | Описание")

@dp.message(F.text == "📦 Товары")
async def list_products(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT id, name, price FROM products")
    products = cursor.fetchall()
    if not products:
        await message.answer("Товаров нет.")
        return
    text = "📦 Товары:\n" + "\n".join([f"#{p[0]} {p[1]} — {p[2]} ₽" for p in products])
    await message.answer(text)

@dp.message(F.text == "📋 Заказы")
async def list_orders(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT id, user_id, product, status FROM orders ORDER BY id DESC LIMIT 20")
    orders = cursor.fetchall()
    if not orders:
        await message.answer("Заказов нет.")
        return
    text = "📋 Заказы:\n" + "\n".join([f"#{o[0]} | user {o[1]} | {o[2]} | {o[3]}" for o in orders])
    await message.answer(text)

@dp.message(F.text == "💬 Тикеты")
async def list_tickets(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT id, user_id, message, answer FROM tickets ORDER BY id DESC LIMIT 20")
    tickets = cursor.fetchall()
    if not tickets:
        await message.answer("Тикетов нет.")
        return
    text = "💬 Тикеты:\n"
    for t in tickets:
        text += f"#{t[0]} | user {t[1]}\nВопрос: {t[2]}\nОтвет: {t[3] or '—'}\n\n"
    await message.answer(text)

# ===== ЗАПУСК =====
async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
