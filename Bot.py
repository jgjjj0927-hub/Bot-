import asyncio, logging, os, shutil, sys, time
from datetime import datetime
from threading import Thread
from flask import Flask
import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "96266")
DB_PATH = os.getenv("DB_PATH", "shop.db")
BACKUP_DIR = "backups"
BACKUP_INTERVAL_MIN = 30
BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID", "0"))
START_TIME = time.time()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")
if ADMIN_ID == 0:
    logger.warning("ADMIN_ID = 0")
if BACKUP_CHANNEL_ID == 0:
    logger.warning("BACKUP_CHANNEL_ID не задан — авто-восстановление выключено")

os.makedirs(BACKUP_DIR, exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_flask():
    try:
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), use_reloader=False)
    except Exception as e:
        logger.error(f"Flask: {e}")

BUTTONS = {"🛒 Заказать","🔍 Поиск","🧺 Корзина","👤 Профиль","🆘 Поддержка",
"➕ Добавить товар","📦 Товары","🗑 Удалить товар","📋 Заказы","💬 Тикеты",
"📊 Состояние бота","💾 Бэкап","📢 Рассылка","🔙 Выйти"}
BUTTONS_LIST = list(BUTTONS)

def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🛒 Заказать"), KeyboardButton(text="🔍 Поиск")],
        [KeyboardButton(text="🧺 Корзина"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🆘 Поддержка")],
    ], resize_keyboard=True)

def admin_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Добавить товар")],
        [KeyboardButton(text="📦 Товары"), KeyboardButton(text="🗑 Удалить товар")],
        [KeyboardButton(text="📋 Заказы"), KeyboardButton(text="💬 Тикеты")],
        [KeyboardButton(text="📢 Рассылка"), KeyboardButton(text="📊 Состояние бота")],
        [KeyboardButton(text="💾 Бэкап"), KeyboardButton(text="🔙 Выйти")],
    ], resize_keyboard=True)

user_states = {}
admin_sessions = {}
temp_product = {}
temp_broadcast = {}

REQUIRED_TABLES = ["products", "orders", "cart", "tickets", "users"]

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,price INTEGER,description TEXT,category TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,product TEXT,total INTEGER,status TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS cart(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,product_id INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,message TEXT,answer TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,username TEXT,full_name TEXT,first_seen TEXT)")
        await db.commit()
        try:
            cur = await db.execute("SELECT COUNT(*) FROM users")
            cnt = (await cur.fetchone())[0]
            await cur.close()
            if cnt == 0:
                await db.execute("INSERT OR IGNORE INTO users(user_id, username, full_name, first_seen) SELECT DISTINCT user_id, '', '', '' FROM orders")
                await db.execute("INSERT OR IGNORE INTO users(user_id, username, full_name, first_seen) SELECT DISTINCT user_id, '', '', '' FROM tickets")
                await db.commit()
                logger.info("users заполнена из orders/tickets")
        except Exception as e:
            logger.error(f"init_db users migrate: {e}")

async def db_is_healthy():
    """Все ли нужные таблицы есть в базе."""
    if not os.path.exists(DB_PATH):
        return False
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in await cur.fetchall()}
            await cur.close()
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        if missing:
            logger.warning(f"В базе нет таблиц: {missing}")
            return False
        return True
    except Exception as e:
        logger.error(f"db_is_healthy: {e}")
        return False

async def db_has_data():
    """Есть ли в базе живые данные (товары/заказы/тикеты)."""
    if not os.path.exists(DB_PATH):
        return False
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            for t in ["orders", "products", "tickets"]:
                try:
                    cur = await db.execute(f"SELECT COUNT(*) FROM {t}")
                    cnt = (await cur.fetchone())[0]
                    await cur.close()
                    if cnt > 0:
                        return True
                except Exception:
                    pass
        return False
    except Exception:
        return False

async def restore_from_channel():
    """Скачивает закреплённый .db из канала и подменяет shop.db."""
    if BACKUP_CHANNEL_ID == 0:
        logger.warning("BACKUP_CHANNEL_ID не задан")
        return False
    try:
        chat = await bot.get_chat(BACKUP_CHANNEL_ID)
        pinned = chat.pinned_message
        if not pinned or not pinned.document:
            logger.warning("В канале нет закреплённого .db")
            return False

        doc = pinned.document
        if not doc.file_name.endswith(".db"):
            logger.warning(f"Закреплённый файл не .db: {doc.file_name}")
            return False

        file = await bot.get_file(doc.file_id)
        tmp = "restore_from_channel.db"
        await bot.download_file(file.file_path, tmp)

        async with aiosqlite.connect(tmp) as t:
            cur = await t.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = await cur.fetchall()
            await cur.close()
        if not tables:
            logger.warning("Скачанный файл пустой")
            os.remove(tmp)
            return False

        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, f"{DB_PATH}.before_auto_restore")
        shutil.move(tmp, DB_PATH)
        logger.info(f"✅ База восстановлена из канала ({len(tables)} таблиц)")
        return True
    except Exception as e:
        logger.error(f"restore_from_channel: {e}")
        return False

def safe_username(u): return f"@{u.username}" if u.username else f"id{u.id}"
def is_admin(uid): return ADMIN_ID != 0 and uid == ADMIN_ID
def is_admin_state(uid): return is_admin(uid) and admin_sessions.get(uid, False)

async def save_user(u):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?)",(u.id,u.username or "",u.full_name or "",datetime.now().isoformat()))
            await db.execute("UPDATE users SET username=?,full_name=? WHERE user_id=?",(u.username or "",u.full_name or "",u.id))
            await db.commit()
    except Exception as e: logger.error(f"save_user: {e}")

async def fetch_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users UNION SELECT user_id FROM orders UNION SELECT user_id FROM tickets")
        rows = await cur.fetchall(); await cur.close()
        return [r[0] for r in rows if r[0] != ADMIN_ID]

async def fetch_products():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,name,price,category FROM products")
        rows = await cur.fetchall(); await cur.close(); return rows

async def fetch_product(pid):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name,price,description FROM products WHERE id=?",(pid,))
        row = await cur.fetchone(); await cur.close(); return row

def catalog_kb(products):
    b = InlineKeyboardBuilder()
    for p in products:
        b.add(InlineKeyboardButton(text=f"{p[1]} — {p[2]} ₽", callback_data=f"view_{p[0]}"))
    b.adjust(1); return b.as_markup()

async def send_backup(reason="ручной"):
    if not os.path.exists(DB_PATH): return False
    try:
        name = f"{BACKUP_DIR}/shop_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.db"
        shutil.copy2(DB_PATH, name)
        size = round(os.path.getsize(name)/1024,1)
        caption = f"💾 <b>Бэкап</b>\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n📌 {reason}\n📦 {size} KB"

        # В ЛС админу
        if is_admin(ADMIN_ID):
            try:
                await bot.send_document(ADMIN_ID, FSInputFile(name), caption=caption)
            except Exception as e:
                logger.error(f"backup to admin: {e}")

        # В канал + пин
        if BACKUP_CHANNEL_ID != 0:
            try:
                msg = await bot.send_document(BACKUP_CHANNEL_ID, FSInputFile(name), caption=caption)
                try:
                    await bot.pin_chat_message(BACKUP_CHANNEL_ID, msg.message_id, disable_notification=True)
                except Exception as e:
                    logger.warning(f"pin failed: {e}. Дай боту право 'Закреплять сообщения' в канале")
            except Exception as e:
                logger.error(f"backup to channel: {e}")

        # Чистим локальные, оставляем 10
        files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("shop_")], reverse=True)
        for old in files[10:]:
            try: os.remove(os.path.join(BACKUP_DIR, old))
            except: pass
        return True
    except Exception as e:
        logger.error(f"backup: {e}"); return False

async def auto_backup_loop():
    await asyncio.sleep(60)
    while True:
        try: await send_backup(f"авто ({BACKUP_INTERVAL_MIN} мин)")
        except Exception as e: logger.error(f"auto_backup: {e}")
        await asyncio.sleep(BACKUP_INTERVAL_MIN * 60)

async def run_broadcast(admin_id, draft, users):
    sent=blocked=failed=0
    for uid in users:
        try:
            if draft["type"]=="text": await bot.send_message(uid, draft["text"])
            elif draft["type"]=="photo": await bot.send_photo(uid, draft["photo_id"], caption=draft.get("caption") or None)
            elif draft["type"]=="video": await bot.send_video(uid, draft["video_id"], caption=draft.get("caption") or None)
            sent+=1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after+1)
            try:
                if draft["type"]=="text": await bot.send_message(uid, draft["text"])
                elif draft["type"]=="photo": await bot.send_photo(uid, draft["photo_id"], caption=draft.get("caption") or None)
                elif draft["type"]=="video": await bot.send_video(uid, draft["video_id"], caption=draft.get("caption") or None)
                sent+=1
            except: failed+=1
        except TelegramForbiddenError: blocked+=1
        except Exception as e:
            err=str(e).lower()
            if "blocked" in err or "chat not found" in err or "deactivated" in err: blocked+=1
            else: failed+=1; logger.warning(f"bc {uid}: {e}")
        await asyncio.sleep(0.05)
    temp_broadcast.pop(admin_id, None)
    try:
        await bot.send_message(admin_id, f"📢 <b>Рассылка готова</b>\n✅ {sent}\n🚫 {blocked}\n❌ {failed}\n👥 {len(users)}")
    except: pass

@dp.message(CommandStart())
async def start_cmd(m):
    user_states.pop(m.from_user.id, None)
    await save_user(m.from_user)
    await m.answer(f"👋 Привет, {m.from_user.first_name}!\nЭто S Mod Shop.\nВыбери действие:", reply_markup=main_menu())

@dp.message(F.text == "🛒 Заказать")
async def catalog(m):
    await save_user(m.from_user)
    products = await fetch_products()
    if not products: await m.answer("Товаров нет."); return
    await m.answer("📦 Выбери товар:", reply_markup=catalog_kb(products))

@dp.callback_query(F.data == "back_catalog")
async def back_catalog(c):
    products = await fetch_products()
    if not products: await c.message.edit_text("Товаров нет."); await c.answer(); return
    await c.message.edit_text("📦 Выбери товар:", reply_markup=catalog_kb(products)); await c.answer()

@dp.callback_query(F.data.startswith("view_"))
async def view_product(c):
    try: pid = int(c.data.split("_")[1])
    except: await c.answer("Ошибка", show_alert=True); return
    p = await fetch_product(pid)
    if not p: await c.answer("Нет", show_alert=True); return
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="🛒 Купить", callback_data=f"buy_{pid}"))
    b.add(InlineKeyboardButton(text="🧺 В корзину", callback_data=f"cart_{pid}"))
    b.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_catalog"))
    await c.message.edit_text(f"📦 {p[0]}\n💰 {p[1]} ₽\n📝 {p[2]}", reply_markup=b.as_markup())
    await c.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(c):
    try: pid = int(c.data.split("_")[1])
    except: await c.answer("Ошибка", show_alert=True); return
    p = await fetch_product(pid)
    if not p: await c.answer("Нет", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO orders(user_id,product,total,status) VALUES(?,?,?,?)",(c.from_user.id,p[0],p[1],"новый"))
        await db.commit()
    await c.message.answer(f"✅ Заказ: {p[0]} за {p[1]} ₽.")
    if is_admin(ADMIN_ID):
        try: await bot.send_message(ADMIN_ID, f"🛒 Заказ!\n{c.from_user.full_name} ({safe_username(c.from_user)})\n🆔 <code>{c.from_user.id}</code>\n{p[0]} — {p[1]} ₽\n<a href='tg://user?id={c.from_user.id}'>💬 Написать</a>")
        except: pass
    await c.answer()

@dp.callback_query(F.data.startswith("cart_"))
async def add_cart(c):
    try: pid = int(c.data.split("_")[1])
    except: await c.answer("Ошибка", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM cart WHERE user_id=? AND product_id=?",(c.from_user.id,pid))
        if await cur.fetchone(): await cur.close(); await c.answer("Уже в корзине", show_alert=True); return
        await cur.close()
        await db.execute("INSERT INTO cart(user_id,product_id) VALUES(?,?)",(c.from_user.id,pid))
        await db.commit()
    await c.answer("✅ В корзине", show_alert=True)

@dp.message(F.text == "🧺 Корзина")
async def show_cart(m):
    await save_user(m.from_user)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT c.id,p.name,p.price FROM cart c JOIN products p ON c.product_id=p.id WHERE c.user_id=?",(m.from_user.id,))
        items = await cur.fetchall(); await cur.close()
    if not items: await m.answer("🧺 Пусто."); return
    text = "🧺 Корзина:\n"; total = 0
    for i in items: text += f"#{i[0]} {i[1]} — {i[2]} ₽\n"; total += i[2]
    text += f"\n💰 {total} ₽"
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="✅ Оформить", callback_data="checkout"))
    b.add(InlineKeyboardButton(text="🗑 Очистить", callback_data="clear_cart"))
    b.adjust(1)
    await m.answer(text, reply_markup=b.as_markup())

@dp.callback_query(F.data == "clear_cart")
async def clear_cart(c):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM cart WHERE user_id=?",(c.from_user.id,)); await db.commit()
    await c.message.edit_text("🧺 Очищено."); await c.answer()

@dp.callback_query(F.data == "checkout")
async def checkout(c):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT p.name,p.price FROM cart c JOIN products p ON c.product_id=p.id WHERE c.user_id=?",(c.from_user.id,))
        items = await cur.fetchall(); await cur.close()
        if not items: await c.answer("Пусто", show_alert=True); return
        txt = ", ".join(f"{i[0]} ({i[1]} ₽)" for i in items); total = sum(i[1] for i in items)
        await db.execute("INSERT INTO orders(user_id,product,total,status) VALUES(?,?,?,?)",(c.from_user.id,txt,total,"новый"))
        await db.execute("DELETE FROM cart WHERE user_id=?",(c.from_user.id,)); await db.commit()
    await c.message.answer(f"✅ Заказ на {total} ₽:\n{txt}")
    if is_admin(ADMIN_ID):
        try: await bot.send_message(ADMIN_ID, f"🛒 Заказ из корзины!\n{c.from_user.full_name} ({safe_username(c.from_user)})\n🆔 <code>{c.from_user.id}</code>\n{txt}\n{total} ₽\n<a href='tg://user?id={c.from_user.id}'>💬 Написать</a>")
        except: pass
    await c.answer()

@dp.message(F.text == "🔍 Поиск")
async def search(m):
    await save_user(m.from_user); user_states[m.from_user.id] = "awaiting_search"
    await m.answer("🔍 Введи название:")

@dp.message(F.text == "👤 Профиль")
async def profile(m):
    await save_user(m.from_user)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE user_id=?",(m.from_user.id,))
        cnt = (await cur.fetchone())[0]; await cur.close()
    await m.answer(f"👤 ID: {m.from_user.id}\nИмя: {m.from_user.full_name}\nЗаказов: {cnt}")

@dp.message(F.text == "🆘 Поддержка")
async def support(m):
    await save_user(m.from_user); user_states[m.from_user.id] = "awaiting_support"
    await m.answer("🆘 Напиши вопрос:")

@dp.message(Command("backup"))
async def backup_cmd(m):
    if not is_admin(m.from_user.id): return
    await m.answer("💾 Делаю...")
    if not await send_backup("ручной"): await m.answer("❌ Не вышло")

@dp.message(F.text == "💾 Бэкап")
async def backup_btn(m):
    if not is_admin_state(m.from_user.id): await m.answer("⛔ /admin"); return
    await m.answer("💾 Делаю...")
    if not await send_backup("кнопка"): await m.answer("❌ Не вышло")

@dp.message(Command("restore"))
async def restore_cmd(m):
    if not is_admin(m.from_user.id): return
    if not m.reply_to_message or not m.reply_to_message.document:
        await m.answer("Ответь на .db файл командой /restore"); return
    doc = m.reply_to_message.document
    if not doc.file_name.endswith(".db"): await m.answer("Нужен .db"); return
    try:
        f = await bot.get_file(doc.file_id); tmp = "restore_tmp.db"
        await bot.download_file(f.file_path, tmp)
        async with aiosqlite.connect(tmp) as t:
            cur = await t.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = await cur.fetchall(); await cur.close()
        if not tables: await m.answer("Пустой"); os.remove(tmp); return
        if os.path.exists(DB_PATH): shutil.copy2(DB_PATH, f"{DB_PATH}.old")
        shutil.move(tmp, DB_PATH)
        await init_db()
        await m.answer(f"✅ Восстановлено. Таблиц: {len(tables)}")
        # Пиним свежий бэкап в канал, чтобы авто-восстановление взяло именно его
        await send_backup("после /restore")
    except Exception as e: await m.answer(f"❌ {e}")

@dp.message(Command("restore_channel"))
async def restore_channel_cmd(m):
    if not is_admin(m.from_user.id): return
    await m.answer("💾 Восстанавливаю из канала...")
    ok = await restore_from_channel()
    if ok:
        await init_db()
        await m.answer("✅ База восстановлена из канала!")
    else:
        await m.answer("❌ Не удалось (нет закреплённого .db в канале?)")

@dp.message(F.text == "📢 Рассылка")
async def bc_start(m):
    if not is_admin_state(m.from_user.id): await m.answer("⛔ /admin"); return
    user_states[m.from_user.id] = "broadcast_content"
    await m.answer("📢 Отправь текст/фото/видео.\n/cancel — отмена")

@dp.message(Command("cancel"))
async def cancel(m):
    if not is_admin(m.from_user.id): return
    user_states.pop(m.from_user.id, None); temp_broadcast.pop(m.from_user.id, None)
    await m.answer("Отменено.", reply_markup=admin_menu())

@dp.callback_query(F.data == "broadcast_send")
async def bc_send(c):
    if not is_admin_state(c.from_user.id): await c.answer("Нет", show_alert=True); return
    draft = temp_broadcast.get(c.from_user.id)
    if not draft: await c.answer("Черновик потерян", show_alert=True); return
    users = await fetch_all_users()
    if not users: await c.message.edit_text("Нет юзеров"); await c.answer(); return
    await c.message.edit_text(f"📢 Рассылка на {len(users)}...")
    await c.answer()
    asyncio.create_task(run_broadcast(c.from_user.id, draft, users))

@dp.callback_query(F.data == "broadcast_cancel")
async def bc_cancel(c):
    temp_broadcast.pop(c.from_user.id, None); user_states.pop(c.from_user.id, None)
    await c.message.edit_text("❌ Отменено."); await c.answer()

@dp.message(Command("admin"))
async def admin_cmd(m):
    if not is_admin(m.from_user.id): await m.answer("⛔ Нет доступа"); return
    user_states[m.from_user.id] = "awaiting_password"; await m.answer("🔐 Пароль:")

@dp.message(F.text == "🔙 Выйти")
async def exit_admin(m):
    if not is_admin(m.from_user.id): return
    admin_sessions.pop(m.from_user.id, None); user_states.pop(m.from_user.id, None)
    await m.answer("Вышел.", reply_markup=main_menu())

@dp.message(F.text == "➕ Добавить товар")
async def add_prod(m):
    if not is_admin_state(m.from_user.id): await m.answer("⛔ /admin"); return
    user_states[m.from_user.id] = "product_name"; await m.answer("📝 Название:")

@dp.message(F.text == "🗑 Удалить товар")
async def del_prod(m):
    if not is_admin_state(m.from_user.id): await m.answer("⛔ /admin"); return
    ps = await fetch_products()
    if not ps: await m.answer("Товаров нет."); return
    txt = "🗑 ID для удаления:\n" + "\n".join(f"#{p[0]} {p[1]}" for p in ps)
    user_states[m.from_user.id] = "delete_product"
    await m.answer(txt + "\n\nВведи ID:")

@dp.message(F.text == "📦 Товары")
async def list_prods(m):
    if not is_admin_state(m.from_user.id): await m.answer("⛔ /admin"); return
    ps = await fetch_products()
    if not ps: await m.answer("Пусто."); return
    await m.answer("📦 Товары:\n" + "\n".join(f"#{p[0]} {p[1]} — {p[2]} ₽ ({p[3]})" for p in ps))

@dp.message(F.text == "📋 Заказы")
async def list_orders(m):
    if not is_admin_state(m.from_user.id): await m.answer("⛔ /admin"); return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,user_id,product,total,status FROM orders ORDER BY id DESC LIMIT 20")
        orders = await cur.fetchall(); await cur.close()
    if not orders: await m.answer("Нет заказов."); return
    await m.answer("📋 Заказы:\n" + "\n".join(f"#{o[0]} | {o[1]} | {o[2]} | {o[3]} ₽ | {o[4]}" for o in orders))

@dp.message(F.text == "💬 Тикеты")
async def list_tickets(m):
    if not is_admin_state(m.from_user.id): await m.answer("⛔ /admin"); return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,user_id,message,answer FROM tickets ORDER BY id DESC LIMIT 20")
        ts = await cur.fetchall(); await cur.close()
    if not ts: await m.answer("Пусто."); return
    txt = "💬 Тикеты:\n"
    for t in ts: txt += f"#{t[0]} | {t[1]}\n❓ {t[2]}\n💬 {t[3] or '—'}\n\n"
    await m.answer(txt)

@dp.message(F.text == "📊 Состояние бота")
async def status(m):
    if not is_admin_state(m.from_user.id): await m.answer("⛔ /admin"); return
    try:
        ram = "н/д"
        if sys.platform != "win32":
            import resource
            ram = f"{round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)} MB"
        up = int(time.time()-START_TIME); h = up//3600; mn = (up%3600)//60
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT COUNT(*) FROM products"); prods = (await cur.fetchone())[0]; await cur.close()
            cur = await db.execute("SELECT COUNT(*) FROM orders"); ords = (await cur.fetchone())[0]; await cur.close()
            cur = await db.execute("SELECT COUNT(*) FROM tickets"); tks = (await cur.fetchone())[0]; await cur.close()
            cur = await db.execute("SELECT COUNT(*) FROM users"); us = (await cur.fetchone())[0]; await cur.close()
        sz = round(os.path.getsize(DB_PATH)/1024,1) if os.path.exists(DB_PATH) else 0
        chan = "✅" if BACKUP_CHANNEL_ID != 0 else "❌"
        await m.answer(f"📊 Состояние\n━━━━━━━━━━━━\n🧠 RAM: {ram}\n⏱ {h}ч {mn}м\n💾 БД: {sz} KB\n📡 Канал: {chan}\n━━━━━━━━━━━━\n👥 {us}\n📦 {prods}\n📋 {ords}\n💬 {tks}\n━━━━━━━━━━━━\n🤖 OK")
    except Exception as e: await m.answer(f"❌ {e}")

@dp.message(Command("answer"))
async def ans_ticket(m):
    if not is_admin(m.from_user.id): return
    try:
        parts = m.text.split(" ",2); tid = int(parts[1]); txt = parts[2]
    except: await m.answer("Формат: /answer ID текст"); return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM tickets WHERE id=?",(tid,))
        row = await cur.fetchone(); await cur.close()
        if not row: await m.answer("Не найден"); return
        await db.execute("UPDATE tickets SET answer=? WHERE id=?",(txt,tid)); await db.commit()
    try:
        await bot.send_message(row[0], f"📩 Ответ:\n{txt}"); await m.answer("✅ Отправлено")
    except Exception as e: await m.answer(f"❌ {e}")

@dp.message(F.photo | F.video)
async def handle_media(m):
    uid = m.from_user.id
    if user_states.get(uid) != "broadcast_content" or not is_admin_state(uid): return
    if m.photo: draft = {"type":"photo","photo_id":m.photo[-1].file_id,"caption":m.caption or ""}
    else: draft = {"type":"video","video_id":m.video.file_id,"caption":m.caption or ""}
    temp_broadcast[uid] = draft; user_states.pop(uid, None)
    users = await fetch_all_users()
    prev = f"[{'Фото' if draft['type']=='photo' else 'Видео'}] {(draft.get('caption') or '')[:100]}"
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text=f"✅ Отправить {len(users)}", callback_data="broadcast_send"))
    b.add(InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")); b.adjust(1)
    await m.answer(f"📢 <b>Проверь</b>\n👥 {len(users)}\n📄 {prev}", reply_markup=b.as_markup())

@dp.message(F.text, ~F.text.startswith("/"), ~F.text.in_(BUTTONS_LIST))
async def handle_input(m):
    uid = m.from_user.id; state = user_states.get(uid)

    if state == "broadcast_content":
        if not is_admin_state(uid): return
        temp_broadcast[uid] = {"type":"text","text":m.text}; user_states.pop(uid, None)
        users = await fetch_all_users()
        b = InlineKeyboardBuilder()
        b.add(InlineKeyboardButton(text=f"✅ Отправить {len(users)}", callback_data="broadcast_send"))
        b.add(InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")); b.adjust(1)
        prev = m.text[:120] + ("..." if len(m.text)>120 else "")
        await m.answer(f"📢 <b>Проверь</b>\n👥 {len(users)}\n📄 {prev}", reply_markup=b.as_markup()); return

    if state == "awaiting_password":
        if not is_admin(uid): user_states.pop(uid,None); return
        if m.text.strip() == ADMIN_PASSWORD:
            admin_sessions[uid] = True; user_states.pop(uid, None)
            await m.answer("🔧 Админ-панель:", reply_markup=admin_menu())
        else:
            user_states.pop(uid, None); await m.answer("❌ Неверно")
        return

    if state == "product_name":
        if not is_admin_state(uid): return
        temp_product[uid] = {"name": m.text}; user_states[uid] = "product_price"
        await m.answer("💰 Цена:"); return

    if state == "product_price":
        if not is_admin_state(uid): return
        if not m.text.strip().isdigit(): await m.answer("Число!"); return
        temp_product[uid]["price"] = int(m.text.strip()); user_states[uid] = "product_desc"
        await m.answer("📝 Описание:"); return
