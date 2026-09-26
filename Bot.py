import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Токен будет браться из переменных окружения (безопасно)
import os
TOKEN = os.getenv("8788841972:AAEgMOObpUqOcA9Rcxz8OnuB6MejEVsBBHg")

# Админ ID (твой Telegram ID, узнать можно у @userinfobot)
ADMIN_ID = int(os.getenv("8224529558", "0"))

# Подключение к базе данных (файл создастся сам)
conn = sqlite3.connect('shop.db')
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS products
                  (id INTEGER PRIMARY KEY, name TEXT, price TEXT, description TEXT)''')
conn.commit()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Главное меню (Reply-кнопки)
def main_menu():
    kb = [
        [types.KeyboardButton(text="🛒 Заказать")],
        [types.KeyboardButton(text="👤 Профиль"), types.KeyboardButton(text="🆘 Поддержка")],
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\nЭто S Mod Shop.\nВыбери действие:",
        reply_markup=main_menu()
    )

@dp.message(F.text == "🛒 Заказать")
async def catalog(message: types.Message):
    # Здесь ты будешь брать товары из базы данных
    # Пока заглушка
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="Бокс-мод Drag X", callback_data="buy_1"))
    builder.add(InlineKeyboardButton(text="Мех-мод Mechlyfe", callback_data="buy_2"))
    builder.adjust(1)
    await message.answer("Товары в наличии:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    product_id = callback.data.split("_")[1]
    await callback.message.answer(f"Ты выбрал товар ID: {product_id}. Напиши админу для оплаты: @твой_ник")
    await callback.answer()

@dp.message(F.text == "👤 Профиль")
async def profile(message: types.Message):
    await message.answer(f"Твой ID: {message.from_user.id}\nЗаказов: 0 (пока)")

@dp.message(F.text == "🆘 Поддержка")
async def support(message: types.Message):
    await message.answer("Напиши свой вопрос, я передам админу.")

# Админ-команда (только для тебя)
@dp.message(F.text == "/admin", F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    await message.answer("Админ-панель:\n/add — добавить товар\n/list — список товаров")

@dp.message(F.text.startswith("/add"), F.from_user.id == ADMIN_ID)
async def add_product(message: types.Message):
    # Формат: /add Название | Цена | Описание
    try:
        parts = message.text[5:].split("|")
        name, price, desc = parts[0].strip(), parts[1].strip(), parts[2].strip()
        cursor.execute("INSERT INTO products (name, price, description) VALUES (?, ?, ?)", (name, price, desc))
        conn.commit()
        await message.answer(f"✅ Товар '{name}' добавлен!")
    except Exception as e:
        await message.answer(f"❌ Ошибка. Пиши так: /add Название | Цена | Описание")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
