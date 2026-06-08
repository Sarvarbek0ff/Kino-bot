import logging
import asyncio
import os
import aiohttp
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
import asyncpg

load_dotenv()

BOT_TOKEN        = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_IDS        = [7370706915, 5783390460]
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@Aniyoof")
CHANNEL_ID       = os.getenv("CHANNEL_ID", "-100000000000")
BOT_USERNAME     = os.getenv("BOT_USERNAME", "aniyoof_bot")
DATABASE_URL     = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
ADVERTISER_USERNAME = os.getenv("ADVERTISER_USERNAME", "@Sarvarbek_offf")
ADMIN_USERNAMES  = os.getenv("ADMIN_USERNAMES", "@admin1").split(",")
PAYMENT_CARD     = os.getenv("PAYMENT_CARD", "8600 0000 0000 0000")

JANRLAR = ["Aksyon","Komediya","Drama","Romantika","Fantastika","Sehrli",
           "Jangovar san'at","Maktab","Isekai","Triller","Qo'rqinch","Sport",
           "Sarguzasht","Tarix","Musiqiy","Psixologik","Supernatural"]
YILLAR = [str(y) for y in range(2024, 1999, -1)]
YOSH_CHEGARALAR = ["10+","11+","12+","13+","14+","15+","16+","17+","18+","Belgilanmagan"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

pool = None
# Qism qo'shishda race condition oldini olish uchun lock
_episode_locks: dict = {}
# Media group buffer: {admin_id: {media_group_id: [video_file_ids]}}
_mg_buffer: dict = {}
_mg_timers: dict = {}

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, ssl="require", statement_cache_size=0)
    async with pool.acquire() as c:
        await c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            username TEXT, ism TEXT, yosh INTEGER, jins TEXT,
            qiziqishlar TEXT, raqam TEXT,
            premium BOOLEAN DEFAULT FALSE,
            premium_tugash TIMESTAMP,
            royxat_sanasi TIMESTAMP DEFAULT NOW(),
            korgan_count INTEGER DEFAULT 0,
            is_blocked BOOLEAN DEFAULT FALSE
        );
        CREATE TABLE IF NOT EXISTS animes (
            id SERIAL PRIMARY KEY,
            nomi TEXT NOT NULL, kodi TEXT UNIQUE NOT NULL,
            janr TEXT, yil INTEGER,
            fasllar_soni INTEGER DEFAULT 1,
            qismlar_soni INTEGER DEFAULT 0,
            joylangan_qismlar INTEGER DEFAULT 0,
            holati TEXT DEFAULT 'Davom etmoqda',
            yosh_chegarasi TEXT DEFAULT 'Belgilanmagan',
            media_file_id TEXT, media_type TEXT DEFAULT 'photo',
            tavsif TEXT DEFAULT '',
            korish_soni INTEGER DEFAULT 0,
            reyting REAL DEFAULT 0,
            reyting_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS seasons (
            id SERIAL PRIMARY KEY,
            anime_id INTEGER REFERENCES animes(id) ON DELETE CASCADE,
            fasl_nomi TEXT, fasl_raqami INTEGER
        );
        CREATE TABLE IF NOT EXISTS episodes (
            id SERIAL PRIMARY KEY,
            season_id INTEGER REFERENCES seasons(id) ON DELETE CASCADE,
            anime_id INTEGER REFERENCES animes(id) ON DELETE CASCADE,
            qism_raqami INTEGER, video_file_id TEXT NOT NULL, nomi TEXT
        );
        CREATE TABLE IF NOT EXISTS favorites (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            anime_id INTEGER REFERENCES animes(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, anime_id)
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            anime_id INTEGER REFERENCES animes(id) ON DELETE CASCADE,
            fasl INTEGER DEFAULT 1, qism INTEGER DEFAULT 1,
            UNIQUE(user_id, anime_id)
        );
        CREATE TABLE IF NOT EXISTS ratings (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            anime_id INTEGER REFERENCES animes(id) ON DELETE CASCADE,
            baho REAL NOT NULL,
            UNIQUE(user_id, anime_id)
        );
        CREATE TABLE IF NOT EXISTS comments (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            anime_id INTEGER REFERENCES animes(id) ON DELETE CASCADE,
            matn TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS premium_requests (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL, tarif TEXT NOT NULL,
            screenshot_file_id TEXT,
            holati TEXT DEFAULT 'kutilmoqda',
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            anime_id INTEGER REFERENCES animes(id) ON DELETE CASCADE,
            UNIQUE(user_id, anime_id)
        );
        """)
    async with pool.acquire() as c:
        for sql in [
            "ALTER TABLE animes ADD COLUMN IF NOT EXISTS joylangan_qismlar INTEGER DEFAULT 0",
            "ALTER TABLE animes ADD COLUMN IF NOT EXISTS yosh_chegarasi TEXT DEFAULT 'Belgilanmagan'",
        ]:
            try: await c.execute(sql)
            except: pass
    logger.info("✅ Database ulandi!")

async def get_user(tid):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE telegram_id=$1", tid)

async def create_user(tid, username=None):
    async with pool.acquire() as c:
        await c.execute("INSERT INTO users(telegram_id,username) VALUES($1,$2) ON CONFLICT DO NOTHING", tid, username)

async def update_user(tid, **kw):
    if not kw: return
    sets = ", ".join(f"{k}=${i+2}" for i,k in enumerate(kw))
    async with pool.acquire() as c:
        await c.execute(f"UPDATE users SET {sets} WHERE telegram_id=$1", tid, *kw.values())

async def get_all_users():
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM users WHERE is_blocked=FALSE")

async def get_non_premium_users():
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM users WHERE premium=FALSE AND is_blocked=FALSE")

async def get_premium_users():
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM users WHERE premium=TRUE AND is_blocked=FALSE")

async def get_user_by_username(uname):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE username=$1", uname.lstrip("@"))

async def get_user_by_phone(raqam):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE raqam=$1", raqam)

async def get_top_watchers(limit=20):
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM users ORDER BY korgan_count DESC LIMIT $1", limit)

async def get_user_rank(tid):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT rank FROM (SELECT telegram_id, RANK() OVER (ORDER BY korgan_count DESC) as rank FROM users) r WHERE telegram_id=$1", tid)
        return row['rank'] if row else 0

async def get_stats():
    async with pool.acquire() as c:
        tu  = await c.fetchval("SELECT COUNT(*) FROM users")
        pu  = await c.fetchval("SELECT COUNT(*) FROM users WHERE premium=TRUE")
        ta  = await c.fetchval("SELECT COUNT(*) FROM animes")
        tv  = await c.fetchval("SELECT SUM(korish_soni) FROM animes") or 0
        tdu = await c.fetchval("SELECT COUNT(*) FROM users WHERE royxat_sanasi::date=CURRENT_DATE")
        return {"total_users":tu,"premium_users":pu,"total_animes":ta,"total_views":tv,"today_users":tdu}

async def create_anime(nomi,kodi,janr,yil,fasllar_soni,qismlar_soni,holati,
                       media_file_id,media_type,tavsif="",yosh_chegarasi="Belgilanmagan"):
    async with pool.acquire() as c:
        return await c.fetchrow(
            "INSERT INTO animes(nomi,kodi,janr,yil,fasllar_soni,qismlar_soni,holati,"
            "media_file_id,media_type,tavsif,yosh_chegarasi) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING *",
            nomi,kodi,janr,yil,fasllar_soni,qismlar_soni,holati,media_file_id,media_type,tavsif,yosh_chegarasi)

async def get_anime(aid):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM animes WHERE id=$1", aid)

async def get_anime_by_code(kodi):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM animes WHERE kodi=$1", kodi)

async def search_anime_name(q):
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM animes WHERE LOWER(nomi) LIKE LOWER($1) LIMIT 10", f"%{q}%")

async def search_anime_genre_year(janr, yil):
    async with pool.acquire() as c:
        if janr and yil:
            return await c.fetch("SELECT * FROM animes WHERE LOWER(janr) LIKE LOWER($1) AND yil=$2 LIMIT 10", f"%{janr}%", int(yil))
        elif janr:
            return await c.fetch("SELECT * FROM animes WHERE LOWER(janr) LIKE LOWER($1) LIMIT 10", f"%{janr}%")
        elif yil:
            return await c.fetch("SELECT * FROM animes WHERE yil=$1", int(yil))
        return []

async def get_top_views(limit=20):
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM animes ORDER BY korish_soni DESC LIMIT $1", limit)

async def get_top_rating(limit=20):
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM animes WHERE reyting_count>0 ORDER BY reyting DESC LIMIT $1", limit)

async def get_random_anime():
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM animes ORDER BY RANDOM() LIMIT 1")

async def get_all_animes():
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM animes ORDER BY nomi")

async def delete_anime(aid):
    async with pool.acquire() as c:
        await c.execute("DELETE FROM animes WHERE id=$1", aid)

async def inc_views(aid):
    async with pool.acquire() as c:
        await c.execute("UPDATE animes SET korish_soni=korish_soni+1 WHERE id=$1", aid)

async def get_recommended(janr, limit=5):
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM animes WHERE LOWER(janr) LIKE LOWER($1) ORDER BY reyting DESC LIMIT $2", f"%{janr}%", limit)

async def create_season(anime_id, fasl_nomi, fasl_raqami):
    async with pool.acquire() as c:
        return await c.fetchrow(
            "INSERT INTO seasons(anime_id,fasl_nomi,fasl_raqami) VALUES($1,$2,$3) RETURNING *",
            anime_id, fasl_nomi, fasl_raqami)

async def get_seasons(anime_id):
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM seasons WHERE anime_id=$1 ORDER BY fasl_raqami", anime_id)

async def get_next_episode_number(season_id):
    """Bo'sh qism raqamini topadi - o'chirilgan qismlar o'rnini to'ldiradi"""
    async with pool.acquire() as c:
        existing = await c.fetch("SELECT qism_raqami FROM episodes WHERE season_id=$1 ORDER BY qism_raqami", season_id)
        existing_nums = {r['qism_raqami'] for r in existing}
        n = 1
        while n in existing_nums:
            n += 1
        return n

async def create_episode(season_id, anime_id, qism_raqami, video_file_id, nomi=""):
    async with pool.acquire() as c:
        ep = await c.fetchrow(
            "INSERT INTO episodes(season_id,anime_id,qism_raqami,video_file_id,nomi) VALUES($1,$2,$3,$4,$5) RETURNING *",
            season_id, anime_id, qism_raqami, video_file_id, nomi)
        await c.execute("UPDATE animes SET joylangan_qismlar=joylangan_qismlar+1 WHERE id=$1", anime_id)
        return ep

async def get_episodes(season_id):
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM episodes WHERE season_id=$1 ORDER BY qism_raqami", season_id)

async def get_episode(eid):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM episodes WHERE id=$1", eid)

async def delete_episode(eid):
    async with pool.acquire() as c:
        ep = await c.fetchrow("SELECT * FROM episodes WHERE id=$1", eid)
        if ep:
            await c.execute("DELETE FROM episodes WHERE id=$1", eid)
            await c.execute("UPDATE animes SET joylangan_qismlar=GREATEST(joylangan_qismlar-1,0) WHERE id=$1", ep['anime_id'])
        return ep

async def add_favorite(uid, aid):
    async with pool.acquire() as c:
        try: await c.execute("INSERT INTO favorites(user_id,anime_id) VALUES($1,$2)", uid, aid); return True
        except: return False

async def remove_favorite(uid, aid):
    async with pool.acquire() as c:
        await c.execute("DELETE FROM favorites WHERE user_id=$1 AND anime_id=$2", uid, aid)

async def get_favorites(uid):
    async with pool.acquire() as c:
        return await c.fetch(
            "SELECT a.* FROM animes a JOIN favorites f ON f.anime_id=a.id WHERE f.user_id=$1 ORDER BY f.created_at DESC", uid)

async def is_favorite(uid, aid):
    async with pool.acquire() as c:
        return bool(await c.fetchrow("SELECT id FROM favorites WHERE user_id=$1 AND anime_id=$2", uid, aid))

async def add_watchlist(uid, aid):
    async with pool.acquire() as c:
        try: await c.execute("INSERT INTO watchlist(user_id,anime_id) VALUES($1,$2)", uid, aid); return True
        except: return False

async def remove_watchlist(uid, aid):
    async with pool.acquire() as c:
        await c.execute("DELETE FROM watchlist WHERE user_id=$1 AND anime_id=$2", uid, aid)

async def get_watchlist(uid):
    async with pool.acquire() as c:
        return await c.fetch(
            "SELECT a.*,w.fasl,w.qism FROM animes a JOIN watchlist w ON w.anime_id=a.id WHERE w.user_id=$1", uid)

async def is_in_watchlist(uid, aid):
    async with pool.acquire() as c:
        return bool(await c.fetchrow("SELECT id FROM watchlist WHERE user_id=$1 AND anime_id=$2", uid, aid))

async def add_rating(uid, aid, baho):
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ratings(user_id,anime_id,baho) VALUES($1,$2,$3) ON CONFLICT(user_id,anime_id) DO UPDATE SET baho=$3",
            uid, aid, baho)
        r = await c.fetchrow("SELECT AVG(baho) as avg, COUNT(*) as cnt FROM ratings WHERE anime_id=$1", aid)
        if r and r['avg']:
            await c.execute("UPDATE animes SET reyting=$1, reyting_count=$2 WHERE id=$3",
                            round(float(r['avg']),1), r['cnt'], aid)

async def get_user_rating(uid, aid):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM ratings WHERE user_id=$1 AND anime_id=$2", uid, aid)

async def add_comment(uid, aid, matn):
    async with pool.acquire() as c:
        await c.execute("INSERT INTO comments(user_id,anime_id,matn) VALUES($1,$2,$3)", uid, aid, matn)

async def get_comments(aid, limit=10, offset=0):
    async with pool.acquire() as c:
        return await c.fetch(
            "SELECT c.*,u.ism,u.username FROM comments c JOIN users u ON u.telegram_id=c.user_id "
            "WHERE c.anime_id=$1 ORDER BY c.created_at DESC LIMIT $2 OFFSET $3", aid, limit, offset)

async def count_comments(aid):
    async with pool.acquire() as c:
        return await c.fetchval("SELECT COUNT(*) FROM comments WHERE anime_id=$1", aid)

async def create_premium_req(uid, tarif, screenshot_id):
    async with pool.acquire() as c:
        return await c.fetchrow(
            "INSERT INTO premium_requests(user_id,tarif,screenshot_file_id) VALUES($1,$2,$3) RETURNING *",
            uid, tarif, screenshot_id)

async def update_premium_req(rid, holati):
    async with pool.acquire() as c:
        await c.execute("UPDATE premium_requests SET holati=$1 WHERE id=$2", holati, rid)

async def get_premium_req(rid):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM premium_requests WHERE id=$1", rid)

async def add_notif(uid, aid):
    async with pool.acquire() as c:
        try: await c.execute("INSERT INTO notifications(user_id,anime_id) VALUES($1,$2)", uid, aid); return True
        except: return False

async def remove_notif(uid, aid):
    async with pool.acquire() as c:
        await c.execute("DELETE FROM notifications WHERE user_id=$1 AND anime_id=$2", uid, aid)

async def get_notif_subs(aid):
    async with pool.acquire() as c:
        return await c.fetch(
            "SELECT u.telegram_id FROM users u JOIN notifications n ON n.user_id=u.telegram_id "
            "WHERE n.anime_id=$1 AND u.premium=TRUE AND u.is_blocked=FALSE", aid)

async def is_notif_on(uid, aid):
    async with pool.acquire() as c:
        return bool(await c.fetchrow("SELECT id FROM notifications WHERE user_id=$1 AND anime_id=$2", uid, aid))

# trace.moe orqali rasm qidirish
async def search_anime_by_image(file_url: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.trace.moe/search?url={file_url}&anilistInfo") as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get('result'):
                    return None
                top = data['result'][0]
                similarity = top.get('similarity', 0)
                if similarity < 0.7:
                    return None
                anilist = top.get('anilistInfo', {})
                title = (anilist.get('title', {}).get('romaji') or
                         anilist.get('title', {}).get('english') or
                         anilist.get('title', {}).get('native') or "")
                return {"title": title, "similarity": round(similarity * 100, 1)}
    except:
        return None

def is_admin(uid): return uid in ADMIN_IDS

async def is_premium(uid):
    u = await get_user(uid)
    if not u or not u['premium']: return False
    if u['premium_tugash'] and datetime.now() > u['premium_tugash']:
        await update_user(uid, premium=False, premium_tugash=None)
        return False
    return True

async def check_sub(bot: Bot, uid):
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        return m.status not in ["left","kicked","banned"]
    except: return False

def anime_text(a):
    e = "✅" if a['holati']=="Tugallangan" else "🔄"
    yosh = a.get('yosh_chegarasi') or 'Belgilanmagan'
    jami = a['qismlar_soni'] or 0
    joylangan = a.get('joylangan_qismlar') or 0
    qism_txt = f"{jami}/{joylangan} qism" if jami > 0 else f"{joylangan} qism"
    t  = f"🎬 <b>{a['nomi']}</b>\n━━━━━━━━━━━━━━━━━━\n"
    t += f"📁 Kod: <code>{a['kodi']}</code>\n"
    t += f"🎭 Janr: {a['janr']}\n📅 Yil: {a['yil']}\n"
    t += f"🔞 Yosh: {yosh}\n"
    t += f"🗂 Fasllar: {a['fasllar_soni']}\n📺 Qismlar: {qism_txt}\n"
    t += f"{e} Holati: {a['holati']}\n"
    t += f"⭐ Baho: {a['reyting']}/10 ({a['reyting_count']} ta)\n"
    t += f"👁 Ko'rishlar: {a['korish_soni']:,}\n"
    if a['tavsif']: t += f"📝 {a['tavsif']}\n"
    t += "\n<i>@Aniyoof</i>"
    return t

def premium_end_date(tarif):
    return datetime.now() + timedelta(days={"1oy":30,"3oy":90,"1yil":365}.get(tarif,30))

def tarif_name(tarif):
    return {"1oy":"1 oylik — 10,000 so'm","3oy":"3 oylik — 27,000 so'm",
            "1yil":"1 yillik — 89,000 so'm"}.get(tarif, tarif)

def edit_txt(a):
    return (f"✏️ <b>{a['nomi']}</b>\n\n"
            f"📁 Kod: {a['kodi']}\n🎭 Janr: {a['janr']}\n📅 Yil: {a['yil']}\n"
            f"🔞 Yosh: {a.get('yosh_chegarasi') or '—'}\n"
            f"🗂 Fasllar: {a['fasllar_soni']}\n📺 Qismlar: {a['qismlar_soni']}\n"
            f"🔄 Holat: {a['holati']}\n📝 Tavsif: {a['tavsif'] or '—'}\n\n"
            f"Qaysi maydonni o'zgartirmoqchisiz?")

def kb_main():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🔍 Anime izlash")],
        [KeyboardButton(text="💎 Premium olish"), KeyboardButton(text="📢 Reklama berish")],
        [KeyboardButton(text="⭐ Reyting"),        KeyboardButton(text="❤️ Sevimlilar")],
        [KeyboardButton(text="👤 Profilim"),       KeyboardButton(text="📋 Watch list")],
        [KeyboardButton(text="📩 Murojat uchun")]
    ], resize_keyboard=True)

def kb_admin():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Anime qo'shish")],
        [KeyboardButton(text="✏️ Anime tahrirlash"), KeyboardButton(text="✂️ Qism o'chirish")],
        [KeyboardButton(text="📝 Post yaratish")],
        [KeyboardButton(text="💎 Premium berish")],
        [KeyboardButton(text="📊 Statistika")],
        [KeyboardButton(text="📢 Xabar yuborish")],
        [KeyboardButton(text="👤 User paneliga o'tish")]
    ], resize_keyboard=True)

def kb_cancel():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)

def kb_skip():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="⏭ O'tkazib yuborish")],
        [KeyboardButton(text="❌ Bekor qilish")]
    ], resize_keyboard=True)

def kb_phone():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📱 Raqamni ulashish", request_contact=True)],
        [KeyboardButton(text="❌ Bekor qilish")]
    ], resize_keyboard=True, one_time_keyboard=True)

def ik_gender():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👦 Erkak", callback_data="gender_erkak"),
        InlineKeyboardButton(text="👧 Ayol",  callback_data="gender_ayol")
    ]])

def ik_channel():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📢 {CHANNEL_USERNAME}", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")]
    ])

def ik_search():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Nomi bilan",  callback_data="s_name"),
         InlineKeyboardButton(text="🔢 Kodi bilan",  callback_data="s_code")],
        [InlineKeyboardButton(text="🎭 Janr/Yil 💎", callback_data="s_janryil")],
        [InlineKeyboardButton(text="🖼 Rasm bilan 💎", callback_data="s_image"),
         InlineKeyboardButton(text="🎲 Tasodifiy",   callback_data="s_random")],
        [InlineKeyboardButton(text="🔥 Eng ko'p ko'rilgan 💎", callback_data="s_top")],
        [InlineKeyboardButton(text="🌟 Tavsiya",     callback_data="s_recommend")],
        [InlineKeyboardButton(text="🔙 Orqaga",      callback_data="back_main")]
    ])

def ik_janr_select(sel=None):
    if sel is None: sel = []
    b = InlineKeyboardBuilder()
    for j in JANRLAR:
        b.button(text=("✅ " if j in sel else "") + j, callback_data=f"seljanr_{j}")
    b.button(text="📅 Yil tanlash →", callback_data="goto_yil_sel")
    b.button(text="🔙 Orqaga", callback_data="back_search")
    b.adjust(2)
    return b.as_markup()

def ik_yil_select(sel=None):
    if sel is None: sel = []
    b = InlineKeyboardBuilder()
    for y in YILLAR:
        b.button(text=("✅ " if y in sel else "") + y, callback_data=f"selyil_{y}")
    b.button(text="🔍 Qidirish", callback_data="do_janryil_search")
    b.button(text="🔙 Janrga qaytish", callback_data="goto_janr_sel")
    b.adjust(3)
    return b.as_markup()

def ik_anime_watch(aid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="▶️ Tomosha qilish", callback_data=f"watch_{aid}")
    ]])

def ik_anime_watch_channel(aid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="▶️ Tomosha qilish",
                             url=f"https://t.me/{BOT_USERNAME}?start=anime_{aid}")
    ]])

def ik_anime_extra(aid, is_fav=False, is_wl=False, is_notif=False):
    fav   = "💔 Sevimlilardan olish" if is_fav else "❤️ Sevimlilarga"
    wl    = "📋 Watch listdan olish" if is_wl  else "📋 Watch list 💎"
    notif = "🔕 Bildirishnomani o'chirish" if is_notif else "🔔 Bildirishnoma 💎"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=fav, callback_data=f"fav_{aid}"),
         InlineKeyboardButton(text=wl,  callback_data=f"wl_{aid}")],
        [InlineKeyboardButton(text="💬 Izohlar",     callback_data=f"cmt_{aid}_0"),
         InlineKeyboardButton(text="⭐ Baho berish", callback_data=f"rate_{aid}")],
        [InlineKeyboardButton(text=notif, callback_data=f"noff_{aid}" if is_notif else f"non_{aid}")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_search")]
    ])

def ik_seasons(seasons, aid):
    b = InlineKeyboardBuilder()
    for s in seasons:
        b.button(text=f"📂 {s['fasl_nomi']}", callback_data=f"ssn_{s['id']}_{aid}")
    b.button(text="🔙 Orqaga", callback_data=f"ac_{aid}")
    b.adjust(2)
    return b.as_markup()

def ik_episodes(eps, sid, aid, admin=False):
    b = InlineKeyboardBuilder()
    for e in eps:
        b.button(text=f"▶️ {e['qism_raqami']}-qism", callback_data=f"ep_{e['id']}")
    b.adjust(3)
    if eps: b.button(text="📦 Barchasini yuborish", callback_data=f"allep_{sid}")
    if admin: b.button(text="🗑 Qism o'chirish", callback_data=f"deleplist_{sid}_{aid}")
    b.button(text="🔙 Orqaga", callback_data=f"watch_{aid}")
    b.adjust(3)
    return b.as_markup()

def ik_episodes_delete(eps, sid, aid):
    b = InlineKeyboardBuilder()
    for e in eps:
        b.button(text=f"🗑 {e['qism_raqami']}-qism", callback_data=f"delep_{e['id']}_{sid}_{aid}")
    b.button(text="🔙 Orqaga", callback_data=f"ssn_{sid}_{aid}")
    b.adjust(2)
    return b.as_markup()

def ik_premium_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Bot orqali olish",   callback_data="pr_bot")],
        [InlineKeyboardButton(text="👤 Admin orqali olish", callback_data="pr_admin")],
        [InlineKeyboardButton(text="🔙 Orqaga",             callback_data="back_main")]
    ])

def ik_tarif():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 oy — 10,000 so'm",  callback_data="tarif_1oy")],
        [InlineKeyboardButton(text="3 oy — 27,000 so'm",  callback_data="tarif_3oy")],
        [InlineKeyboardButton(text="1 yil — 89,000 so'm", callback_data="tarif_1yil")],
        [InlineKeyboardButton(text="🔙 Orqaga",           callback_data="pr_menu")]
    ])

def ik_pr_cancel():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="pr_menu")
    ]])

def ik_admin_pr_req(rid, uid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"apr_{rid}_{uid}"),
        InlineKeyboardButton(text="❌ Rad etish",  callback_data=f"rpr_{rid}_{uid}")
    ]])

def ik_rating(aid):
    b = InlineKeyboardBuilder()
    for i in range(1, 11):
        b.button(text=f"{'⭐' if i<=5 else '🌟'} {i}", callback_data=f"rt_{aid}_{i}")
    b.button(text="🔙 Orqaga", callback_data=f"ac_{aid}")
    b.adjust(5)
    return b.as_markup()

def ik_comments(aid, offset, total):
    b = InlineKeyboardBuilder()
    if offset > 0:        b.button(text="⬅️ Oldingi", callback_data=f"cmt_{aid}_{offset-10}")
    if offset+10 < total: b.button(text="➡️ Keyingi", callback_data=f"cmt_{aid}_{offset+10}")
    b.button(text="✍️ Izoh yozish", callback_data=f"wcmt_{aid}")
    b.button(text="🔙 Orqaga",     callback_data=f"ac_{aid}")
    b.adjust(2)
    return b.as_markup()

def ik_reyting():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Eng ko'p ko'rganlar", callback_data="rtg_users")],
        [InlineKeyboardButton(text="🌟 Anime reytingi",      callback_data="rtg_anime")],
        [InlineKeyboardButton(text="🔙 Orqaga",              callback_data="back_main")]
    ])

def ik_profile():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ismni o'zgartirish",         callback_data="ed_ism")],
        [InlineKeyboardButton(text="✏️ Yoshni o'zgartirish",        callback_data="ed_yosh")],
        [InlineKeyboardButton(text="✏️ Jinsni o'zgartirish",        callback_data="ed_jins")],
        [InlineKeyboardButton(text="✏️ Qiziqishlarni o'zgartirish", callback_data="ed_qiz")],
        [InlineKeyboardButton(text="🔙 Orqaga",                     callback_data="back_main")]
    ])

def ik_edit_gender():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👦 Erkak", callback_data="eg_erkak"),
        InlineKeyboardButton(text="👧 Ayol",  callback_data="eg_ayol")
    ]])

def ik_back(cb="back_main"):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔙 Orqaga", callback_data=cb)
    ]])

def ik_premium_req():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Premium olish", callback_data="pr_menu")],
        [InlineKeyboardButton(text="🔙 Orqaga",        callback_data="back_search")]
    ])

def ik_admin_list(animes):
    b = InlineKeyboardBuilder()
    for a in animes:
        b.button(text=f"🎬 {a['kodi']} — {a['nomi']}", callback_data=f"aa_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()

def ik_admin_action(aid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📺 Qism qo'shish", callback_data=f"addep_{aid}")],
        [InlineKeyboardButton(text="📂 Fasl qo'shish", callback_data=f"addsn_{aid}")],
        [InlineKeyboardButton(text="🔙 Orqaga",        callback_data="adm_alist")]
    ])

def ik_admin_add():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Yangi anime",       callback_data="adm_new")],
        [InlineKeyboardButton(text="📝 Davomini qo'shish", callback_data="adm_cont")],
        [InlineKeyboardButton(text="🔙 Orqaga",            callback_data="adm_back")]
    ])

def ik_seasons_ep(seasons, aid):
    b = InlineKeyboardBuilder()
    for s in seasons:
        b.button(text=f"📂 {s['fasl_nomi']}", callback_data=f"sel_sn_{s['id']}_{aid}")
    b.button(text="🔙 Orqaga", callback_data=f"aa_{aid}")
    b.adjust(1)
    return b.as_markup()

def ik_admin_tarif():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 oy",  callback_data="gv_1oy")],
        [InlineKeyboardButton(text="3 oy",  callback_data="gv_3oy")],
        [InlineKeyboardButton(text="1 yil", callback_data="gv_1yil")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]
    ])

def ik_admins():
    b = InlineKeyboardBuilder()
    for u in ADMIN_USERNAMES:
        u = u.strip()
        b.button(text=f"👤 {u}", url=f"https://t.me/{u.lstrip('@')}")
    b.button(text="🔙 Orqaga", callback_data="pr_menu")
    b.adjust(1)
    return b.as_markup()

def ik_confirm_bc():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash",   callback_data="bc_yes"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="bc_no")
    ]])

def ik_bc_target():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Barcha userlar",      callback_data="bc_all")],
        [InlineKeyboardButton(text="💎 Faqat premiumlar",    callback_data="bc_premium")],
        [InlineKeyboardButton(text="👤 Faqat oddiy userlar", callback_data="bc_free")],
        [InlineKeyboardButton(text="🔙 Bekor qilish",        callback_data="adm_back")]
    ])

def ik_anime_edit_fields(aid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📛 Nomi",           callback_data=f"efield_nomi_{aid}"),
         InlineKeyboardButton(text="📁 Kodi",           callback_data=f"efield_kodi_{aid}")],
        [InlineKeyboardButton(text="🎭 Janri",          callback_data=f"efield_janr_{aid}"),
         InlineKeyboardButton(text="📅 Yili",           callback_data=f"efield_yil_{aid}")],
        [InlineKeyboardButton(text="🗂 Fasllar soni",   callback_data=f"efield_fasllar_{aid}"),
         InlineKeyboardButton(text="📺 Qismlar soni",   callback_data=f"efield_qismlar_{aid}")],
        [InlineKeyboardButton(text="🔄 Holati",         callback_data=f"efield_holati_{aid}"),
         InlineKeyboardButton(text="📝 Tavsif",         callback_data=f"efield_tavsif_{aid}")],
        [InlineKeyboardButton(text="🔞 Yosh chegarasi", callback_data=f"efield_yosh_{aid}")],
        [InlineKeyboardButton(text="🖼 Rasm/Video",     callback_data=f"efield_media_{aid}")],
        [InlineKeyboardButton(text="🗑 Animeni o'chirish", callback_data=f"delanime_{aid}")],
        [InlineKeyboardButton(text="🔙 Orqaga",         callback_data="adm_back")]
    ])

def ik_holati_select(aid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Davom etmoqda", callback_data=f"setholati_davom_{aid}"),
         InlineKeyboardButton(text="✅ Tugallangan",   callback_data=f"setholati_tugal_{aid}")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"editanim_{aid}")]
    ])

def ik_yosh_select(aid):
    b = InlineKeyboardBuilder()
    for y in YOSH_CHEGARALAR:
        cb_val = y.replace("+","plus")
        b.button(text=y, callback_data=f"setyosh_{cb_val}_{aid}")
    b.button(text="🔙 Orqaga", callback_data=f"editanim_{aid}")
    b.adjust(3)
    return b.as_markup()

def ik_confirm_del_anime(aid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, o'chir", callback_data=f"confirmdel_{aid}"),
        InlineKeyboardButton(text="❌ Yo'q",       callback_data=f"editanim_{aid}")
    ]])

def ik_post_anime_list(animes):
    b = InlineKeyboardBuilder()
    for a in animes:
        b.button(text=f"🎬 {a['kodi']} — {a['nomi']}", callback_data=f"posta_{a['id']}")
    b.button(text="❌ Bekor qilish", callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()

class Reg(StatesGroup):
    ism=State(); yosh=State(); jins=State(); qiziqish=State(); raqam=State()

class Search(StatesGroup):
    name=State(); code=State(); image=State()

class PremiumPay(StatesGroup):
    screenshot=State()

class EditProfile(StatesGroup):
    ism=State(); yosh=State(); qiziqish=State()

class CommentW(StatesGroup):
    write=State()

class Contact(StatesGroup):
    msg=State()

class AddAnime(StatesGroup):
    info=State(); media=State()

class AddSeason(StatesGroup):
    name=State()

class AddEpisode(StatesGroup):
    sel_season=State(); video=State()

class AdminPremium(StatesGroup):
    find=State(); tarif=State()

class Broadcast(StatesGroup):
    msg=State(); target=State(); confirm=State()

class CreatePost(StatesGroup):
    media=State(); caption=State(); anime_sel=State()

class EditAnime(StatesGroup):
    value=State(); media=State()

router = Router()

INFO_FMT = (
    "Quyidagi formatda yuboring:\n\n"
    "Nomi: \nKod: \nJanr: \nYil: \n"
    "Fasllar: \nQismlar: \n"
    "Holati: Tugallangan yoki Davom etmoqda\n"
    "Yosh: 18+ yoki 16+ yoki 12+ yoki Belgilanmagan\n"
    "Tavsif: (ixtiyoriy)"
)

async def send_card(target: Message, anime, uid):
    fav   = await is_favorite(uid, anime['id'])
    wl    = await is_in_watchlist(uid, anime['id'])
    notif = await is_notif_on(uid, anime['id'])
    txt   = anime_text(anime)
    try:
        if anime['media_type'] == 'video':
            await target.answer_video(anime['media_file_id'], caption=txt,
                reply_markup=ik_anime_watch(anime['id']), parse_mode="HTML")
        else:
            await target.answer_photo(anime['media_file_id'], caption=txt,
                reply_markup=ik_anime_watch(anime['id']), parse_mode="HTML")
    except:
        await target.answer(txt, reply_markup=ik_anime_watch(anime['id']), parse_mode="HTML")
    await target.answer("➕ <b>Qo'shimcha tugmalar:</b>",
        reply_markup=ik_anime_extra(anime['id'], fav, wl, notif), parse_mode="HTML")

async def premium_wall(cb: CallbackQuery):
    pr = await is_premium(cb.from_user.id)
    if not pr:
        await cb.message.edit_text(
            "⚠️ Bu funksiya faqat 💎 <b>Premium</b> foydalanuvchilar uchun!",
            reply_markup=ik_premium_req(), parse_mode="HTML")
    return pr

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    uid = msg.from_user.id; uname = msg.from_user.username
    args = msg.text.split() if msg.text else []
    anime_id_from_link = None
    if len(args) > 1 and args[1].startswith("anime_"):
        try: anime_id_from_link = int(args[1].split("_")[1])
        except: pass
    if is_admin(uid):
        await create_user(uid, uname)
        await msg.answer("👑 Xush kelibsiz, Admin!", reply_markup=kb_admin())
        if anime_id_from_link:
            a = await get_anime(anime_id_from_link)
            if a: await send_card(msg, a, uid)
        return
    user = await get_user(uid)
    if not user: await create_user(uid, uname)
    user = await get_user(uid)
    if not user or not user['ism']:
        if anime_id_from_link: await state.update_data(pending_anime=anime_id_from_link)
        await state.set_state(Reg.ism)
        await msg.answer("👋 Xush kelibsiz!\n\n📝 <b>Ismingizni kiriting:</b>",
                         parse_mode="HTML", reply_markup=kb_cancel()); return
    ok = await check_sub(bot, uid)
    if not ok:
        await msg.answer(f"📢 Kanalga obuna bo'ling!\n\n<b>{CHANNEL_USERNAME}</b>",
                         reply_markup=ik_channel(), parse_mode="HTML"); return
    await msg.answer(f"🌸 Xush kelibsiz, <b>{user['ism']}</b>! 🎌",
                     reply_markup=kb_main(), parse_mode="HTML")
    if anime_id_from_link:
        a = await get_anime(anime_id_from_link)
        if a: await send_card(msg, a, uid)

@router.callback_query(F.data == "check_sub")
async def cb_check_sub(cb: CallbackQuery, state: FSMContext, bot: Bot):
    ok = await check_sub(bot, cb.from_user.id)
    if not ok: await cb.answer("❌ Hali obuna bo'lmadingiz!", show_alert=True); return
    user = await get_user(cb.from_user.id)
    try: await cb.message.delete()
    except: pass
    if not user or not user['ism']:
        await state.set_state(Reg.ism)
        await cb.message.answer("📝 <b>Ismingizni kiriting:</b>", parse_mode="HTML", reply_markup=kb_cancel()); return
    await cb.message.answer(f"✅ Xush kelibsiz, <b>{user['ism']}</b>! 🎌", reply_markup=kb_main(), parse_mode="HTML")

@router.callback_query(F.data == "back_main")
async def cb_back_main(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try: await cb.message.delete()
    except: pass
    await cb.message.answer("🏠 Bosh menyu", reply_markup=kb_main())

@router.message(Reg.ism)
async def reg_ism(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_main()); return
    if not msg.text or len(msg.text) < 2: await msg.answer("❌ To'g'ri ism kiriting."); return
    await state.update_data(ism=msg.text.strip()); await state.set_state(Reg.yosh)
    await msg.answer("🎂 <b>Yoshingizni kiriting:</b>", parse_mode="HTML")

@router.message(Reg.yosh)
async def reg_yosh(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_main()); return
    try:
        y = int(msg.text.strip()); assert 5 <= y <= 100
    except: await msg.answer("❌ To'g'ri yosh kiriting (5-100)."); return
    await state.update_data(yosh=y); await state.set_state(Reg.jins)
    await msg.answer("⚧ <b>Jinsingizni tanlang:</b>", parse_mode="HTML", reply_markup=ik_gender())

@router.callback_query(F.data.in_(["gender_erkak","gender_ayol"]), Reg.jins)
async def reg_jins(cb: CallbackQuery, state: FSMContext):
    jins = "Erkak" if cb.data=="gender_erkak" else "Ayol"
    await state.update_data(jins=jins); await state.set_state(Reg.qiziqish)
    await cb.message.edit_text(f"✅ Jins: <b>{jins}</b>", parse_mode="HTML")
    await cb.message.answer("🎭 <b>Qiziqishlaringiz</b> (ixtiyoriy):", parse_mode="HTML", reply_markup=kb_skip())

@router.message(Reg.qiziqish)
async def reg_qiz(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_main()); return
    q = "" if msg.text == "⏭ O'tkazib yuborish" else msg.text.strip()
    await state.update_data(qiziqish=q); await state.set_state(Reg.raqam)
    await msg.answer("📱 <b>Telefon raqamingizni ulashing:</b>", parse_mode="HTML", reply_markup=kb_phone())

@router.message(Reg.raqam, F.contact)
async def reg_raqam(msg: Message, state: FSMContext, bot: Bot):
    raqam = msg.contact.phone_number; uname = msg.from_user.username
    if not uname:
        await msg.answer("⚠️ Username yo'q! Telegram sozlamalaridan username qo'ying, keyin /start bosing.",
                         reply_markup=kb_cancel())
        await state.clear(); return
    d = await state.get_data()
    await update_user(msg.from_user.id, ism=d['ism'], yosh=d['yosh'], jins=d['jins'],
                      qiziqishlar=d.get('qiziqish',''), raqam=raqam, username=uname)
    pending = d.get('pending_anime'); await state.clear()
    ok = await check_sub(bot, msg.from_user.id)
    if not ok:
        await msg.answer(f"✅ Ro'yxatdan o'tdingiz!\n\n📢 Kanalga obuna bo'ling:\n<b>{CHANNEL_USERNAME}</b>",
                         reply_markup=ik_channel(), parse_mode="HTML")
    else:
        await msg.answer(f"✅ Xush kelibsiz, <b>{d['ism']}</b>! 🎌", reply_markup=kb_main(), parse_mode="HTML")
        if pending:
            a = await get_anime(pending)
            if a: await send_card(msg, a, msg.from_user.id)

@router.message(Reg.raqam)
async def reg_raqam_wrong(msg: Message):
    if msg.text == "❌ Bekor qilish": return
    await msg.answer("📱 Raqamni ulashish tugmasini bosing.", reply_markup=kb_phone())

@router.message(F.text == "🔍 Anime izlash")
async def menu_search(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    ok = await check_sub(bot, msg.from_user.id)
    if not ok: await msg.answer("📢 Avval kanalga obuna bo'ling!", reply_markup=ik_channel()); return
    await msg.answer("🔍 <b>Anime izlash</b>", reply_markup=ik_search(), parse_mode="HTML")

@router.message(F.text == "💎 Premium olish")
async def menu_premium(msg: Message):
    await msg.answer(
        "💎 <b>Aniyoof Premium</b>\n\n"
        "✅ Janr/Yil bilan qidirish\n"
        "✅ Rasm orqali qidirish\n"
        "✅ Eng ko'p ko'rilgan\n"
        "✅ Watch list\n"
        "✅ Bildirishnomalar\n\nTanlang:",
        reply_markup=ik_premium_menu(), parse_mode="HTML")

@router.message(F.text == "📢 Reklama berish")
async def menu_reklama(msg: Message):
    await msg.answer(f"📢 Reklama uchun: {ADVERTISER_USERNAME}", reply_markup=kb_main())

@router.message(F.text == "⭐ Reyting")
async def menu_reyting(msg: Message):
    await msg.answer("⭐ <b>Reyting</b>", reply_markup=ik_reyting(), parse_mode="HTML")

@router.message(F.text == "❤️ Sevimlilar")
async def menu_favs(msg: Message):
    favs = await get_favorites(msg.from_user.id)
    if not favs: await msg.answer("❤️ Sevimlilar bo'sh.", reply_markup=kb_main()); return
    b = InlineKeyboardBuilder()
    for a in favs: b.button(text=f"🎬 {a['nomi']}", callback_data=f"ac_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="back_main"); b.adjust(1)
    await msg.answer(f"❤️ <b>Sevimlilar ({len(favs)} ta):</b>", reply_markup=b.as_markup(), parse_mode="HTML")

@router.message(F.text == "📋 Watch list")
async def menu_wl(msg: Message):
    if not await is_premium(msg.from_user.id):
        await msg.answer("⚠️ Faqat 💎 <b>Premium</b> uchun!", parse_mode="HTML", reply_markup=kb_main()); return
    wl = await get_watchlist(msg.from_user.id)
    if not wl: await msg.answer("📋 Watch list bo'sh.", reply_markup=kb_main()); return
    b = InlineKeyboardBuilder()
    for a in wl: b.button(text=f"🎬 {a['nomi']}", callback_data=f"ac_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="back_main"); b.adjust(1)
    await msg.answer(f"📋 <b>Watch list ({len(wl)} ta):</b>", reply_markup=b.as_markup(), parse_mode="HTML")

@router.message(F.text == "📩 Murojat uchun")
async def menu_contact(msg: Message, state: FSMContext):
    await state.set_state(Contact.msg)
    await msg.answer("📩 Murojatingizni yozing:", reply_markup=kb_cancel())

@router.message(Contact.msg)
async def contact_send(msg: Message, state: FSMContext, bot: Bot):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_main()); return
    u = await get_user(msg.from_user.id); name = u['ism'] if u else "?"
    un = f"@{msg.from_user.username}" if msg.from_user.username else "yo'q"
    for aid in ADMIN_IDS:
        try: await bot.send_message(aid, f"📩 <b>Murojat</b>\n👤 {name} ({un})\n🆔 {msg.from_user.id}\n\n{msg.text}", parse_mode="HTML")
        except: pass
    await state.clear(); await msg.answer("✅ Murojat yuborildi!", reply_markup=kb_main())

@router.message(F.text == "👤 Profilim")
async def menu_profile(msg: Message):
    u = await get_user(msg.from_user.id)
    if not u: await msg.answer("❌"); return
    rank = await get_user_rank(msg.from_user.id)
    pr = "✅ Faol" if u['premium'] else "❌ Yo'q"
    pe = f"\n⏰ Tugash: {u['premium_tugash'].strftime('%d.%m.%Y')}" if u['premium'] and u['premium_tugash'] else ""
    t = (f"👤 <b>Profilim</b>\n━━━━━━━━━━━━━━━━━━━━\n"
         f"📛 Ism: <b>{u['ism'] or '—'}</b>\n🎂 Yosh: <b>{u['yosh'] or '—'}</b>\n"
         f"⚧ Jins: <b>{u['jins'] or '—'}</b>\n🎭 Qiziqishlar: <b>{u['qiziqishlar'] or '—'}</b>\n"
         f"💎 Premium: {pr}{pe}\n🏆 Ko'rish reytingi: <b>{rank}-o'rin</b>\n"
         f"👁 Ko'rgan: <b>{u['korgan_count'] or 0} ta</b>")
    await msg.answer(t, reply_markup=ik_profile(), parse_mode="HTML")

@router.callback_query(F.data == "ed_ism")
async def ed_ism_s(cb: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.ism)
    await cb.message.answer("📝 Yangi ismingizni kiriting:", reply_markup=kb_cancel())

@router.message(EditProfile.ism)
async def ed_ism(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_main()); return
    await update_user(msg.from_user.id, ism=msg.text.strip())
    await state.clear(); await msg.answer("✅ Ism yangilandi!", reply_markup=kb_main())

@router.callback_query(F.data == "ed_yosh")
async def ed_yosh_s(cb: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.yosh)
    await cb.message.answer("🎂 Yangi yoshingizni kiriting:", reply_markup=kb_cancel())

@router.message(EditProfile.yosh)
async def ed_yosh(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_main()); return
    try: y = int(msg.text.strip())
    except: await msg.answer("❌ Raqamda kiriting."); return
    await update_user(msg.from_user.id, yosh=y)
    await state.clear(); await msg.answer("✅ Yosh yangilandi!", reply_markup=kb_main())

@router.callback_query(F.data == "ed_jins")
async def ed_jins_s(cb: CallbackQuery):
    await cb.message.answer("⚧ Yangi jinsni tanlang:", reply_markup=ik_edit_gender())

@router.callback_query(F.data.in_(["eg_erkak","eg_ayol"]))
async def ed_jins(cb: CallbackQuery):
    j = "Erkak" if "erkak" in cb.data else "Ayol"
    await update_user(cb.from_user.id, jins=j)
    await cb.message.edit_text(f"✅ Jins yangilandi: <b>{j}</b>", parse_mode="HTML")
    await cb.message.answer("🏠 Bosh menyu", reply_markup=kb_main())

@router.callback_query(F.data == "ed_qiz")
async def ed_qiz_s(cb: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.qiziqish)
    await cb.message.answer("🎭 Yangi qiziqishlarni kiriting:", reply_markup=kb_cancel())

@router.message(EditProfile.qiziqish)
async def ed_qiz(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_main()); return
    await update_user(msg.from_user.id, qiziqishlar=msg.text.strip())
    await state.clear(); await msg.answer("✅ Qiziqishlar yangilandi!", reply_markup=kb_main())

@router.callback_query(F.data == "rtg_users")
async def cb_rtg_users(cb: CallbackQuery):
    top = await get_top_watchers(20); uid = cb.from_user.id; rank = await get_user_rank(uid)
    t = "🏆 <b>Eng ko'p ko'rganlar:</b>\n━━━━━━━━━━━━━━━━\n"
    for i,u in enumerate(top,1):
        m = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
        n = u['ism'] or u['username'] or "?"; me = " 👈 Siz" if u['telegram_id']==uid else ""
        t += f"{m} {n} — {u['korgan_count']} ta{me}\n"
    if rank > 20: t += f"\n📍 Sizning o'rningiz: <b>{rank}-o'rin</b>"
    await cb.message.edit_text(t, reply_markup=ik_back("back_main"), parse_mode="HTML")

@router.callback_query(F.data == "rtg_anime")
async def cb_rtg_anime(cb: CallbackQuery):
    top = await get_top_rating(20)
    t = "🌟 <b>Top animelar (baho):</b>\n━━━━━━━━━━━━━━━━\n"
    for i,a in enumerate(top,1):
        m = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
        t += f"{m} {a['nomi']} — ⭐{a['reyting']}/10\n"
    if not top: t += "Hali baho berilmagan."
    await cb.message.edit_text(t, reply_markup=ik_back("back_main"), parse_mode="HTML")

@router.callback_query(F.data == "back_search")
async def cb_back_search(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try: await cb.message.edit_text("🔍 <b>Anime izlash</b>", reply_markup=ik_search(), parse_mode="HTML")
    except: await cb.message.answer("🔍 <b>Anime izlash</b>", reply_markup=ik_search(), parse_mode="HTML")

@router.callback_query(F.data == "s_name")
async def s_name_s(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Search.name)
    await cb.message.edit_text("📝 Anime nomini kiriting:", reply_markup=ik_back("back_search"))

@router.message(Search.name)
async def s_name(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("🏠", reply_markup=kb_main()); return
    res = await search_anime_name(msg.text.strip()); await state.clear()
    if not res:
        b = InlineKeyboardBuilder(); b.button(text="🔙 Orqaga", callback_data="back_search")
        await msg.answer("❌ Anime topilmadi.", reply_markup=b.as_markup()); return
    if len(res)==1: await send_card(msg, res[0], msg.from_user.id); return
    b = InlineKeyboardBuilder()
    for a in res: b.button(text=f"🎬 {a['nomi']}", callback_data=f"ac_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="back_search"); b.adjust(1)
    await msg.answer(f"🔍 <b>{len(res)} ta topildi:</b>", reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "s_code")
async def s_code_s(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Search.code)
    await cb.message.edit_text("🔢 Anime kodini kiriting:", reply_markup=ik_back("back_search"))

@router.message(Search.code)
async def s_code(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("🏠", reply_markup=kb_main()); return
    a = await get_anime_by_code(msg.text.strip()); await state.clear()
    if not a:
        b = InlineKeyboardBuilder(); b.button(text="🔙 Orqaga", callback_data="back_search")
        await msg.answer("❌ Bu kodda anime topilmadi.", reply_markup=b.as_markup()); return
    await send_card(msg, a, msg.from_user.id)

# RASM ORQALI QIDIRISH
@router.callback_query(F.data == "s_image")
async def s_image_start(cb: CallbackQuery, state: FSMContext):
    if not await premium_wall(cb): return
    await state.set_state(Search.image)
    await cb.message.edit_text(
        "🖼 <b>Rasm orqali qidirish</b>\n\n"
        "Anime screenshotini yuboring.\n"
        "⚠️ Faqat anime sahnalari aniqlanadi (poster emas).",
        reply_markup=ik_back("back_search"), parse_mode="HTML")

@router.message(Search.image, F.photo)
async def s_image_search(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    wait_msg = await msg.answer("🔍 Qidirilyapti...")
    photo = msg.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    result = await search_anime_by_image(file_url)
    try: await wait_msg.delete()
    except: pass
    if not result:
        b = InlineKeyboardBuilder(); b.button(text="🔙 Orqaga", callback_data="back_search")
        await msg.answer(
            "❌ Anime aniqlanmadi.\n\nSabab: rasm sifati past yoki anime screenshoti emas.",
            reply_markup=b.as_markup()); return
    title = result['title']; similarity = result['similarity']
    res = await search_anime_name(title)
    if not res:
        b = InlineKeyboardBuilder(); b.button(text="🔙 Orqaga", callback_data="back_search")
        await msg.answer(
            f"🎬 trace.moe aniqladi: <b>{title}</b> ({similarity}%)\n\n"
            f"❌ Lekin bazamizda bu anime yo'q.",
            reply_markup=b.as_markup(), parse_mode="HTML"); return
    if len(res)==1:
        await msg.answer(f"✅ Aniqlandi: <b>{title}</b> ({similarity}%)", parse_mode="HTML")
        await send_card(msg, res[0], msg.from_user.id); return
    b = InlineKeyboardBuilder()
    for a in res: b.button(text=f"🎬 {a['nomi']}", callback_data=f"ac_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="back_search"); b.adjust(1)
    await msg.answer(
        f"✅ Aniqlandi: <b>{title}</b> ({similarity}%)\n🔍 {len(res)} ta natija:",
        reply_markup=b.as_markup(), parse_mode="HTML")

@router.message(Search.image)
async def s_image_wrong(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("🏠", reply_markup=kb_main()); return
    await msg.answer("📸 Iltimos rasm (screenshot) yuboring!")

@router.callback_query(F.data == "s_janryil")
async def s_janryil(cb: CallbackQuery, state: FSMContext):
    if not await premium_wall(cb): return
    await state.update_data(sel_janrlar=[], sel_yillar=[])
    await cb.message.edit_text("🎭 <b>Janrlarni tanlang:</b>", reply_markup=ik_janr_select([]), parse_mode="HTML")

@router.callback_query(F.data.startswith("seljanr_"))
async def cb_seljanr(cb: CallbackQuery, state: FSMContext):
    janr = cb.data.replace("seljanr_",""); d = await state.get_data()
    sel = d.get("sel_janrlar", [])
    if janr in sel: sel.remove(janr)
    else: sel.append(janr)
    await state.update_data(sel_janrlar=sel)
    try: await cb.message.edit_reply_markup(reply_markup=ik_janr_select(sel))
    except: pass
    await cb.answer(("✅ " if janr in sel else "❌ ") + janr)

@router.callback_query(F.data == "goto_yil_sel")
async def cb_goto_yil(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await cb.message.edit_text("📅 <b>Yillarni tanlang:</b>",
                                reply_markup=ik_yil_select(d.get("sel_yillar",[])), parse_mode="HTML")

@router.callback_query(F.data == "goto_janr_sel")
async def cb_goto_janr(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await cb.message.edit_text("🎭 <b>Janrlarni tanlang:</b>",
                                reply_markup=ik_janr_select(d.get("sel_janrlar",[])), parse_mode="HTML")

@router.callback_query(F.data.startswith("selyil_"))
async def cb_selyil(cb: CallbackQuery, state: FSMContext):
    yil = cb.data.replace("selyil_",""); d = await state.get_data()
    sel = d.get("sel_yillar", [])
    if yil in sel: sel.remove(yil)
    else: sel.append(yil)
    await state.update_data(sel_yillar=sel)
    try: await cb.message.edit_reply_markup(reply_markup=ik_yil_select(sel))
    except: pass
    await cb.answer(("✅ " if yil in sel else "❌ ") + yil)

@router.callback_query(F.data == "do_janryil_search")
async def cb_do_janryil_search(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    sel_janrlar = d.get("sel_janrlar", []); sel_yillar = d.get("sel_yillar", [])
    await state.clear()
    if not sel_janrlar and not sel_yillar:
        await cb.answer("❗ Kamida 1 ta janr yoki yil tanlang!", show_alert=True); return
    results = []
    combos = [(j,y) for j in (sel_janrlar or [None]) for y in (sel_yillar or [None])]
    for janr, yil in combos:
        res = await search_anime_genre_year(janr, yil)
        for r in res:
            if not any(x['id']==r['id'] for x in results): results.append(r)
    if not results:
        await cb.message.edit_text("❌ Hech narsa topilmadi.", reply_markup=ik_back("back_search")); return
    if len(results)==1:
        try: await cb.message.delete()
        except: pass
        await send_card(cb.message, results[0], cb.from_user.id); return
    b = InlineKeyboardBuilder()
    for a in results[:20]: b.button(text=f"🎬 {a['nomi']}", callback_data=f"ac_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="back_search"); b.adjust(1)
    await cb.message.edit_text(
        f"🔍 <b>{len(results)} ta topildi</b>\n🎭 {', '.join(sel_janrlar) or '—'}\n📅 {', '.join(sel_yillar) or '—'}",
        reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "s_random")
async def s_random(cb: CallbackQuery):
    a = await get_random_anime()
    if not a: await cb.answer("❌ Hali anime yo'q!", show_alert=True); return
    try: await cb.message.delete()
    except: pass
    await send_card(cb.message, a, cb.from_user.id)

@router.callback_query(F.data == "s_top")
async def s_top(cb: CallbackQuery):
    if not await premium_wall(cb): return
    top = await get_top_views(10)
    b = InlineKeyboardBuilder()
    for i,a in enumerate(top,1):
        b.button(text=f"{i}. 🎬 {a['nomi']} 👁{a['korish_soni']}", callback_data=f"ac_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="back_search"); b.adjust(1)
    await cb.message.edit_text("🔥 <b>Eng ko'p ko'rilgan:</b>", reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "s_recommend")
async def s_recommend(cb: CallbackQuery):
    u = await get_user(cb.from_user.id); janr = (u['qiziqishlar'] or "Aksyon") if u else "Aksyon"
    res = await get_recommended(janr, 5)
    if not res: res = await get_top_rating(5)
    b = InlineKeyboardBuilder()
    for a in res: b.button(text=f"🎬 {a['nomi']}", callback_data=f"ac_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="back_search"); b.adjust(1)
    await cb.message.edit_text(f"🌟 <b>Tavsiyalar ({janr}):</b>", reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("ac_"))
async def cb_anime_card(cb: CallbackQuery):
    aid = int(cb.data.split("_")[1]); a = await get_anime(aid)
    if not a: await cb.answer("❌ Topilmadi!", show_alert=True); return
    try: await cb.message.delete()
    except: pass
    await send_card(cb.message, a, cb.from_user.id)

@router.callback_query(F.data.startswith("watch_"))
async def cb_watch(cb: CallbackQuery):
    aid = int(cb.data.split("_")[1]); seasons = await get_seasons(aid)
    if not seasons: await cb.answer("❌ Hali fasl qo'shilmagan!", show_alert=True); return
    await inc_views(aid); u = await get_user(cb.from_user.id)
    await update_user(cb.from_user.id, korgan_count=(u['korgan_count'] or 0)+1)
    try: await cb.message.edit_reply_markup(reply_markup=ik_seasons(seasons, aid))
    except: await cb.message.answer("📂 Faslni tanlang:", reply_markup=ik_seasons(seasons, aid))

@router.callback_query(F.data.startswith("ssn_"))
async def cb_season(cb: CallbackQuery):
    parts = cb.data.split("_"); sid, aid = int(parts[1]), int(parts[2])
    eps = await get_episodes(sid)
    if not eps: await cb.answer("❌ Bu faslda qism yo'q!", show_alert=True); return
    admin = is_admin(cb.from_user.id)
    try: await cb.message.edit_reply_markup(reply_markup=ik_episodes(eps, sid, aid, admin))
    except: await cb.message.answer("📺 Qismni tanlang:", reply_markup=ik_episodes(eps, sid, aid, admin))

@router.callback_query(F.data.startswith("ep_"))
async def cb_episode(cb: CallbackQuery, bot: Bot):
    eid = int(cb.data.split("_")[1]); ep = await get_episode(eid)
    if not ep: await cb.answer("❌ Topilmadi!", show_alert=True); return
    await cb.answer()
    await bot.send_video(cb.from_user.id, video=ep['video_file_id'],
                         caption=f"▶️ {ep['qism_raqami']}-qism\n\n@Aniyoof")

@router.callback_query(F.data.startswith("allep_"))
async def cb_all_ep(cb: CallbackQuery, bot: Bot):
    sid = int(cb.data.split("_")[1]); eps = await get_episodes(sid)
    if not eps: await cb.answer("❌ Topilmadi!", show_alert=True); return
    await cb.answer(f"📦 {len(eps)} ta qism yuborilmoqda...", show_alert=True)
    for ep in eps:
        try:
            await bot.send_video(cb.from_user.id, video=ep['video_file_id'],
                                 caption=f"▶️ {ep['qism_raqami']}-qism | @Aniyoof")
            await asyncio.sleep(0.3)
        except: pass

@router.callback_query(F.data.startswith("deleplist_"))
async def cb_delep_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    _, sid, aid = cb.data.split("_"); sid, aid = int(sid), int(aid)
    eps = await get_episodes(sid)
    if not eps: await cb.answer("❌ Qism yo'q!", show_alert=True); return
    try: await cb.message.edit_reply_markup(reply_markup=ik_episodes_delete(eps, sid, aid))
    except: await cb.message.answer("🗑 Qismni tanlang:", reply_markup=ik_episodes_delete(eps, sid, aid))

@router.callback_query(F.data.startswith("delep_"))
async def cb_delep(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    parts = cb.data.split("_"); eid, sid, aid = int(parts[1]), int(parts[2]), int(parts[3])
    ep = await delete_episode(eid)
    if not ep: await cb.answer("❌ Topilmadi!", show_alert=True); return
    await cb.answer(f"✅ {ep['qism_raqami']}-qism o'chirildi!", show_alert=True)
    eps = await get_episodes(sid)
    if eps:
        try: await cb.message.edit_reply_markup(reply_markup=ik_episodes_delete(eps, sid, aid))
        except: pass
    else: await cb.message.answer("✅ Barcha qismlar o'chirildi.", reply_markup=ik_admin_action(aid))

@router.callback_query(F.data.startswith("fav_"))
async def cb_fav(cb: CallbackQuery):
    aid = int(cb.data.split("_")[1]); uid = cb.from_user.id
    f = await is_favorite(uid, aid)
    if f: await remove_favorite(uid, aid); await cb.answer("💔 Sevimlilardan olib tashlandi!")
    else: await add_favorite(uid, aid);    await cb.answer("❤️ Sevimlilarga qo'shildi!")
    wl = await is_in_watchlist(uid, aid); notif = await is_notif_on(uid, aid)
    try: await cb.message.edit_reply_markup(reply_markup=ik_anime_extra(aid, not f, wl, notif))
    except: pass

@router.callback_query(F.data.startswith("wl_"))
async def cb_wl(cb: CallbackQuery):
    aid = int(cb.data.split("_")[1]); uid = cb.from_user.id
    if not await is_premium(uid): await cb.answer("💎 Faqat Premium uchun!", show_alert=True); return
    w = await is_in_watchlist(uid, aid)
    if w: await remove_watchlist(uid, aid); await cb.answer("📋 Watch listdan olib tashlandi!")
    else: await add_watchlist(uid, aid);    await cb.answer("📋 Watch listga qo'shildi!")
    fav = await is_favorite(uid, aid); notif = await is_notif_on(uid, aid)
    try: await cb.message.edit_reply_markup(reply_markup=ik_anime_extra(aid, fav, not w, notif))
    except: pass

@router.callback_query(F.data.startswith("rate_"))
async def cb_rate_start(cb: CallbackQuery):
    aid = int(cb.data.split("_")[1]); ex = await get_user_rating(cb.from_user.id, aid)
    extra = f"\n\nSizning bahoyingiz: ⭐{ex['baho']}" if ex else ""
    await cb.message.answer(f"⭐ <b>Baholash</b>{extra}\n\nBaho bering (1-10):",
                             reply_markup=ik_rating(aid), parse_mode="HTML")

@router.callback_query(F.data.startswith("rt_"))
async def cb_rate_save(cb: CallbackQuery):
    _, aid, baho = cb.data.split("_")
    await add_rating(cb.from_user.id, int(aid), float(baho))
    await cb.answer(f"✅ Bahoyingiz {baho}/10 saqlandi!", show_alert=True)
    try: await cb.message.delete()
    except: pass

@router.callback_query(F.data.startswith("non_"))
async def cb_notif_on(cb: CallbackQuery):
    aid = int(cb.data.split("_")[1])
    if not await is_premium(cb.from_user.id): await cb.answer("💎 Faqat Premium uchun!", show_alert=True); return
    await add_notif(cb.from_user.id, aid); await cb.answer("🔔 Bildirishnoma yoqildi!")
    fav = await is_favorite(cb.from_user.id, aid); wl = await is_in_watchlist(cb.from_user.id, aid)
    try: await cb.message.edit_reply_markup(reply_markup=ik_anime_extra(aid, fav, wl, True))
    except: pass

@router.callback_query(F.data.startswith("noff_"))
async def cb_notif_off(cb: CallbackQuery):
    aid = int(cb.data.split("_")[1])
    await remove_notif(cb.from_user.id, aid); await cb.answer("🔕 Bildirishnoma o'chirildi!")
    fav = await is_favorite(cb.from_user.id, aid); wl = await is_in_watchlist(cb.from_user.id, aid)
    try: await cb.message.edit_reply_markup(reply_markup=ik_anime_extra(aid, fav, wl, False))
    except: pass

@router.callback_query(F.data.startswith("cmt_"))
async def cb_comments(cb: CallbackQuery):
    _, aid, offset = cb.data.split("_"); aid, offset = int(aid), int(offset)
    cmts = await get_comments(aid, 10, offset); total = await count_comments(aid); a = await get_anime(aid)
    t = f"💬 <b>{a['nomi']} — Izohlar</b>\n━━━━━━━━━━━━━━━━\n"
    if not cmts: t += "\nHali izoh yo'q. Birinchi bo'ling! 👇"
    else:
        for c in cmts:
            n = c['ism'] or c['username'] or "?"
            d = c['created_at'].strftime("%d.%m") if c['created_at'] else ""
            t += f"👤 <b>{n}</b> <i>{d}</i>\n{c['matn']}\n\n"
    await cb.message.answer(t, reply_markup=ik_comments(aid, offset, total), parse_mode="HTML")

@router.callback_query(F.data.startswith("wcmt_"))
async def cb_write_cmt(cb: CallbackQuery, state: FSMContext):
    aid = int(cb.data.split("_")[1])
    await state.update_data(cmt_aid=aid); await state.set_state(CommentW.write)
    await cb.message.answer("✍️ Izohingizni yozing:", reply_markup=kb_cancel())

@router.message(CommentW.write)
async def save_comment(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_main()); return
    d = await state.get_data()
    await add_comment(msg.from_user.id, d['cmt_aid'], msg.text.strip())
    await state.clear(); await msg.answer("✅ Izoh qo'shildi!", reply_markup=kb_main())

@router.callback_query(F.data == "pr_menu")
async def cb_pr_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    txt = ("💎 <b>Aniyoof Premium</b>\n\n"
           "✅ Janr/Yil bilan qidirish\n"
           "✅ Rasm orqali qidirish\n"
           "✅ Eng ko'p ko'rilgan\n"
           "✅ Watch list\n"
           "✅ Bildirishnomalar\n\nTanlang:")
    try: await cb.message.edit_text(txt, reply_markup=ik_premium_menu(), parse_mode="HTML")
    except: await cb.message.answer(txt, reply_markup=ik_premium_menu(), parse_mode="HTML")

@router.callback_query(F.data == "pr_bot")
async def cb_pr_bot(cb: CallbackQuery):
    await cb.message.edit_text("💰 <b>Tarifni tanlang:</b>", reply_markup=ik_tarif(), parse_mode="HTML")

@router.callback_query(F.data == "pr_admin")
async def cb_pr_admin(cb: CallbackQuery):
    await cb.message.edit_text("👤 <b>Admin orqali olish:</b>", reply_markup=ik_admins(), parse_mode="HTML")

@router.callback_query(F.data.startswith("tarif_"))
async def cb_tarif(cb: CallbackQuery, state: FSMContext):
    tarif = cb.data.split("_")[1]
    await state.update_data(tarif=tarif); await state.set_state(PremiumPay.screenshot)
    await cb.message.edit_text(
        f"💳 <b>To'lov:</b>\n\n💳 Karta: <code>{PAYMENT_CARD}</code>\n"
        f"💰 Miqdor: <b>{tarif_name(tarif)}</b>\n\nChek screenshot yuboring 👇",
        reply_markup=ik_pr_cancel(), parse_mode="HTML")

@router.message(PremiumPay.screenshot, F.photo)
async def pr_screenshot(msg: Message, state: FSMContext, bot: Bot):
    d = await state.get_data(); tarif = d.get('tarif','1oy')
    sid = msg.photo[-1].file_id; uid = msg.from_user.id
    req = await create_premium_req(uid, tarif, sid)
    u = await get_user(uid); name = u['ism'] if u else "?"
    un = f"@{msg.from_user.username}" if msg.from_user.username else "yo'q"
    await state.clear()
    await msg.answer("✅ <b>Ariza qabul qilindi!</b>\n⏰ 1-24 soat ichida tekshiriladi.",
                     reply_markup=kb_main(), parse_mode="HTML")
    for adm_id in ADMIN_IDS:
        try:
            await bot.send_photo(adm_id, photo=sid,
                caption=f"💳 <b>Premium ariza!</b>\n👤 {name}\n🆔 {un}\n📱 ID: {uid}\n"
                        f"📦 {tarif_name(tarif)}\n🔑 ID: {req['id']}",
                reply_markup=ik_admin_pr_req(req['id'], uid), parse_mode="HTML")
        except: pass

@router.message(PremiumPay.screenshot)
async def pr_screenshot_wrong(msg: Message, state: FSMContext):
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_main()); return
    await msg.answer("📸 Screenshot rasm yuboring.")

@router.callback_query(F.data.startswith("apr_"))
async def cb_approve_pr(cb: CallbackQuery, bot: Bot):
    _, rid, uid = cb.data.split("_"); rid, uid = int(rid), int(uid)
    req = await get_premium_req(rid)
    if not req: await cb.answer("❌ Topilmadi!", show_alert=True); return
    pe = premium_end_date(req['tarif'])
    await update_user(uid, premium=True, premium_tugash=pe)
    await update_premium_req(rid, "tasdiqlandi")
    try: await cb.message.edit_caption(cb.message.caption + "\n\n✅ TASDIQLANDI", parse_mode="HTML")
    except: pass
    try:
        await bot.send_message(uid,
            f"🎉 <b>Premium faollashtirildi!</b>\n📦 {tarif_name(req['tarif'])}\n"
            f"⏰ {pe.strftime('%d.%m.%Y')} gacha\n💎 Rohatlaning!", parse_mode="HTML")
    except: pass
    await cb.answer("✅ Premium berildi!")

@router.callback_query(F.data.startswith("rpr_"))
async def cb_reject_pr(cb: CallbackQuery, bot: Bot):
    _, rid, uid = cb.data.split("_"); rid, uid = int(rid), int(uid)
    await update_premium_req(rid, "rad etildi")
    try: await cb.message.edit_caption(cb.message.caption + "\n\n❌ RAD ETILDI", parse_mode="HTML")
    except: pass
    try: await bot.send_message(uid, "❌ <b>Premium arizangiz rad etildi.</b>", parse_mode="HTML")
    except: pass
    await cb.answer("❌ Rad etildi!")

@router.message(Command("admin"))
async def cmd_admin(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    await msg.answer("👑 Admin paneli:", reply_markup=kb_admin())

@router.message(F.text == "👤 User paneliga o'tish")
async def switch_user(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    await msg.answer("👤 User paneliga o'tdingiz!", reply_markup=kb_main())

@router.callback_query(F.data == "adm_back")
async def cb_adm_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try: await cb.message.delete()
    except: pass
    await cb.message.answer("👑 Admin paneli:", reply_markup=kb_admin())

@router.message(F.text == "✏️ Anime tahrirlash")
async def adm_edit_anime_list(msg: Message):
    if not is_admin(msg.from_user.id): return
    animes = await get_all_animes()
    if not animes: await msg.answer("❌ Hali anime yo'q!", reply_markup=kb_admin()); return
    b = InlineKeyboardBuilder()
    for a in animes: b.button(text=f"🎬 {a['kodi']} — {a['nomi']}", callback_data=f"editanim_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="adm_back"); b.adjust(1)
    await msg.answer("✏️ <b>Qaysi animeni tahrirlaysiz?</b>", reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("editanim_"))
async def cb_editanim(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    aid = int(cb.data.split("_")[1]); a = await get_anime(aid)
    if not a: await cb.answer("❌", show_alert=True); return
    try: await cb.message.edit_text(edit_txt(a), reply_markup=ik_anime_edit_fields(aid), parse_mode="HTML")
    except: await cb.message.answer(edit_txt(a), reply_markup=ik_anime_edit_fields(aid), parse_mode="HTML")

@router.callback_query(F.data.startswith("efield_"))
async def cb_efield(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    parts = cb.data.split("_"); field = parts[1]; aid = int(parts[2])
    await state.update_data(edit_aid=aid, edit_field=field)
    if field == "holati":
        await cb.message.edit_text("🔄 Holatni tanlang:", reply_markup=ik_holati_select(aid)); return
    if field == "yosh":
        await cb.message.edit_text("🔞 Yosh chegarasini tanlang:", reply_markup=ik_yosh_select(aid)); return
    if field == "media":
        await state.set_state(EditAnime.media)
        await cb.message.edit_text("🖼 Yangi rasm yoki video yuboring:", reply_markup=ik_back(f"editanim_{aid}")); return
    names = {"nomi":"📛 Nomi","kodi":"📁 Kodi","janr":"🎭 Janri","yil":"📅 Yili",
             "fasllar":"🗂 Fasllar soni","qismlar":"📺 Qismlar soni","tavsif":"📝 Tavsif"}
    await state.set_state(EditAnime.value)
    await cb.message.edit_text(f"✏️ <b>{names.get(field,field)}</b> uchun yangi qiymat yozing:",
                                reply_markup=ik_back(f"editanim_{aid}"), parse_mode="HTML")

@router.callback_query(F.data.startswith("setholati_"))
async def cb_setholati(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    parts = cb.data.split("_"); aid = int(parts[2])
    holati = "Davom etmoqda" if parts[1] == "davom" else "Tugallangan"
    async with pool.acquire() as c:
        await c.execute("UPDATE animes SET holati=$1 WHERE id=$2", holati, aid)
    await cb.answer(f"✅ Holat: {holati}")
    a = await get_anime(aid)
    try: await cb.message.edit_text(edit_txt(a), reply_markup=ik_anime_edit_fields(aid), parse_mode="HTML")
    except: pass

@router.callback_query(F.data.startswith("setyosh_"))
async def cb_setyosh(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    parts = cb.data.split("_"); aid = int(parts[2])
    yosh_val = parts[1].replace("plus", "+")
    async with pool.acquire() as c:
        await c.execute("UPDATE animes SET yosh_chegarasi=$1 WHERE id=$2", yosh_val, aid)
    await cb.answer(f"✅ Yosh: {yosh_val}")
    a = await get_anime(aid)
    try: await cb.message.edit_text(edit_txt(a), reply_markup=ik_anime_edit_fields(aid), parse_mode="HTML")
    except: pass

@router.message(EditAnime.value)
async def edit_anime_value(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text == "❌ Bekor qilish": await state.clear(); await msg.answer("❌", reply_markup=kb_admin()); return
    d = await state.get_data(); aid = d['edit_aid']; field = d['edit_field']
    val = msg.text.strip()
    db_field = {"nomi":"nomi","kodi":"kodi","janr":"janr","yil":"yil",
                "fasllar":"fasllar_soni","qismlar":"qismlar_soni","tavsif":"tavsif"}.get(field)
    if not db_field: await state.clear(); await msg.answer("❌ Xato!", reply_markup=kb_admin()); return
    try:
        if field in ("yil","fasllar","qismlar"): val = int(val)
        async with pool.acquire() as c:
            await c.execute(f"UPDATE animes SET {db_field}=$1 WHERE id=$2", val, aid)
        await state.clear(); a = await get_anime(aid)
        await msg.answer("✅ <b>Yangilandi!</b>\n\n" + edit_txt(a),
                         reply_markup=ik_anime_edit_fields(aid), parse_mode="HTML")
    except: await msg.answer("❌ Xato! Raqam kerak bo'lsa raqam kiriting.")

@router.message(EditAnime.media, F.photo | F.video)
async def edit_anime_media(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    d = await state.get_data(); aid = d['edit_aid']
    fid = msg.photo[-1].file_id if msg.photo else msg.video.file_id
    mt  = "photo" if msg.photo else "video"
    async with pool.acquire() as c:
        await c.execute("UPDATE animes SET media_file_id=$1, media_type=$2 WHERE id=$3", fid, mt, aid)
    await state.clear(); a = await get_anime(aid)
    await msg.answer(f"✅ <b>{a['nomi']}</b> rasmi/videosi yangilandi!",
                     reply_markup=ik_anime_edit_fields(aid), parse_mode="HTML")

@router.message(EditAnime.media)
async def edit_anime_media_wrong(msg: Message):
    if msg.text == "❌ Bekor qilish": return
    await msg.answer("🖼 Rasm yoki video yuboring!")

@router.callback_query(F.data.startswith("delanime_"))
async def cb_delanime_confirm(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    aid = int(cb.data.split("_")[1]); a = await get_anime(aid)
    if not a: await cb.answer("❌ Topilmadi!", show_alert=True); return
    try:
        await cb.message.edit_text(
            f"⚠️ <b>{a['nomi']}</b> animesini o'chirishni tasdiqlaysizmi?\n\n"
            f"Barcha fasl, qism va ma'lumotlar o'chib ketadi!",
            reply_markup=ik_confirm_del_anime(aid), parse_mode="HTML")
    except:
        await cb.message.answer(
            f"⚠️ <b>{a['nomi']}</b> — o'chirishni tasdiqlaysizmi?",
            reply_markup=ik_confirm_del_anime(aid), parse_mode="HTML")

@router.callback_query(F.data.startswith("confirmdel_"))
async def cb_confirmdel(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    aid = int(cb.data.split("_")[1]); a = await get_anime(aid); nomi = a['nomi'] if a else "?"
    await delete_anime(aid); await cb.answer(f"✅ {nomi} o'chirildi!", show_alert=True)
    try: await cb.message.delete()
    except: pass
    await cb.message.answer("✅ Anime o'chirildi!", reply_markup=kb_admin())

@router.message(F.text == "✂️ Qism o'chirish")
async def adm_del_ep_start(msg: Message):
    if not is_admin(msg.from_user.id): return
    animes = await get_all_animes()
    if not animes: await msg.answer("❌ Hali anime yo'q!", reply_markup=kb_admin()); return
    b = InlineKeyboardBuilder()
    for a in animes: b.button(text=f"🎬 {a['kodi']} — {a['nomi']}", callback_data=f"epdelanim_{a['id']}")
    b.button(text="🔙 Orqaga", callback_data="adm_back"); b.adjust(1)
    await msg.answer("✂️ <b>Qaysi animening qismini o'chirmoqchisiz?</b>",
                     reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("epdelanim_"))
async def cb_epdelanim(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    aid = int(cb.data.split("_")[1]); sns = await get_seasons(aid)
    if not sns: await cb.answer("❌ Bu animeda fasl yo'q!", show_alert=True); return
    b = InlineKeyboardBuilder()
    for s in sns: b.button(text=f"📂 {s['fasl_nomi']}", callback_data=f"epdelssn_{s['id']}_{aid}")
    b.button(text="🔙 Orqaga", callback_data="adm_back"); b.adjust(1)
    try: await cb.message.edit_text("📂 <b>Faslni tanlang:</b>", reply_markup=b.as_markup(), parse_mode="HTML")
    except: await cb.message.answer("📂 <b>Faslni tanlang:</b>", reply_markup=b.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("epdelssn_"))
async def cb_epdelssn(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    parts = cb.data.split("_"); sid, aid = int(parts[1]), int(parts[2])
    eps = await get_episodes(sid)
    if not eps: await cb.answer("❌ Bu faslda qism yo'q!", show_alert=True); return
    try: await cb.message.edit_text("✂️ <b>Qismni tanlang:</b>",
                                     reply_markup=ik_episodes_delete(eps, sid, aid), parse_mode="HTML")
    except: await cb.message.answer("✂️ <b>Qismni tanlang:</b>",
                                      reply_markup=ik_episodes_delete(eps, sid, aid), parse_mode="HTML")

@router.message(F.text == "➕ Anime qo'shish")
async def adm_add_anime(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    await msg.answer("➕ <b>Anime qo'shish</b>", reply_markup=ik_admin_add(), parse_mode="HTML")

@router.callback_query(F.data == "adm_new")
async def adm_new_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await state.set_state(AddAnime.info)
    await cb.message.edit_text(f"📝 <b>Yangi anime:</b>\n\n{INFO_FMT}",
                                parse_mode="HTML", reply_markup=ik_back("adm_back"))

@router.message(AddAnime.info)
async def adm_anime_info(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text in ["❌ Bekor qilish","/admin"]:
        await state.clear(); await msg.answer("❌", reply_markup=kb_admin()); return
    try:
        d = {}
        for line in msg.text.strip().split('\n'):
            if ':' in line:
                k,v = line.split(':',1); d[k.strip().lower()] = v.strip()
        nomi=d['nomi']; kodi=d['kod']; janr=d['janr']; yil=int(d['yil'])
        fasllar=int(d.get('fasllar',1)); qismlar=int(d.get('qismlar',0))
        holati=d.get('holati','Davom etmoqda'); tavsif=d.get('tavsif','')
        yosh=d.get('yosh','Belgilanmagan')
        if await get_anime_by_code(kodi):
            await msg.answer(f"❌ <b>{kodi}</b> kodi allaqachon mavjud!", parse_mode="HTML"); return
        await state.update_data(nomi=nomi,kodi=kodi,janr=janr,yil=yil,
                                fasllar=fasllar,qismlar=qismlar,holati=holati,tavsif=tavsif,yosh=yosh)
        await state.set_state(AddAnime.media)
        await msg.answer("🖼 <b>Rasm yoki video yuboring:</b>", parse_mode="HTML", reply_markup=ik_back("adm_back"))
    except: await msg.answer(f"❌ Xato format!\n\n{INFO_FMT}")

@router.message(AddAnime.media, F.photo | F.video)
async def adm_anime_media(msg: Message, state: FSMContext, bot: Bot):
    if not is_admin(msg.from_user.id): return
    d = await state.get_data()
    fid = msg.photo[-1].file_id if msg.photo else msg.video.file_id
    mt  = "photo" if msg.photo else "video"
    a = await create_anime(d['nomi'],d['kodi'],d['janr'],d['yil'],d['fasllar'],
                           d['qismlar'],d['holati'],fid,mt,d.get('tavsif',''),d.get('yosh','Belgilanmagan'))
    await state.clear()
    await msg.answer(f"✅ <b>{d['nomi']}</b> qo'shildi!", reply_markup=ik_admin_action(a['id']), parse_mode="HTML")
    e = "✅" if d['holati']=="Tugallangan" else "🔄"
    ch_txt = (f"🎌 <b>Yangi anime!</b>\n\n🎬 <b>{d['nomi']}</b>\n📁 Kod: <code>{d['kodi']}</code>\n"
              f"🎭 {d['janr']}\n📅 {d['yil']}\n🗂 {d['fasllar']} fasl | 📺 {d['qismlar']} qism\n"
              f"{e} {d['holati']}\n\n@Aniyoof")
    try:
        if mt=='video': await bot.send_video(CHANNEL_ID, video=fid, caption=ch_txt,
                                              reply_markup=ik_anime_watch_channel(a['id']), parse_mode="HTML")
        else:           await bot.send_photo(CHANNEL_ID, photo=fid, caption=ch_txt,
                                              reply_markup=ik_anime_watch_channel(a['id']), parse_mode="HTML")
    except Exception as ex: await msg.answer(f"⚠️ Kanalga post xato: {ex}")
    premium_users = await get_premium_users(); count = 0
    for u in premium_users:
        try:
            await bot.send_message(u['telegram_id'],
                f"🔔 <b>Yangi anime!</b>\n\n🎬 <b>{d['nomi']}</b>\n🎭 {d['janr']} | 📅 {d['yil']}\n\nBotda ko'ring!",
                parse_mode="HTML")
            count += 1; await asyncio.sleep(0.05)
        except: pass
    if count > 0: await msg.answer(f"📢 {count} ta premium foydalanuvchiga xabar yuborildi!")

@router.message(AddAnime.media)
async def adm_anime_media_wrong(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text in ["❌ Bekor qilish","/admin"]:
        await state.clear(); await msg.answer("❌", reply_markup=kb_admin()); return
    await msg.answer("🖼 Rasm yoki video yuboring!")

@router.callback_query(F.data == "adm_cont")
async def adm_cont(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    animes = await get_all_animes()
    if not animes: await cb.answer("❌ Hali anime yo'q!", show_alert=True); return
    await cb.message.edit_text("📝 Animeni tanlang:", reply_markup=ik_admin_list(animes))

@router.callback_query(F.data == "adm_alist")
async def adm_alist(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    animes = await get_all_animes()
    try: await cb.message.edit_text("📝 Animeni tanlang:", reply_markup=ik_admin_list(animes))
    except: await cb.message.answer("📝 Animeni tanlang:", reply_markup=ik_admin_list(animes))

@router.callback_query(F.data.startswith("aa_"))
async def cb_aa(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    aid = int(cb.data.split("_")[1]); a = await get_anime(aid)
    if not a: await cb.answer("❌", show_alert=True); return
    txt = f"🎬 <b>{a['nomi']}</b>\n📁 {a['kodi']}\n\nNima qo'shmoqchisiz?"
    try: await cb.message.edit_text(txt, reply_markup=ik_admin_action(aid), parse_mode="HTML")
    except: await cb.message.answer(txt, reply_markup=ik_admin_action(aid), parse_mode="HTML")

@router.callback_query(F.data.startswith("addsn_"))
async def addsn_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    aid = int(cb.data.split("_")[1])
    await state.update_data(sn_aid=aid); await state.set_state(AddSeason.name)
    await cb.message.edit_text("📂 Fasl nomini kiriting (masalan: 1-Fasl):", reply_markup=ik_back(f"aa_{aid}"))

@router.message(AddSeason.name)
async def addsn_save(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text in ["❌ Bekor qilish","/admin"]:
        await state.clear(); await msg.answer("❌", reply_markup=kb_admin()); return
    d = await state.get_data(); aid = d['sn_aid']
    sns = await get_seasons(aid)
    await create_season(aid, msg.text.strip(), len(sns)+1); await state.clear()
    await msg.answer(f"✅ <b>{msg.text.strip()}</b> fasl qo'shildi!",
                     reply_markup=ik_admin_action(aid), parse_mode="HTML")

@router.callback_query(F.data.startswith("addep_"))
async def addep_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    aid = int(cb.data.split("_")[1]); sns = await get_seasons(aid)
    if not sns: await cb.answer("❌ Avval fasl qo'shing!", show_alert=True); return
    await state.update_data(ep_aid=aid); await state.set_state(AddEpisode.sel_season)
    await cb.message.edit_text("📺 Qaysi faslga qism qo'shmoqchisiz?", reply_markup=ik_seasons_ep(sns, aid))

@router.callback_query(F.data.startswith("sel_sn_"))
async def sel_sn(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    parts = cb.data.split("_"); sid, aid = int(parts[2]), int(parts[3])
    await state.update_data(ep_sid=sid, ep_aid=aid); await state.set_state(AddEpisode.video)
    next_qism = await get_next_episode_number(sid)
    await cb.message.edit_text(
        f"🎬 <b>Video fayllarni yuboring</b>\n\n"
        f"Keyingi bo'sh qism: <b>{next_qism}-qism</b>\n"
        f"✅ Bir nechta video yuboring — har biri alohida saqlanadi.\n"
        f"⚠️ Videolar ketma-ket yuborilishi kerak (media group emas).\n"
        f"⏹ Tugatish: /admin",
        parse_mode="HTML", reply_markup=ik_back(f"addep_{aid}"))

@router.message(AddEpisode.video, F.video)
async def addep_video(msg: Message, state: FSMContext, bot: Bot):
    if not is_admin(msg.from_user.id): return
    d = await state.get_data()
    sid = d['ep_sid']; aid = d['ep_aid']
    uid = msg.from_user.id
    mg_id = msg.media_group_id

    if mg_id:
        # Media group — buffer ga yig'amiz
        key = f"{uid}_{mg_id}"
        if key not in _mg_buffer:
            _mg_buffer[key] = {"fids": [], "sid": sid, "aid": aid}
        _mg_buffer[key]["fids"].append(msg.video.file_id)

        # Avvalgi timer bo'lsa bekor qilamiz
        if key in _mg_timers:
            _mg_timers[key].cancel()

        # 2 soniya kutib, bufferdan barchasini saqlaymiz
        async def flush_group():
            await asyncio.sleep(2.0)
            buf = _mg_buffer.pop(key, None)
            _mg_timers.pop(key, None)
            if not buf: return
            fids = buf["fids"]; s_id = buf["sid"]; a_id = buf["aid"]

            if s_id not in _episode_locks:
                _episode_locks[s_id] = asyncio.Lock()

            saved = []
            async with _episode_locks[s_id]:
                for fid in fids:
                    qn = await get_next_episode_number(s_id)
                    await create_episode(s_id, a_id, qn, fid, f"{qn}-qism")
                    saved.append(qn)

            if saved:
                qism_txt = f"{saved[0]}-{saved[-1]}" if len(saved) > 1 else f"{saved[0]}"
                next_qn = await get_next_episode_number(s_id)
                try:
                    await bot.send_message(uid,
                        f"✅ <b>{qism_txt}-qismlar qo'shildi!</b> ({len(saved)} ta)\n"
                        f"📤 Keyingi: <b>{next_qn}-qism</b> uchun video yuboring yoki /admin bosing.",
                        parse_mode="HTML")
                except: pass
                # Bildirishnoma
                subs = await get_notif_subs(a_id); a = await get_anime(a_id)
                if subs and a:
                    for sub in subs:
                        try:
                            await bot.send_message(sub['telegram_id'],
                                f"🔔 <b>Yangi qismlar!</b>\n🎬 {a['nomi']}\n"
                                f"▶️ {qism_txt}-qismlar qo'shildi!\n\nBotga kiring 🎌",
                                parse_mode="HTML")
                        except: pass

        task = asyncio.create_task(flush_group())
        _mg_timers[key] = task

    else:
        # Oddiy bitta video
        if sid not in _episode_locks:
            _episode_locks[sid] = asyncio.Lock()
        async with _episode_locks[sid]:
            qn = await get_next_episode_number(sid)
            await create_episode(sid, aid, qn, msg.video.file_id, f"{qn}-qism")

        next_qn = await get_next_episode_number(sid)
        await msg.answer(
            f"✅ <b>{qn}-qism</b> qo'shildi!\n"
            f"📤 Keyingi: <b>{next_qn}-qism</b> uchun video yuboring yoki /admin bosing.",
            parse_mode="HTML")
        subs = await get_notif_subs(aid); a = await get_anime(aid)
        if subs and a:
            for sub in subs:
                try:
                    await bot.send_message(sub['telegram_id'],
                        f"🔔 <b>Yangi qism!</b>\n🎬 {a['nomi']}\n▶️ {qn}-qism qo'shildi!\n\nBotga kiring 🎌",
                        parse_mode="HTML")
                except: pass

@router.message(AddEpisode.video)
async def addep_video_wrong(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text in ["❌ Bekor qilish","/admin"]:
        d = await state.get_data(); aid = d.get('ep_aid', 0); await state.clear()
        await msg.answer("❌ Qism qo'shish to'xtatildi.",
                         reply_markup=ik_admin_action(aid) if aid else kb_admin()); return
    await msg.answer("🎬 Video fayl yuboring! Yoki /admin bosing.")

@router.message(F.text == "💎 Premium berish")
async def adm_pr_start(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear(); await state.set_state(AdminPremium.find)
    await msg.answer("💎 <b>Premium berish</b>\n\nUsername (@username) yoki raqam kiriting:",
                     parse_mode="HTML", reply_markup=kb_cancel())

@router.message(AdminPremium.find)
async def adm_pr_find(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text in ["❌ Bekor qilish","/admin"]:
        await state.clear(); await msg.answer("❌", reply_markup=kb_admin()); return
    q = msg.text.strip()
    u = await get_user_by_username(q) if q.startswith("@") or not q.startswith("+") else await get_user_by_phone(q)
    if not u: u = await get_user_by_username(q)
    if not u: await msg.answer("❌ Foydalanuvchi topilmadi!"); return
    await state.update_data(pr_uid=u['telegram_id'], pr_ism=u['ism'])
    await state.set_state(AdminPremium.tarif)
    pr = "✅ Faol" if u['premium'] else "❌ Yo'q"
    await msg.answer(f"👤 <b>Topildi!</b>\n📛 {u['ism']}\n🆔 @{u['username'] or '—'}\n💎 {pr}\n\nMuddat tanlang:",
                     reply_markup=ik_admin_tarif(), parse_mode="HTML")

@router.callback_query(F.data.in_(["gv_1oy","gv_3oy","gv_1yil"]))
async def adm_give_pr(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(cb.from_user.id): return
    d = await state.get_data(); uid = d.get('pr_uid'); ism = d.get('pr_ism','?')
    if not uid: await cb.answer("❌ Xato!", show_alert=True); return
    tarif = cb.data.replace("gv_",""); pe = premium_end_date(tarif)
    await update_user(uid, premium=True, premium_tugash=pe); await state.clear()
    await cb.message.edit_text(f"✅ <b>Premium berildi!</b>\n👤 {ism}\n📦 {tarif_name(tarif)}\n⏰ {pe.strftime('%d.%m.%Y')}",
                                parse_mode="HTML")
    await cb.message.answer("👑 Admin paneli:", reply_markup=kb_admin())
    try:
        await bot.send_message(uid,
            f"🎉 <b>Premium faollashtirildi!</b>\n📦 {tarif_name(tarif)}\n⏰ {pe.strftime('%d.%m.%Y')} gacha\n💎 Rohatlaning!",
            parse_mode="HTML")
    except: pass

@router.message(F.text == "📊 Statistika")
async def adm_stats(msg: Message):
    if not is_admin(msg.from_user.id): return
    s = await get_stats(); ta = await get_top_views(5); tu = await get_top_watchers(5)
    ta_t = "".join(f"{i}. {a['nomi']} — 👁{a['korish_soni']}\n" for i,a in enumerate(ta,1)) or "Yo'q"
    tu_t = "".join(f"{i}. {u['ism'] or u['username'] or '?'} — {u['korgan_count']} ta\n" for i,u in enumerate(tu,1)) or "Yo'q"
    await msg.answer(
        f"📊 <b>Statistika</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"👥 Jami users: <b>{s['total_users']}</b>\n💎 Premium: <b>{s['premium_users']}</b>\n"
        f"🆕 Bugun: <b>{s['today_users']}</b>\n🎬 Animelar: <b>{s['total_animes']}</b>\n"
        f"👁 Ko'rishlar: <b>{s['total_views']:,}</b>\n\n"
        f"🔥 <b>Top 5 anime:</b>\n{ta_t}\n🏆 <b>Top 5 user:</b>\n{tu_t}",
        reply_markup=kb_admin(), parse_mode="HTML")

@router.message(F.text == "📢 Xabar yuborish")
async def adm_bc_start(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear(); await state.set_state(Broadcast.msg)
    await msg.answer("📢 Xabarni yozing (/admin — bekor qilish):", reply_markup=kb_admin())

@router.message(Broadcast.msg)
async def adm_bc_msg(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text in ["❌ Bekor qilish","/admin"]:
        await state.clear(); await msg.answer("❌", reply_markup=kb_admin()); return
    md = {'text':msg.text, 'photo':msg.photo[-1].file_id if msg.photo else None,
          'video':msg.video.file_id if msg.video else None, 'caption':msg.caption}
    await state.update_data(bc_msg=md); await state.set_state(Broadcast.target)
    await msg.answer("👥 <b>Kimga yubormoqchisiz?</b>", reply_markup=ik_bc_target(), parse_mode="HTML")

@router.callback_query(F.data.in_(["bc_all","bc_premium","bc_free"]))
async def adm_bc_target(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    target = cb.data; await state.update_data(bc_target=target); await state.set_state(Broadcast.confirm)
    if target=="bc_all": users=await get_all_users(); txt="👥 Barcha userlar"
    elif target=="bc_premium": users=await get_premium_users(); txt="💎 Premium userlar"
    else: users=await get_non_premium_users(); txt="👤 Oddiy userlar"
    d = await state.get_data(); md = d.get('bc_msg',{})
    await cb.message.edit_text(
        f"👁 <b>Ko'rinish:</b>\n\n{md.get('text') or md.get('caption') or '[Media]'}\n\n"
        f"📊 {txt} — {len(users)} ta\n\nTasdiqlaysizmi?",
        reply_markup=ik_confirm_bc(), parse_mode="HTML")

@router.callback_query(F.data == "bc_yes")
async def adm_bc_do(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(cb.from_user.id): return
    d = await state.get_data(); md = d.get('bc_msg',{}); target = d.get('bc_target','bc_free')
    await state.clear()
    if target=="bc_all": users=await get_all_users()
    elif target=="bc_premium": users=await get_premium_users()
    else: users=await get_non_premium_users()
    total=len(users); sent=0; failed=0
    await cb.message.edit_text(f"📤 Yuborilmoqda... 0/{total}")
    for i,u in enumerate(users):
        try:
            if md.get('photo'): await bot.send_photo(u['telegram_id'], photo=md['photo'], caption=md.get('caption',''))
            elif md.get('video'): await bot.send_video(u['telegram_id'], video=md['video'], caption=md.get('caption',''))
            else: await bot.send_message(u['telegram_id'], md.get('text',''))
            sent += 1
        except Exception as ex:
            if 'blocked' in str(ex).lower() or 'deactivated' in str(ex).lower():
                await update_user(u['telegram_id'], is_blocked=True)
            failed += 1
        if (i+1)%50==0:
            try: await cb.message.edit_text(f"📤 Yuborilmoqda... {i+1}/{total}")
            except: pass
        await asyncio.sleep(0.05)
    await cb.message.edit_text(
        f"✅ <b>Xabar yuborildi!</b>\n✅ Muvaffaqiyatli: {sent}\n❌ Yuborilmadi: {failed}\n📊 Jami: {total}",
        parse_mode="HTML")

@router.callback_query(F.data == "bc_no")
async def adm_bc_no(cb: CallbackQuery, state: FSMContext):
    await state.clear(); await cb.message.edit_text("❌ Bekor qilindi.")
    await cb.message.answer("👑 Admin paneli:", reply_markup=kb_admin())

@router.message(F.text == "📝 Post yaratish")
async def adm_create_post(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear(); await state.set_state(CreatePost.media)
    await msg.answer("📝 <b>Post yaratish</b>\n\n1️⃣ Rasm yoki video yuboring:",
                     parse_mode="HTML", reply_markup=kb_cancel())

@router.message(CreatePost.media, F.photo | F.video)
async def post_media(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    fid = msg.photo[-1].file_id if msg.photo else msg.video.file_id
    mt  = "photo" if msg.photo else "video"
    await state.update_data(post_fid=fid, post_mt=mt); await state.set_state(CreatePost.caption)
    await msg.answer("2️⃣ Post uchun <b>matn</b> yozing:", parse_mode="HTML", reply_markup=kb_cancel())

@router.message(CreatePost.media)
async def post_media_wrong(msg: Message):
    if msg.text == "❌ Bekor qilish": return
    await msg.answer("📸 Rasm yoki video yuboring!")

@router.message(CreatePost.caption)
async def post_caption(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text == "❌ Bekor qilish":
        await state.clear(); await msg.answer("❌", reply_markup=kb_admin()); return
    await state.update_data(post_caption=msg.text.strip()); await state.set_state(CreatePost.anime_sel)
    animes = await get_all_animes()
    if not animes: await msg.answer("❌ Hali anime yo'q!", reply_markup=kb_admin()); await state.clear(); return
    await msg.answer("3️⃣ Tugmani bosganda qaysi anime ochilsin?", reply_markup=ik_post_anime_list(animes))

@router.callback_query(F.data.startswith("posta_"), CreatePost.anime_sel)
async def post_anime_selected(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(cb.from_user.id): return
    aid = int(cb.data.split("_")[1]); a = await get_anime(aid)
    if not a: await cb.answer("❌ Anime topilmadi!", show_alert=True); return
    d = await state.get_data(); fid=d['post_fid']; mt=d['post_mt']; caption=d['post_caption']
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        InlineKeyboard]