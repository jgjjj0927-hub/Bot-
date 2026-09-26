import asyncio
import logging
import sqlite3
import os
from threading import Thread
from flask import Flask

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_PASSWORD = "96266"

# ===== БАЗА ДАННЫХ =====
conn = sqlite3.connect('shop.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS products
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price TEXT, description TEXT, category TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS orders
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product TEXT, status TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS cart
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_id INTEGER)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS tickets
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, message TEXT, answer TEXT)''')
conn.commit()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== ФЕЙКОВЫЙ ВЕБ-СЕРВЕР (для Render) =====
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))

# ===== КЛАВИАТУРЫ =====
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
        [KeyboardButton(text="📦 Товары"), KeyboardButton(text="📋 Заказы")],
        [KeyboardButton(text="💬 Тикеты"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🔙 Выйти")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ===== СОСТОЯНИЯ =====
user_states = {}  # {user_id: "awaiting_password" | "awaiting_product" | "awaiting_search"}

# ===== ЗАПУСК =====
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_states.pop(message.from_user.id, None)
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n"
        f"Это S Mod Shop — магазин вейпов.\n"
        f"Выбери действие:",
        reply_markup=main_menu()
    )

# ===== ЗАКАЗАТЬ =====
@dp.message(F.text == "🛒 Заказать")
async def catalog(message: types.Message):
    cursor.execute("SELECT id, name, price, category FROM products")
    products = cursor.fetchall()
    if not products:
        await message.answer("Пока товаров нет. Загляни позже!")
        return

    builder = InlineKeyboardBuilder()
    for p in products:
        builder.add(InlineKeyboardButton(
            text=f"{p[1]} — {p[2]} ₽",
            callback_data=f"view_{p[0]}"
        ))
    builder.adjust(1)
    await message.answer("📦 Выбери товар:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("view_"))
async def view_product(callback: types.CallbackQuery):
    product_id = callback.data.split("_")[1]
    cursor.execute("SELECT name, price, description FROM products WHERE id = ?", (product_id,))
    p = cursor.fetchone()
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

@dp.callback_query(F.data == "back_catalog")
async def back_catalog(callback: types.CallbackQuery):
    cursor.execute("SELECT id, name, price FROM products")
    products = cursor.fetchall()
    builder = InlineKeyboardBuilder()
    for p in products:
        builder.add(InlineKeyboardButton(text=f"{p[1]} — {p[2]} ₽", callback_data=f"view_{p[0]}"))
    builder.adjust(1)
    await callback.message.edit_text("📦 Выбери товар:", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    product_id = callback.data.split("_")[1]
    cursor.execute("SELECT name, price FROM products WHERE id = ?", (product_id,))
    p = cursor.fetchone()
    if not p:
        await callback.answer("Товар не найден", show_alert=True)
        return

    cursor.execute("INSERT INTO orders (user_id, product, status) VALUES (?, ?, ?)",
                   (callback.from_user.id, p[0], "новый"))
    conn.commit()

    await callback.message.answer(f"✅ Заказ оформлен: {p[0]} за {p[1]} ₽.\nАдмин свяжется с тобой.")
    await bot.send_message(
        ADMIN_ID,
        f"🛒 Новый заказ!\n"
        f"Клиент: {callback.from_user.full_name} (@{callback.from_user.username})\n"
        f"Товар: {p[0]}\nЦена: {p[1]} ₽"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("cart_"))
async def add_to_cart(callback: types.CallbackQuery):
    product_id = callback.data.split("_")[1]
    cursor.execute("INSERT INTO cart (user_id, product_id) VALUES (?, ?)",
                   (callback.from_user.id, product_id))
    conn.commit()
    await callback.answer("✅ Добавлено в корзину", show_alert=True)

# ===== КОРЗИНА =====
@dp.message(F.text == "🧺 Корзина")
async def show_cart(message: types.Message):
    cursor.execute('''SELECT c.id, p.name, p.price FROM cart c
                      JOIN products p ON c.product_id = p.id
                      WHERE c.user_id = ?''', (message.from_user.id,))
    items = cursor.fetchall()
    if not items:
        await message.answer("🧺 Корзина пуста.")
        return

    text = "🧺 Твоя корзина:\n"
    total = 0
    for i in items:
        text += f"#{i[0]} {i[1]} — {i[2]} ₽\n"
        try:
            total += int(i[2].replace("₽", "").strip())
        except:
            pass
    text += f"\n💰 Итого: {total} ₽"

    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout"))
    builder.add(InlineKeyboardButton(text="🗑 Очистить", callback_data="clear_cart"))
    builder.adjust(1)

    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "clear_cart")
async def clear_cart(callback: types.CallbackQuery):
    cursor.execute("DELETE FROM cart WHERE user_id = ?", (callback.from_user.id,))
    conn.commit()
    await callback.message.edit_text("🧺 Корзина очищена.")
    await callback.answer()

@dp.callback_query(F.data == "checkout")
async def checkout(callback: types.CallbackQuery):
    cursor.execute('''SELECT p.name, p.price FROM cart c
                      JOIN products p ON c.product_id = p.id
                      WHERE c.user_id = ?''', (callback.from_user.id,))
    items = cursor.fetchall()
    if not items:
        await callback.answer("Корзина пуста", show_alert=True)
        return

    products_text = ", ".join([f"{i[0]} ({i[1]} ₽)" for i in items])
    cursor.execute("INSERT INTO orders (user_id, product, status) VALUES (?, ?, ?)",
                   (callback.from_user.id, products_text, "новый"))
    conn.commit()
    cursor.execute("DELETE FROM cart WHERE user_id = ?", (callback.from_user.id,))
    conn.commit()

    await callback.message.answer(f"✅ Заказ оформлен:\n{products_text}")
    await bot.send_message(
        ADMIN_ID,
        f"🛒 Новый заказ (корзина)!\n"
        f"Клиент: {callback.from_user.full_name} (@{callback.from_user.username})\n"
        f"Товары: {products_text}"
    )
    await callback.answer()

# ===== ПОИСК =====
@dp.message(F.text == "🔍 Поиск")
async def search_start(message: types.Message):
    user_states[message.from_user.id] = "awaiting_search"
    await message.answer("🔍 Напиши название товара для поиска:")

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
    user_states[message.from_user.id] = "awaiting_support"
    await message.answer("🆘 Напиши свой вопрос, я передам админу.")

# ===== ОБРАБОТКА ВВОДА =====
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_input(message: types.Message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    # Поиск
    if state == "awaiting_search":
        user_states.pop(user_id, None)
        cursor.execute("SELECT id, name, price FROM products WHERE name LIKE ?",
                       (f"%{message.text}%",))
        results = cursor.fetchall()
        if not results:
            await message.answer("❌ Ничего не найдено.")
            return
        text = "🔍 Найдено:\n"
        for r in results:
            text += f"#{r[0]} {r[1]} — {r[2]} ₽\n"
        await message.answer(text)
        return

    # Поддержка
    if state == "awaiting_support":
        user_states.pop(user_id, None)
        cursor.execute("INSERT INTO tickets (user_id, message) VALUES (?, ?)",
                       (user_id, message.text))
        conn.commit()
        ticket_id = cursor.lastrowid
        await bot.send_message(
            ADMIN_ID,
            f"🆘 Тикет #{ticket_id}\n"
            f"От: {message.from_user.full_name} (@{message.from_user.username})\n"
            f"Сообщение: {message.text}\n\n"
            f"Ответь: /answer {ticket_id} текст"
        )
        await message.answer("✅ Сообщение отправлено. Жди ответа.")
        return

    # Пароль админа
    if state == "awaiting_password":
        user_states.pop(user_id, None)
        if message.text.strip() == ADMIN_PASSWORD:
            await message.answer("🔧 Доступ разрешён. Админ-панель:", reply_markup=admin_menu())
        else:
            await message.answer("❌ Неверный пароль.")
        return

    # Добавление товара
    if state == "awaiting_product":
        user_states.pop(user_id, None)
        if user_id != ADMIN_ID:
            return
        try:
            parts = message.text.split("|")
            name, price, desc = parts[0].strip(), parts[1].strip(), parts[2].strip()
            category = parts[3].strip() if len(parts) > 3 else "без категории"
            cursor.execute("INSERT INTO products (name, price, description, category) VALUES (?, ?, ?, ?)",
                           (name, price, desc, category))
            conn.commit()
            await message.answer(f"✅ Товар '{name}' добавлен!")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}\nФормат: Название | Цена | Описание | Категория")
        return

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
    user_states[message.from_user.id] = "awaiting_product"
    await message.answer("Отправь товар:\nНазвание | Цена | Описание | Категория")

@dp.message(F.text == "📦 Товары")
async def list_products(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT id, name, price, category FROM products")
    products = cursor.fetchall()
    if not products:
        await message.answer("Товаров нет.")
        return
    text = "📦 Товары:\n"
    for p in products:
        text += f"#{p[0]} {p[1]} — {p[2]} ₽ ({p[3]})\n"
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
    text = "📋 Последние заказы:\n"
    for o in orders:
        text += f"#{o[0]} | user {o[1]} | {o[2]} | {o[3]}\n"
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
        text += f"#{t[0]} | user {t[1]}\n❓ {t[2]}\n💬 {t[3] or '—'}\n\n"
    await message.answer(text)

@dp.message(F.text == "📊 Статистика")
async def stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT COUNT(*) FROM products")
    products = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders")
    orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tickets")
    tickets = cursor.fetchone()[0]
    await message.answer(
        f"📊 Статистика:\n"
        f"📦 Товаров: {products}\n"
        f"📋 Заказов: {orders}\n"
        f"💬 Тикетов: {tickets}"
    )

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

# ===== ЗАПУСК =====
async def main():
    logging.basicConfig(level=logging.INFO)
    Thread(target=run_flask, daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
