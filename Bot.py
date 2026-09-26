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

T=os.getenv("BOT_TOKEN"); AID=int(os.getenv("ADMIN_ID","0")); PWD=os.getenv("ADMIN_PASSWORD","96266")
DB=os.getenv("DB_PATH","shop.db"); BD="backups"; BIM=30
CH=int(os.getenv("BACKUP_CHANNEL_ID","0")); ST=time.time()
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s"); log=logging.getLogger("bot")
if not T: raise RuntimeError("BOT_TOKEN не задан")
os.makedirs(BD,exist_ok=True)
bot=Bot(token=T,default=DefaultBotProperties(parse_mode="HTML")); dp=Dispatcher(); app=Flask(__name__)

@app.route("/")
def idx(): return "OK"

def run_flask():
    try: app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)),use_reloader=False)
    except Exception as e: log.error(f"flask:{e}")

BTNS={"🛒 Заказать","🔍 Поиск","🧺 Корзина","👤 Профиль","🆘 Поддержка","➕ Добавить товар",
"📦 Товары","🗑 Удалить товар","📋 Заказы","💬 Тикеты","📊 Состояние бота","💾 Бэкап","📢 Рассылка","🔙 Выйти"}
BTNS_L=list(BTNS)

def mmenu(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🛒 Заказать"),KeyboardButton(text="🔍 Поиск")],[KeyboardButton(text="🧺 Корзина"),KeyboardButton(text="👤 Профиль")],[KeyboardButton(text="🆘 Поддержка")]],resize_keyboard=True)
def amenu(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="➕ Добавить товар")],[KeyboardButton(text="📦 Товары"),KeyboardButton(text="🗑 Удалить товар")],[KeyboardButton(text="📋 Заказы"),KeyboardButton(text="💬 Тикеты")],[KeyboardButton(text="📢 Рассылка"),KeyboardButton(text="📊 Состояние бота")],[KeyboardButton(text="💾 Бэкап"),KeyboardButton(text="🔙 Выйти")]],resize_keyboard=True)

US={}; AS={}; TP={}; TB={}
REQ=["products","orders","cart","tickets","users"]

async def idb():
    async with aiosqlite.connect(DB) as d:
        await d.execute("CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,price INTEGER,description TEXT,category TEXT)")
        await d.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,product TEXT,total INTEGER,status TEXT)")
        await d.execute("CREATE TABLE IF NOT EXISTS cart(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,product_id INTEGER)")
        await d.execute("CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,message TEXT,answer TEXT)")
        await d.execute("CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,username TEXT,full_name TEXT,first_seen TEXT)")
        await d.commit()
        c=await d.execute("SELECT COUNT(*) FROM users"); n=(await c.fetchone())[0]; await c.close()
        if n==0:
            await d.execute("INSERT OR IGNORE INTO users SELECT DISTINCT user_id,'','','' FROM orders")
            await d.execute("INSERT OR IGNORE INTO users SELECT DISTINCT user_id,'','','' FROM tickets")
            await d.commit()

async def healthy():
    if not os.path.exists(DB): return False
    try:
        async with aiosqlite.connect(DB) as d:
            c=await d.execute("SELECT name FROM sqlite_master WHERE type='table'"); ts={r[0] for r in await c.fetchall()}; await c.close()
        return all(t in ts for t in REQ)
    except: return False

async def has_data():
    if not os.path.exists(DB): return False
    try:
        async with aiosqlite.connect(DB) as d:
            for t in ["orders","products","tickets"]:
                try:
                    c=await d.execute(f"SELECT COUNT(*) FROM {t}"); n=(await c.fetchone())[0]; await c.close()
                    if n>0: return True
                except: pass
        return False
    except: return False

async def restore_ch():
    if CH==0: return False
    try:
        ch=await bot.get_chat(CH); pm=ch.pinned_message
        if not pm or not pm.document or not pm.document.file_name.endswith(".db"): return False
        f=await bot.get_file(pm.document.file_id); tmp="rc.db"; await bot.download_file(f.file_path,tmp)
        if os.path.exists(DB): shutil.copy2(DB,DB+".before")
        shutil.move(tmp,DB); log.info("restored from channel"); return True
    except Exception as e: log.error(f"restore_ch:{e}"); return False

def su(u): return f"@{u.username}" if u.username else f"id{u.id}"
def adm(u): return AID!=0 and u==AID
def ast(u): return adm(u) and AS.get(u,False)

async def save_u(u):
    try:
        async with aiosqlite.connect(DB) as d:
            await d.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?)",(u.id,u.username or "",u.full_name or "",datetime.now().isoformat()))
            await d.execute("UPDATE users SET username=?,full_name=? WHERE user_id=?",(u.username or "",u.full_name or "",u.id)); await d.commit()
    except Exception as e: log.error(f"save_u:{e}")

async def all_u():
    async with aiosqlite.connect(DB) as d:
        c=await d.execute("SELECT user_id FROM users UNION SELECT user_id FROM orders UNION SELECT user_id FROM tickets")
        r=await c.fetchall(); await c.close(); return [x[0] for x in r if x[0]!=AID]

async def prods():
    async with aiosqlite.connect(DB) as d:
        c=await d.execute("SELECT id,name,price,category FROM products"); r=await c.fetchall(); await c.close(); return r

async def prod(pid):
    async with aiosqlite.connect(DB) as d:
        c=await d.execute("SELECT name,price,description FROM products WHERE id=?",(pid,)); r=await c.fetchone(); await c.close(); return r

def cat_kb(ps):
    b=InlineKeyboardBuilder()
    for p in ps: b.add(InlineKeyboardButton(text=f"{p[1]} — {p[2]} ₽",callback_data=f"v_{p[0]}"))
    b.adjust(1); return b.as_markup()

async def backup(rs="ручной"):
    if not os.path.exists(DB): return False
    try:
        n=f"{BD}/shop_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"; shutil.copy2(DB,n)
        cap=f"💾 {datetime.now().strftime('%d.%m %H:%M')} | {rs} | {round(os.path.getsize(n)/1024,1)} KB"
        if adm(AID):
            try: await bot.send_document(AID,FSInputFile(n),caption=cap)
            except: pass
        if CH!=0:
            try:
                m=await bot.send_document(CH,FSInputFile(n),caption=cap)
                try: await bot.pin_chat_message(CH,m.message_id,disable_notification=True)
                except: pass
            except Exception as e: log.error(f"ch backup:{e}")
        fs=sorted([f for f in os.listdir(BD) if f.startswith("shop_")],reverse=True)
        for o in fs[10:]:
            try: os.remove(os.path.join(BD,o))
            except: pass
        return True
    except Exception as e: log.error(f"backup:{e}"); return False

async def ab_loop():
    await asyncio.sleep(60)
    while True:
        try: await backup(f"авто {BIM}м")
        except Exception as e: log.error(f"ab:{e}")
        await asyncio.sleep(BIM*60)

async def bcast(aid,df,us):
    s=b=f=0
    for u in us:
        try:
            if df["type"]=="text": await bot.send_message(u,df["text"])
            elif df["type"]=="photo": await bot.send_photo(u,df["photo_id"],caption=df.get("caption") or None)
            else: await bot.send_video(u,df["video_id"],caption=df.get("caption") or None)
            s+=1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after+1)
            try:
                if df["type"]=="text": await bot.send_message(u,df["text"])
                elif df["type"]=="photo": await bot.send_photo(u,df["photo_id"],caption=df.get("caption") or None)
                else: await bot.send_video(u,df["video_id"],caption=df.get("caption") or None)
                s+=1
            except: f+=1
        except TelegramForbiddenError: b+=1
        except: f+=1
        await asyncio.sleep(0.05)
    TB.pop(aid,None)
    try: await bot.send_message(aid,f"📢 Готово\n✅ {s}\n🚫 {b}\n❌ {f}")
    except: pass

@dp.message(CommandStart())
async def cmd_start(m):
    US.pop(m.from_user.id,None); await save_u(m.from_user)
    await m.answer(f"👋 Привет, {m.from_user.first_name}!\nS Mod Shop — выбери действие:",reply_markup=mmenu())

@dp.message(F.text=="🛒 Заказать")
async def cmd_cat(m):
    await save_u(m.from_user); ps=await prods()
    if not ps: await m.answer("Товаров нет."); return
    await m.answer("📦 Выбери:",reply_markup=cat_kb(ps))

@dp.callback_query(F.data=="back_cat")
async def cb_back(c):
    ps=await prods()
    if not ps: await c.message.edit_text("Пусто."); await c.answer(); return
    await c.message.edit_text("📦 Выбери:",reply_markup=cat_kb(ps)); await c.answer()

@dp.callback_query(F.data.startswith("v_"))
async def cb_view(c):
    try: pid=int(c.data.split("_")[1])
    except: await c.answer("err",show_alert=True); return
    p=await prod(pid)
    if not p: await c.answer("нет",show_alert=True); return
    b=InlineKeyboardBuilder(); b.add(InlineKeyboardButton(text="🛒 Купить",callback_data=f"b_{pid}"),InlineKeyboardButton(text="🧺 В корзину",callback_data=f"c_{pid}"),InlineKeyboardButton(text="🔙 Назад",callback_data="back_cat")); b.adjust(1)
    await c.message.edit_text(f"📦 {p[0]}\n💰 {p[1]} ₽\n📝 {p[2]}",reply_markup=b.as_markup()); await c.answer()

@dp.callback_query(F.data.startswith("b_"))
async def cb_buy(c):
    try: pid=int(c.data.split("_")[1])
    except: await c.answer("err",show_alert=True); return
    p=await prod(pid)
    if not p: await c.answer("нет",show_alert=True); return
    async with aiosqlite.connect(DB) as d:
        await d.execute("INSERT INTO orders(user_id,product,total,status) VALUES(?,?,?,?)",(c.from_user.id,p[0],p[1],"новый")); await d.commit()
    await c.message.answer(f"✅ Заказ: {p[0]} — {p[1]} ₽")
    if adm(AID):
        try: await bot.send_message(AID,f"🛒 Заказ\n{c.from_user.full_name} ({su(c.from_user)})\n🆔 <code>{c.from_user.id}</code>\n{p[0]} — {p[1]} ₽\n<a href='tg://user?id={c.from_user.id}'>💬 Написать</a>")
        except: pass
    await c.answer()

@dp.callback_query(F.data.startswith("c_"))
async def cb_cart(c):
    try: pid=int(c.data.split("_")[1])
    except: await c.answer("err",show_alert=True); return
    async with aiosqlite.connect(DB) as d:
        cur=await d.execute("SELECT id FROM cart WHERE user_id=? AND product_id=?",(c.from_user.id,pid))
        if await cur.fetchone(): await cur.close(); await c.answer("Уже в корзине",show_alert=True); return
        await cur.close(); await d.execute("INSERT INTO cart(user_id,product_id) VALUES(?,?)",(c.from_user.id,pid)); await d.commit()
    await c.answer("✅ В корзине",show_alert=True)

@dp.message(F.text=="🧺 Корзина")
async def cmd_cart(m):
    await save_u(m.from_user)
    async with aiosqlite.connect(DB) as d:
        c=await d.execute("SELECT c.id,p.name,p.price FROM cart c JOIN products p ON c.product_id=p.id WHERE c.user_id=?",(m.from_user.id,)); it=await c.fetchall(); await c.close()
    if not it: await m.answer("🧺 Пусто."); return
    t="🧺 Корзина:\n"; tot=0
    for i in it: t+=f"#{i[0]} {i[1]} — {i[2]} ₽\n"; tot+=i[2]
    t+=f"\n💰 {tot} ₽"
    b=InlineKeyboardBuilder(); b.add(InlineKeyboardButton(text="✅ Оформить",callback_data="chk"),InlineKeyboardButton(text="🗑 Очистить",callback_data="clr")); b.adjust(1)
    await m.answer(t,reply_markup=b.as_markup())

@dp.callback_query(F.data=="clr")
async def cb_clr(c):
    async with aiosqlite.connect(DB) as d: await d.execute("DELETE FROM cart WHERE user_id=?",(c.from_user.id,)); await d.commit()
    await c.message.edit_text("🧺 Очищено."); await c.answer()

@dp.callback_query(F.data=="chk")
async def cb_chk(c):
    async with aiosqlite.connect(DB) as d:
        cur=await d.execute("SELECT p.name,p.price FROM cart c JOIN products p ON c.product_id=p.id WHERE c.user_id=?",(c.from_user.id,)); it=await cur.fetchall(); await cur.close()
        if not it: await c.answer("Пусто",show_alert=True); return
        tx=", ".join(f"{i[0]} ({i[1]} ₽)" for i in it); tot=sum(i[1] for i in it)
        await d.execute("INSERT INTO orders(user_id,product,total,status) VALUES(?,?,?,?)",(c.from_user.id,tx,tot,"новый"))
        await d.execute("DELETE FROM cart WHERE user_id=?",(c.from_user.id,)); await d.commit()
    await c.message.answer(f"✅ Заказ на {tot} ₽:\n{tx}")
    if adm(AID):
        try: await bot.send_message(AID,f"🛒 Заказ (корзина)\n{c.from_user.full_name} ({su(c.from_user)})\n🆔 <code>{c.from_user.id}</code>\n{tx}\n{tot} ₽\n<a href='tg://user?id={c.from_user.id}'>💬 Написать</a>")
        except: pass
    await c.answer()

@dp.message(F.text=="🔍 Поиск")
async def cmd_search(m):
    await save_u(m.from_user); US[m.from_user.id]="srch"; await m.answer("🔍 Название:")

@dp.message(F.text=="👤 Профиль")
async def cmd_prof(m):
    await save_u(m.from_user)
    async with aiosqlite.connect(DB) as d:
        c=await d.execute("SELECT COUNT(*) FROM orders WHERE user_id=?",(m.from_user.id,)); n=(await c.fetchone())[0]; await c.close()
    await m.answer(f"👤 ID: {m.from_user.id}\nИмя: {m.from_user.full_name}\nЗаказов: {n}")

@dp.message(F.text=="🆘 Поддержка")
async def cmd_sup(m):
    await save_u(m.from_user); US[m.from_user.id]="sup"; await m.answer("🆘 Напиши вопрос:")

@dp.message(Command("backup"))
async def cmd_bk(m):
    if not adm(m.from_user.id): return
    await m.answer("💾..."); await backup("ручной")

@dp.message(F.text=="💾 Бэкап")
async def btn_bk(m):
    if not ast(m.from_user.id): await m.answer("⛔ /admin"); return
    await m.answer("💾..."); await backup("кнопка")

@dp.message(Command("restore"))
async def cmd_rs(m):
    if not adm(m.from_user.id): return
    if not m.reply_to_message or not m.reply_to_message.document: await m.answer("Ответь на .db /restore"); return
    d=m.reply_to_message.document
    if not d.file_name.endswith(".db"): await m.answer("Нужен .db"); return
    try:
        f=await bot.get_file(d.file_id); tmp="rt.db"; await bot.download_file(f.file_path,tmp)
        if os.path.exists(DB): shutil.copy2(DB,DB+".old")
        shutil.move(tmp,DB); await idb(); await m.answer("✅ Восстановлено"); await backup("после restore")
    except Exception as e: await m.answer(f"❌ {e}")

@dp.message(Command("restore_channel"))
async def cmd_rsc(m):
    if not adm(m.from_user.id): return
    await m.answer("💾...")
    if await restore_ch(): await idb(); await m.answer("✅ Восстановлено из канала")
    else: await m.answer("❌ Нет закреплённого .db")

@dp.message(F.text=="📢 Рассылка")
async def cmd_bc(m):
    if not ast(m.from_user.id): await m.answer("⛔ /admin"); return
    US[m.from_user.id]="bc"; await m.answer("📢 Текст/фото/видео. /cancel — отмена")

@dp.message(Command("cancel"))
async def cmd_cn(m):
    if not adm(m.from_user.id): return
    US.pop(m.from_user.id,None); TB.pop(m.from_user Exception.id,None); await m.answer("Отменено.",reply_markup=amenu())

@dp.callback_query(F.data=="bc_go")
async def cb_bcgo(c):
    if not ast(c.from_user.id): await c.answer("нет",show_alert=True); return
    df=TB.get(c.from_user.id)
    if not df: await c.answer("Черновик потерян",show_alert=True); return
    us=await all_u()
    if not us: await c.message.edit_text("Нет юзеров"); await c.answer(); return
    await c.message.edit_text(f"📢 Рассылка на {len(us)}..."); await c.answer()
    asyncio.create_task(bcast(c.from_user.id,df,us))

@dp.callback_query(F.data=="bc_no")
async def cb_bcno(c):
    TB.pop(c.from_user.id,None); US.pop(c.from_user.id,None); await c.message.edit_text("❌ Отменено."); await c.answer()

@dp.message(Command("admin"))
async def cmd_admin(m):
    if not adm(m.from_user.id): await m.answer("⛔ Нет доступа"); return
    US[m.from_user.id]="pwd"; await m.answer("🔐 Пароль:")

@dp.message(F.text=="🔙 Выйти")
async def cmd_exit(m):
    if not adm(m.from_user.id): return
    AS.pop(m.from_user.id,None); US.pop(m.from_user.id,None); await m.answer("Вышел.",reply_markup=mmenu())

@dp.message(F.text=="➕ Добавить товар")
async def cmd_add(m):
    if not ast(m.from_user.id): await m.answer("⛔ /admin"); return
    US[m.from_user.id]="pn"; await m.answer("📝 Название:")

@dp.message(F.text=="🗑 Удалить товар")
async def cmd_del(m):
    if not ast(m.from_user.id): await m.answer("⛔ /admin"); return
    ps=await prods()
    if not ps: await m.answer("Нет товаров"); return
    US[m.from_user.id]="dp"; as await m e.answer("🗑 ID:\n":+"\n".join(f"#{ awaitp[0]} {p[1] m}" for p in ps))

@dp.message(F.text=="📦 Товары")
async def cmd_lp(m):
    if not ast(m.from_user.id): await m.answer("⛔ /admin"); return
    ps=await prods()
    if not ps: await m.answer("Пусто."); return
    await m.answer("📦\n"+"\n".join(f"#{p[0]} {p[1]} — {p[2]} ₽ ({p[3]})" for p in ps))

@dp.message(F.text=="📋 Заказы")
async def cmd_lo(m):
    if not ast(m.from_user.id): await m.answer("⛔ /admin"); return
    async with aiosqlite.connect(DB) as d:
        c=await d.execute("SELECT id,user_id,product,total,status FROM orders ORDER BY id DESC LIMIT 20"); o=await c.fetchall(); await c.close()
    if not o: await m.answer("Нет заказов"); return
    await m.answer("📋\n"+"\n".join(f"#{x[0]} | {x[1]} | {x[2]} | {x[3]} ₽ | {x[4]}" for x in o))

@dp.message(F.text=="💬 Тикеты")
async def cmd_lt(m):
    if not ast(m.from_user.id): await m.answer("⛔ /admin"); return
    async with aiosqlite.connect(DB) as d:
        c=await d.execute("SELECT id,user_id,message,answer FROM tickets ORDER BY id DESC LIMIT 20"); ts=await c.fetchall(); await c.close()
    if not ts: await m.answer("Пусто."); return
    t="💬\n"
    for x in ts: t+=f"#{x[0]} | {x[1]}\n❓ {x[2]}\n💬 {x[3] or '—'}\n\n"
    await m.answer(t)

@dp.message(F.text=="📊 Состояние бота")
async def cmd_st(m):
    if not ast(m.from_user.id): await m.answer("⛔ /admin"); return
    try:
        ram="н/д"
        if sys.platform!="win32":
            import resource
            ram=f"{round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)} MB"
        up=int(time.time()-ST); h=up//3600; mn=(up%3600)//60
        async with aiosqlite.connect(DB) as d:
            c=await d.execute("SELECT COUNT(*) FROM products"); p=(await c.fetchone())[0]; await c.close()
            c=await d.execute("SELECT COUNT(*) FROM orders"); o=(await c.fetchone())[0]; await c.close()
            c=await d.execute("SELECT COUNT(*) FROM tickets"); tk=(await c.fetchone())[0]; await c.close()
            c=await d.execute("SELECT COUNT(*) FROM users"); u=(await c.fetchone())[0]; await c.close()
        sz=round(os.path.getsize(DB)/1024,1) if os.path.exists(DB) else 0
        ch="✅" if CH!=0 else "❌"
        await m.answer(f"📊 Состояние\n━━━━━━━━━━━━\n🧠 {ram}\n⏱ {h}ч {mn}м\n💾 {sz} KB\n📡 {ch}\n━━━━━━━━━━━━\n👥 {u}\n📦 {p}\n📋 {o}\n💬 {tk}")
    except.answer(f"❌ {e}")

@dp.message(Command("answer"))
async def cmd_ans(m):
    if not adm(m.from_user.id): return
    try: parts=m.text.split(" ",2); tid=int(parts[1]); txt=parts[2]
    except: await m.answer("/answer ID текст"); return
    async with aiosqlite.connect(DB) as d:
        c=await d.execute("SELECT user_id FROM tickets WHERE id=?",(tid,)); r=await c.fetchone(); await c.close()
        if not r: await m.answer("Не найден"); return
        await d.execute("UPDATE tickets SET answer=? WHERE id=?",(txt,tid)); await d.commit()
    try: await bot.send_message(r[0],f"📩 Ответ:\n{txt}"); await m.answer("✅ Отправлено")
    except Exception as e: await m.answer(f"❌ {e}")

@dp.message(F.photo | F.video)
async def h_media(m):
    u=m.from_user.id
    if US.get(u)!="bc" or not ast(u): return
    if m.photo: df={"type":"photo","photo_id":m.photo[-1].file_id,"caption":m.caption or ""}
    else: df={"type":"video","video_id":m.video.file_id,"caption":m.caption or ""}
    TB[u]=df; US.pop(u,None)
    us=await all_u()
    b=InlineKeyboardBuilder(); b.add(InlineKeyboardButton(text=f"✅ Отправить {len(us)}",callback_data="bc_go"),InlineKeyboardButton(text="❌ Отмена",callback_data="bc_no")); b.adjust(1)
    pv=f"[{'Фото' if df['type']=='photo' else 'Видео'}] {(df.get('caption') or '')[:80]}"
    await m.answer(f"📢 Проверь\n👥 {len(us)}\n📄 {pv}",reply_markup=b.as_markup())

@dp.message(F.text,~F.text.startswith("/"),~F.text.in_(BTNS_L))
async def h_input(m):
    u=m.from_user.id; s=US.get(u)

    if s=="bc":
        if not ast(u): return
        TB[u]={"type":"text","text":m.text}; US.pop(u,None)
        us=await all_u()
        b=InlineKeyboardBuilder(); b.add(InlineKeyboardButton(text=f"✅ Отправить {len(us)}",callback_data="bc_go"),InlineKeyboardButton(text="❌ Отмена",callback_data="bc_no")); b.adjust(1)
        pv=m.text[:100]+("..." if len(m.text)>100 else "")
        await m.answer(f"📢 Проверь\n👥 {len(us)}\n📄 {pv}",reply_markup=b.as_markup()); return

    if s=="pwd":
        if not adm(u): US.pop(u,None); return
        if m.text.strip()==PWD: AS[u]=True; US.pop(u,None); await m.answer("🔧 Админ-панель:",reply_markup=amenu())
        else: US.pop(u,None); await m.answer("❌ Неверно")
        return

    if s=="pn":
        if not ast(u): return
        TP[u]={"name":m.text}; US[u]="pp"; await m.answer("💰 Цена:"); return

    if s=="pp":
        if not ast(u): return
        if not m.text.strip().isdigit(): await m.answer("Число!"); return
        TP[u]["price"]=int(m.text.strip()); US[u]="pd"; await m.answer("📝 Описание:"); return

    if s=="pd":
        if not ast(u): return
        TP[u]["desc"]=m.text; US[u]="pc"; await m.answer("📂 Категория:"); return

    if s=="pc":
        if not ast(u): return
        TP[u]["category"]=m.text; p=TP.pop(u,None); US.pop(u,None)
        if not p: await m.answer("Ошибка",reply_markup=amenu()); return
        try:
            async with aiosqlite.connect(DB) as d:
                await d.execute("INSERT INTO products(name,price,description,category) VALUES(?,?,?,?)",(p["name"],p["price"],p["desc"],p["category"])); await d.commit()
            await m.answer(f"✅ '{p['name']}' добавлен",reply_markup=amenu())
        except Exception as e: await m.answer(f"❌ {e}",reply_markup=amenu())
        return

    if s=="dp":
        if not ast(u): return
        US.pop(u,None)
        if not m.text.strip().isdigit(): await m.answer("ID числом",reply_markup=amenu()); return
        pid=int(m.text.strip())
        async with aiosqlite.connect(DB) as d:
            await d.execute("DELETE FROM products WHERE id=?",(pid,)); await d.execute("DELETE FROM cart WHERE product_id=?",(pid,)); await d.commit()
        await m.answer(f"✅ #{pid} удалён",reply_markup=amenu()); return

    if s=="srch":
        US.pop(u,None)
        async with aiosqlite.connect(DB) as d:
            c=await d.execute("SELECT id,name,price FROM products WHERE name LIKE ?",(f"%{m.text}%",)); r=await c.fetchall(); await c.close()
        if not r: await m.answer("❌ Не найдено"); return
        await m.answer("🔍\n"+"\n".join(f"#{x[0]} {x[1]} — {x[2]} ₽" for x in r)); return

    if s=="sup":
        US.pop(u,None)
        async with aiosqlite.connect(DB) as d:
            c=await d.execute("INSERT INTO tickets(user_id,message) VALUES(?,?)",(u,m.text)); tid=c.lastrowid; await c.close(); await d.commit()
        if adm(AID):
            try: await bot.send_message(AID,f"🆘 Тикет #{tid}\n{m.from_user.full_name} ({su(m.from_user)})\n🆔 <code>{m.from_user.id}</code>\n{m.text}\n\n/answer {tid} текст\n<a href='tg://user?id={m.from_user.id}'>💬 Написать</a>")
            except: pass
        await m.answer("✅ Отправлено"); return

async def main():
    if not await healthy() and not await has_data() and CH!=0:
        log.warning("База пустая — восстанавливаю из канала"); await restore_ch()
    await idb()
    await bot.delete_webhook(drop_pending_updates=True)
    Thread(target=run_flask,daemon=True).start()
    asyncio.create_task(ab_loop())
    asyncio.create_task(backup("запуск"))
    log.info("Бот запущен")
    try: await dp.start_polling(bot)
    finally: await bot.session.close()

if __name__=="__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt,SystemExit): log.info("Остановлен")
