import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import logging
import sqlite3
import datetime
import random
import re

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, ContentType,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ============================================================
#   CONFIG
# ============================================================

BOT_TOKEN = "8483278525:AAHDLF9uuLu3r1j-hIKtiG2hWWY7-cHp_GM"
ADMINS = [7370706915]
REQUIRED_CHANNELS = [
    {"id": "@Aniyoof_bot", "name": "Asosiy Kanal", "url": "https://t.me/Aniyoof_bot"},
    {"id": "@Aniyoof_bot", "name": "Zaxira Kanal", "url": "https://t.me/+y4cOcCyKt6s4MGE6"},
]
MAIN_CHANNEL_ID = "@Aniyoof_bot"
PREMIUM_PRICES = {
    "1_month":  {"price": 15000,  "days": 30,  "label": "1 Oylik",  "profit": ""},
    "3_month":  {"price": 39000,  "days": 90,  "label": "3 Oylik",  "profit": "6 000 so'm tejaysiz"},
    "12_month": {"price": 120000, "days": 365, "label": "1 Yillik", "profit": "60 000 so'm tejaysiz"},
}
PAYMENT_CARD = "8600 0000 0000 0000"
PAYMENT_CARD_OWNER = "Ism Familiya"
ADMIN_USERNAME = "@admin_username"
RATING_RESET_HOUR = 12
RATING_RESET_MINUTE = 0
BOT_NAME = "Aniyoof Bot"
BOT_USERNAME = "@AniyoofBot"
DB_NAME = "aniyoof.db"

PREMIUM_BENEFITS = """💎 <b>Aniyoof Pass imkoniyatlari:</b>

1️⃣ Sifatli formatda animelarni ko'rish
2️⃣ Foydalanuvchilar reytingini ko'rish
3️⃣ Animelarni yuklab olish
4️⃣ Screenshot, video, gif va sticker orqali anime qidirish
5️⃣ Tavsiya va janr bo'yicha filtrlash"""

# ============================================================
#   DATABASE
# ============================================================

def get_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        tg_username TEXT,
        is_premium INTEGER DEFAULT 0,
        premium_until TEXT,
        is_banned INTEGER DEFAULT 0,
        joined_at TEXT DEFAULT (datetime('now')),
        favorite_genres TEXT DEFAULT '',
        total_watched INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS animes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        code TEXT UNIQUE NOT NULL,
        description TEXT,
        genre TEXT,
        seasons INTEGER DEFAULT 1,
        poster_id TEXT,
        poster_type TEXT DEFAULT 'photo',
        views INTEGER DEFAULT 0,
        rating_sum REAL DEFAULT 0,
        rating_count INTEGER DEFAULT 0,
        added_at TEXT DEFAULT (datetime('now')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anime_id INTEGER NOT NULL,
        season INTEGER DEFAULT 1,
        episode INTEGER NOT NULL,
        file_id TEXT NOT NULL,
        file_type TEXT DEFAULT 'video',
        FOREIGN KEY (anime_id) REFERENCES animes(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS watch_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        anime_id INTEGER,
        episode_id INTEGER,
        watched_at TEXT DEFAULT (datetime('now')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS ratings (
        user_id INTEGER PRIMARY KEY,
        tg_username TEXT,
        daily_count INTEGER DEFAULT 0,
        weekly_count INTEGER DEFAULT 0,
        monthly_count INTEGER DEFAULT 0,
        all_time_count INTEGER DEFAULT 0,
        last_updated TEXT DEFAULT (datetime('now')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS anime_ratings (
        user_id INTEGER,
        anime_id INTEGER,
        score INTEGER,
        PRIMARY KEY (user_id, anime_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER,
        anime_id INTEGER,
        added_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (user_id, anime_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        anime_id INTEGER,
        text TEXT,
        added_at TEXT DEFAULT (datetime('now')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        tariff TEXT,
        amount INTEGER,
        screenshot TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.commit()
    conn.close()
    print("✅ Ma'lumotlar bazasi tayyor!")

def get_user(user_id):
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return user

def user_exists(user_id):
    return get_user(user_id) is not None

def register_user(user_id, full_name, tg_username):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (user_id, full_name, tg_username) VALUES (?, ?, ?)",
            (user_id, full_name, tg_username)
        )
        conn.commit()
        conn.execute(
            "INSERT OR IGNORE INTO ratings (user_id, tg_username) VALUES (?, ?)",
            (user_id, tg_username)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_all_users():
    conn = get_conn()
    users = conn.execute("SELECT user_id FROM users WHERE is_banned=0").fetchall()
    conn.close()
    return [u["user_id"] for u in users]

def set_premium(user_id, days):
    until = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    conn.execute("UPDATE users SET is_premium=1, premium_until=? WHERE user_id=?", (until, user_id))
    conn.commit()
    conn.close()

def check_premium(user_id):
    user = get_user(user_id)
    if not user or not user["is_premium"]:
        return False
    until = datetime.datetime.strptime(user["premium_until"], "%Y-%m-%d %H:%M:%S")
    if datetime.datetime.now() > until:
        conn = get_conn()
        conn.execute("UPDATE users SET is_premium=0 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        return False
    return True

def get_user_stats():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
    premium = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE is_premium=1").fetchone()["cnt"]
    today = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE date(joined_at)=date('now')").fetchone()["cnt"]
    conn.close()
    return {"total": total, "premium": premium, "today": today}

def update_favorite_genres(user_id, genre):
    user = get_user(user_id)
    if not user:
        return
    genres = user["favorite_genres"] or ""
    genre_list = genres.split(",") if genres else []
    genre_list.append(genre)
    genre_list = genre_list[-20:]
    conn = get_conn()
    conn.execute("UPDATE users SET favorite_genres=? WHERE user_id=?", (",".join(genre_list), user_id))
    conn.commit()
    conn.close()

def get_favorite_genre(user_id):
    user = get_user(user_id)
    if not user or not user["favorite_genres"]:
        return None
    genres = user["favorite_genres"].split(",")
    return max(set(genres), key=genres.count)

def add_anime(title, code, description, genre, seasons, poster_id, poster_type="photo"):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO animes (title, code, description, genre, seasons, poster_id, poster_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, code, description, genre, seasons, poster_id, poster_type)
        )
        conn.commit()
        anime_id = conn.execute("SELECT id FROM animes WHERE code=?", (code,)).fetchone()["id"]
        conn.close()
        return anime_id
    except sqlite3.IntegrityError:
        conn.close()
        return None

def add_episode(anime_id, season, episode_num, file_id, file_type="video"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO episodes (anime_id, season, episode, file_id, file_type) VALUES (?, ?, ?, ?, ?)",
        (anime_id, season, episode_num, file_id, file_type)
    )
    conn.commit()
    conn.close()

def get_anime_by_code(code):
    conn = get_conn()
    anime = conn.execute("SELECT * FROM animes WHERE code=?", (code,)).fetchone()
    conn.close()
    return anime

def get_anime_by_id(anime_id):
    conn = get_conn()
    anime = conn.execute("SELECT * FROM animes WHERE id=?", (anime_id,)).fetchone()
    conn.close()
    return anime

def get_anime_by_title(title):
    conn = get_conn()
    animes = conn.execute("SELECT * FROM animes WHERE title LIKE ?", (f"%{title}%",)).fetchall()
    conn.close()
    return animes

def get_animes_by_genre(genre):
    conn = get_conn()
    animes = conn.execute("SELECT * FROM animes WHERE genre LIKE ?", (f"%{genre}%",)).fetchall()
    conn.close()
    return animes

def get_all_animes():
    conn = get_conn()
    animes = conn.execute("SELECT * FROM animes ORDER BY added_at DESC").fetchall()
    conn.close()
    return animes

def get_top_animes(limit=10):
    conn = get_conn()
    animes = conn.execute("SELECT * FROM animes ORDER BY views DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return animes

def get_random_anime():
    conn = get_conn()
    anime = conn.execute("SELECT * FROM animes ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()
    return anime

def get_episodes_by_season(anime_id, season):
    conn = get_conn()
    eps = conn.execute(
        "SELECT * FROM episodes WHERE anime_id=? AND season=? ORDER BY episode",
        (anime_id, season)
    ).fetchall()
    conn.close()
    return eps

def get_all_episodes(anime_id):
    conn = get_conn()
    eps = conn.execute("SELECT * FROM episodes WHERE anime_id=? ORDER BY season, episode", (anime_id,)).fetchall()
    conn.close()
    return eps

def get_episode(anime_id, season, episode_num):
    conn = get_conn()
    ep = conn.execute(
        "SELECT * FROM episodes WHERE anime_id=? AND season=? AND episode=?",
        (anime_id, season, episode_num)
    ).fetchone()
    conn.close()
    return ep

def get_seasons_list(anime_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT season FROM episodes WHERE anime_id=? ORDER BY season",
        (anime_id,)
    ).fetchall()
    conn.close()
    return [r["season"] for r in rows]

def increment_views(anime_id):
    conn = get_conn()
    conn.execute("UPDATE animes SET views=views+1 WHERE id=?", (anime_id,))
    conn.commit()
    conn.close()

def get_all_anime_list():
    conn = get_conn()
    animes = conn.execute("SELECT id, title, code FROM animes ORDER BY id").fetchall()
    conn.close()
    return animes

def record_watch(user_id, anime_id, episode_id):
    conn = get_conn()
    conn.execute(
        "INSERT INTO watch_history (user_id, anime_id, episode_id) VALUES (?, ?, ?)",
        (user_id, anime_id, episode_id)
    )
    conn.execute("""UPDATE ratings SET daily_count=daily_count+1, weekly_count=weekly_count+1,
        monthly_count=monthly_count+1, all_time_count=all_time_count+1,
        last_updated=datetime('now') WHERE user_id=?""", (user_id,))
    conn.execute("UPDATE users SET total_watched=total_watched+1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_top_rating(period="daily", limit=10):
    col_map = {"daily": "daily_count", "weekly": "weekly_count",
               "monthly": "monthly_count", "all_time": "all_time_count"}
    col = col_map.get(period, "daily_count")
    conn = get_conn()
    rows = conn.execute(f"""SELECT r.tg_username, r.{col} as count, u.full_name
        FROM ratings r JOIN users u ON r.user_id=u.user_id
        WHERE r.{col}>0 ORDER BY r.{col} DESC LIMIT ?""", (limit,)).fetchall()
    conn.close()
    return rows

def reset_daily():
    conn = get_conn()
    conn.execute("UPDATE ratings SET daily_count=0")
    conn.commit()
    conn.close()

def reset_weekly():
    conn = get_conn()
    conn.execute("UPDATE ratings SET weekly_count=0")
    conn.commit()
    conn.close()

def reset_monthly():
    conn = get_conn()
    conn.execute("UPDATE ratings SET monthly_count=0")
    conn.commit()
    conn.close()

def add_favorite(user_id, anime_id):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO favorites (user_id, anime_id) VALUES (?, ?)", (user_id, anime_id))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def remove_favorite(user_id, anime_id):
    conn = get_conn()
    conn.execute("DELETE FROM favorites WHERE user_id=? AND anime_id=?", (user_id, anime_id))
    conn.commit()
    conn.close()

def get_favorites(user_id):
    conn = get_conn()
    favs = conn.execute(
        "SELECT a.* FROM favorites f JOIN animes a ON f.anime_id=a.id WHERE f.user_id=?",
        (user_id,)
    ).fetchall()
    conn.close()
    return favs

def is_favorite(user_id, anime_id):
    conn = get_conn()
    r = conn.execute("SELECT 1 FROM favorites WHERE user_id=? AND anime_id=?", (user_id, anime_id)).fetchone()
    conn.close()
    return r is not None

def add_comment(user_id, anime_id, text):
    conn = get_conn()
    conn.execute("INSERT INTO comments (user_id, anime_id, text) VALUES (?, ?, ?)", (user_id, anime_id, text))
    conn.commit()
    conn.close()

def get_comments(anime_id, limit=5):
    conn = get_conn()
    rows = conn.execute("""SELECT c.text, u.tg_username, c.added_at
        FROM comments c JOIN users u ON c.user_id=u.user_id
        WHERE c.anime_id=? ORDER BY c.added_at DESC LIMIT ?""", (anime_id, limit)).fetchall()
    conn.close()
    return rows

def rate_anime(user_id, anime_id, score):
    conn = get_conn()
    conn.execute("""INSERT INTO anime_ratings (user_id, anime_id, score) VALUES (?, ?, ?)
        ON CONFLICT(user_id, anime_id) DO UPDATE SET score=excluded.score""",
        (user_id, anime_id, score))
    avg = conn.execute(
        "SELECT AVG(score) as avg, COUNT(*) as cnt FROM anime_ratings WHERE anime_id=?",
        (anime_id,)
    ).fetchone()
    conn.execute(
        "UPDATE animes SET rating_sum=?, rating_count=? WHERE id=?",
        (avg["avg"] * avg["cnt"], avg["cnt"], anime_id)
    )
    conn.commit()
    conn.close()

def get_anime_rating(anime_id):
    conn = get_conn()
    anime = conn.execute("SELECT rating_sum, rating_count FROM animes WHERE id=?", (anime_id,)).fetchone()
    conn.close()
    if anime and anime["rating_count"] > 0:
        return round(anime["rating_sum"] / anime["rating_count"], 1)
    return 0

def add_transaction(user_id, tariff, amount, screenshot):
    conn = get_conn()
    conn.execute(
        "INSERT INTO transactions (user_id, tariff, amount, screenshot) VALUES (?, ?, ?, ?)",
        (user_id, tariff, amount, screenshot)
    )
    conn.commit()
    tx_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return tx_id

def update_transaction_status(tx_id, status):
    conn = get_conn()
    conn.execute("UPDATE transactions SET status=? WHERE id=?", (status, tx_id))
    conn.commit()
    conn.close()

# ============================================================
#   KEYBOARDS
# ============================================================

def subscription_kb():
    buttons = []
    for ch in REQUIRED_CHANNELS:
        buttons.append([InlineKeyboardButton(text=f"📢 {ch['name']}", url=ch["url"])])
    buttons.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def remove_kb():
    return ReplyKeyboardRemove()

def main_menu_kb(is_premium=False):
    premium_btn = "💎 Aniyoof Pass ✅" if is_premium else "💎 Aniyoof Pass"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Anime izlash", callback_data="search_menu"),
         InlineKeyboardButton(text="📺 Barcha animelar", callback_data="all_animes")],
        [InlineKeyboardButton(text=premium_btn, callback_data="premium_menu"),
         InlineKeyboardButton(text="🏆 Reyting", callback_data="rating_menu")],
        [InlineKeyboardButton(text="❤️ Sevimlilar", callback_data="favorites"),
         InlineKeyboardButton(text="👤 Profilim", callback_data="my_profile")],
    ])

def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Anime qo'shish", callback_data="admin_add_anime_menu")],
        [InlineKeyboardButton(text="💎 Premium berish", callback_data="admin_give_premium"),
         InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📨 Xabar yuborish", callback_data="admin_broadcast")],
    ])

def admin_add_anime_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Yangi anime ma'lumot qo'shish", callback_data="admin_add_info")],
        [InlineKeyboardButton(text="🎬 Anime video qo'shish", callback_data="admin_add_video")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_menu")],
    ])

def search_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔤 Nomi bilan", callback_data="search_name"),
         InlineKeyboardButton(text="🔢 Kodi bilan", callback_data="search_code")],
        [InlineKeyboardButton(text="🎭 Janr bilan", callback_data="search_genre"),
         InlineKeyboardButton(text="🎲 Random", callback_data="search_random")],
        [InlineKeyboardButton(text="⭐ Tavsiya", callback_data="search_recommend"),
         InlineKeyboardButton(text="🔥 Top animelar", callback_data="search_top")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")],
    ])

GENRES = ["Action", "Romance", "Comedy", "Drama", "Fantasy", "Sci-Fi",
          "Horror", "Thriller", "Adventure", "Slice of Life", "Sports",
          "Mystery", "Supernatural", "Mecha", "Psychological"]

def genres_kb(selected=None):
    if selected is None:
        selected = []
    buttons = []
    row = []
    for i, g in enumerate(GENRES):
        check = "✅ " if g in selected else ""
        row.append(InlineKeyboardButton(text=f"{check}{g}", callback_data=f"genre_{g}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="🔍 Qidirish", callback_data="genre_search"),
        InlineKeyboardButton(text="🔙 Orqaga", callback_data="search_menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def seasons_kb(anime_id, seasons_list):
    buttons = []
    for s in seasons_list:
        buttons.append([InlineKeyboardButton(text=f"📂 {s}-Fasl", callback_data=f"season_{anime_id}_{s}")])
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"anime_{anime_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def episodes_season_kb(anime_id, season, episodes, is_premium=False):
    buttons = []
    row = []
    for ep in episodes:
        ep_num = ep["episode"]
        lock = "🔒" if (ep_num > 3 and not is_premium) else ""
        row.append(InlineKeyboardButton(
            text=f"{lock}{ep_num}-qism",
            callback_data=f"watch_{anime_id}_{season}_{ep_num}"
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🔙 Fasllar", callback_data=f"seasons_{anime_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def anime_card_kb(anime_id, is_fav=False, has_episodes=False):
    fav_text = "❤️ Saqlandi" if is_fav else "🤍 Sevimliga"
    buttons = []
    if has_episodes:
        buttons.append([InlineKeyboardButton(text="▶️ Anime ko'rish", callback_data=f"seasons_{anime_id}")])
    buttons.append([
        InlineKeyboardButton(text=fav_text, callback_data=f"fav_{anime_id}"),
        InlineKeyboardButton(text="💬 Izoh", callback_data=f"comment_{anime_id}")
    ])
    buttons.append([
        InlineKeyboardButton(text="⭐ Baho ber", callback_data=f"rate_{anime_id}"),
        InlineKeyboardButton(text="🔙 Orqaga", callback_data="search_menu")
    ])
    buttons.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def rating_stars_kb(anime_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(i), callback_data=f"score_{anime_id}_{i}") for i in range(1, 6)],
        [InlineKeyboardButton(text=str(i), callback_data=f"score_{anime_id}_{i}") for i in range(6, 11)],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"anime_{anime_id}")]
    ])

def rating_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Kunlik", callback_data="rating_daily"),
         InlineKeyboardButton(text="📆 Haftalik", callback_data="rating_weekly")],
        [InlineKeyboardButton(text="🗓 Oylik", callback_data="rating_monthly"),
         InlineKeyboardButton(text="🏆 Barcha vaqt", callback_data="rating_all_time")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")],
    ])

def premium_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Bot orqali olish", callback_data="premium_bot")],
        [InlineKeyboardButton(text="👤 Admin orqali olish", callback_data="premium_admin")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")],
    ])

def premium_tariffs_kb():
    buttons = []
    for key, val in PREMIUM_PRICES.items():
        profit = f" ({val['profit']})" if val["profit"] else ""
        buttons.append([InlineKeyboardButton(
            text=f"💎 {val['label']} — {val['price']:,} so'm{profit}",
            callback_data=f"buy_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="premium_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def payment_confirm_kb(tariff):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ To'lov qildim, chek yuboraman", callback_data=f"paid_{tariff}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="premium_menu")],
    ])

def admin_approve_kb(user_id, tx_id, tariff):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_{user_id}_{tx_id}_{tariff}"),
         InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_{user_id}_{tx_id}")],
    ])

def admin_give_premium_tariff_kb(user_id):
    buttons = []
    for key, val in PREMIUM_PRICES.items():
        buttons.append([InlineKeyboardButton(
            text=f"💎 {val['label']}",
            callback_data=f"givepremium_{user_id}_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def all_animes_kb(animes, page=0):
    per_page = 8
    start = page * per_page
    end = start + per_page
    page_animes = animes[start:end]
    buttons = []
    for anime in page_animes:
        buttons.append([InlineKeyboardButton(
            text=f"🎬 {anime['title']} [{anime['code']}]",
            callback_data=f"anime_{anime['id']}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"animes_page_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{(len(animes)-1)//per_page+1}", callback_data="noop"))
    if end < len(animes):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"animes_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def back_to_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="main_menu")]
    ])

def back_to_search_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Qidiruvga qaytish", callback_data="search_menu")],
        [InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="main_menu")]
    ])

# ============================================================
#   SCHEDULER
# ============================================================

scheduler = AsyncIOScheduler(timezone="UTC")

def start_scheduler():
    scheduler.add_job(
        lambda: (reset_daily(), logging.info("✅ Kunlik reyting yangilandi")),
        CronTrigger(hour=RATING_RESET_HOUR, minute=RATING_RESET_MINUTE, timezone="UTC")
    )
    scheduler.add_job(
        lambda: (reset_weekly(), logging.info("✅ Haftalik reyting yangilandi")),
        CronTrigger(day_of_week="mon", hour=RATING_RESET_HOUR, minute=RATING_RESET_MINUTE, timezone="UTC")
    )
    scheduler.add_job(
        lambda: (reset_monthly(), logging.info("✅ Oylik reyting yangilandi")),
        CronTrigger(day=1, hour=RATING_RESET_HOUR, minute=RATING_RESET_MINUTE, timezone="UTC")
    )
    scheduler.start()
    logging.info("✅ Scheduler ishga tushdi!")

# ============================================================
#   STATES
# ============================================================

class RegisterStates(StatesGroup):
    waiting_confirm = State()

class AdminStates(StatesGroup):
    add_title = State()
    add_code = State()
    add_genre = State()
    add_seasons = State()
    add_poster = State()
    add_description = State()
    # Video qo'shish
    select_anime_for_video = State()
    select_season_for_video = State()
    uploading_videos = State()
    # Premium berish
    give_premium_user = State()
    # Broadcast
    broadcast_text = State()

class SearchStates(StatesGroup):
    by_name = State()
    by_code = State()
    comment_text = State()
    media_search = State()

class PremiumStates(StatesGroup):
    waiting_screenshot = State()

# ============================================================
#   ROUTERS
# ============================================================

router_start = Router()
router_admin = Router()
router_anime = Router()
router_premium = Router()
router_rating = Router()

# ============================================================
#   HELPERS
# ============================================================

async def check_subscription(bot: Bot, user_id: int) -> bool:
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(ch["id"], user_id)
            if member.status in ["left", "kicked", "restricted"]:
                return False
        except Exception:
            pass
    return True

def is_admin(user_id):
    return user_id in ADMINS

async def show_anime_card(message: Message, anime, user_id, edit=False):
    seasons = get_seasons_list(anime["id"])
    is_fav = is_favorite(user_id, anime["id"])
    avg_rating = get_anime_rating(anime["id"])
    stars = "⭐" * min(int(avg_rating), 5) if avg_rating else "—"
    total_eps = len(get_all_episodes(anime["id"]))
    caption = (
        f"🎬 <b>{anime['title']}</b>\n"
        f"🔢 Kod: <code>{anime['code']}</code>\n"
        f"🎭 Janr: <b>{anime['genre']}</b>\n"
        f"📂 Fasllar: <b>{len(seasons)} ta</b> | 📺 Qismlar: <b>{total_eps} ta</b>\n"
        f"⭐ Reyting: <b>{avg_rating}/10</b> {stars}\n"
        f"👁 Ko'rishlar: <b>{anime['views']:,}</b>\n\n"
        f"📖 {anime['description'] or '—'}"
    )
    kb = anime_card_kb(anime["id"], is_fav, has_episodes=len(seasons) > 0)
    try:
        if edit:
            await message.edit_caption(caption=caption, reply_markup=kb, parse_mode="HTML")
        else:
            if anime["poster_id"]:
                if anime.get("poster_type") == "video":
                    await message.answer_video(video=anime["poster_id"], caption=caption,
                                               reply_markup=kb, parse_mode="HTML")
                else:
                    await message.answer_photo(photo=anime["poster_id"], caption=caption,
                                               reply_markup=kb, parse_mode="HTML")
            else:
                await message.answer(caption, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.answer(caption, reply_markup=kb, parse_mode="HTML")

# ============================================================
#   START HANDLERS
# ============================================================

@router_start.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id

    if user_id in ADMINS:
        await message.answer(
            f"👑 <b>Admin paneliga xush kelibsiz!</b>\n\nSalom, <b>{message.from_user.first_name}</b>!",
            reply_markup=admin_menu_kb(), parse_mode="HTML"
        )
        return

    subscribed = await check_subscription(bot, user_id)
    if not subscribed:
        await message.answer(
            "📢 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:</b>",
            reply_markup=subscription_kb(), parse_mode="HTML"
        )
        return

    if user_exists(user_id):
        user = get_user(user_id)
        is_prem = check_premium(user_id)
        uname = user["tg_username"] or message.from_user.first_name
        await message.answer(
            f"👋 Salom, <b>@{uname}</b>!\n\n{'💎 <b>Aniyoof Pass</b> faol ✅' if is_prem else ''}",
            reply_markup=main_menu_kb(is_prem), parse_mode="HTML"
        )
        return

    # Ro'yxatdan o'tish — Telegram username ishlatamiz
    tg_username = message.from_user.username
    full_name = message.from_user.full_name

    if not tg_username:
        await message.answer(
            "⚠️ <b>Telegram usernamengiz yo'q!</b>\n\n"
            "Botdan foydalanish uchun Telegram sozlamalaridan username o'rnating, keyin /start bosing.",
            parse_mode="HTML"
        )
        return

    success = register_user(user_id, full_name, tg_username)
    if success:
        await message.answer(
            f"🎉 <b>Xush kelibsiz!</b>\n\n"
            f"👤 Username: <b>@{tg_username}</b>\n\n"
            f"🎌 <b>{BOT_NAME}</b> ga xush kelibsiz!",
            reply_markup=main_menu_kb(), parse_mode="HTML"
        )
    else:
        await message.answer(
            f"👋 Salom, <b>@{tg_username}</b>!",
            reply_markup=main_menu_kb(), parse_mode="HTML"
        )

@router_start.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    subscribed = await check_subscription(bot, user_id)
    if not subscribed:
        await callback.answer("❌ Hali obuna bo'lmadingiz!", show_alert=True)
        return
    await callback.message.delete()
    tg_username = callback.from_user.username
    full_name = callback.from_user.full_name
    if not user_exists(user_id):
        if not tg_username:
            await callback.message.answer(
                "⚠️ Telegram usernamengiz yo'q! Sozlamalardan username qo'ying va /start bosing."
            )
            return
        register_user(user_id, full_name, tg_username)
    is_prem = check_premium(user_id)
    await callback.message.answer(
        f"✅ Obuna tasdiqlandi!\n\n👋 Xush kelibsiz, <b>@{tg_username}</b>!",
        reply_markup=main_menu_kb(is_prem), parse_mode="HTML"
    )
    await callback.answer()

@router_start.callback_query(F.data == "main_menu")
async def main_menu_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    is_prem = check_premium(user_id)
    user = get_user(user_id)
    uname = user["tg_username"] if user else callback.from_user.username or callback.from_user.first_name
    try:
        await callback.message.edit_text(
            f"🏠 <b>Bosh menyu</b>\n\n👤 <b>@{uname}</b>{'  |  💎 Premium' if is_prem else ''}",
            reply_markup=main_menu_kb(is_prem), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            f"🏠 <b>Bosh menyu</b>", reply_markup=main_menu_kb(is_prem), parse_mode="HTML"
        )
    await callback.answer()

@router_start.callback_query(F.data == "my_profile")
async def my_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    if not user:
        await callback.answer("Profil topilmadi!")
        return
    is_prem = check_premium(user_id)
    prem_text = "💎 Aniyoof Pass faol ✅" if is_prem else "❌ Aniyoof Pass yo'q"
    if is_prem:
        prem_text += f"\n📅 Tugash sanasi: <b>{user['premium_until'][:10]}</b>"
    await callback.message.edit_text(
        f"👤 <b>Profilim</b>\n\n"
        f"🆔 Username: <b>@{user['tg_username']}</b>\n"
        f"📺 Jami ko'rilgan: <b>{user['total_watched']} ta</b>\n"
        f"📅 Ro'yxatdan o'tgan: <b>{user['joined_at'][:10]}</b>\n\n"
        f"{prem_text}",
        reply_markup=back_to_main_kb(), parse_mode="HTML"
    )
    await callback.answer()

# ============================================================
#   ADMIN HANDLERS
# ============================================================

@router_admin.callback_query(F.data == "admin_menu")
async def admin_menu_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Sizda ruxsat yo'q!", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_text("👑 <b>Admin Panel</b>", reply_markup=admin_menu_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer("👑 <b>Admin Panel</b>", reply_markup=admin_menu_kb(), parse_mode="HTML")
    await callback.answer()

@router_admin.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    stats = get_user_stats()
    animes = get_all_animes()
    await callback.message.edit_text(
        f"📊 <b>Bot Statistikasi</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{stats['total']}</b>\n"
        f"💎 Premium: <b>{stats['premium']}</b>\n"
        f"🆕 Bugun: <b>{stats['today']}</b>\n"
        f"🎬 Jami animalar: <b>{len(animes)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_menu")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()

# --- ANIME QO'SHISH MENU ---

@router_admin.callback_query(F.data == "admin_add_anime_menu")
async def admin_add_anime_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text(
        "🎬 <b>Anime qo'shish</b>\n\nNimani qilmoqchisiz?",
        reply_markup=admin_add_anime_menu_kb(), parse_mode="HTML"
    )
    await callback.answer()

# --- YANGI ANIME MA'LUMOT QO'SHISH ---

@router_admin.callback_query(F.data == "admin_add_info")
async def admin_add_info(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text(
        "📝 <b>Yangi anime ma'lumot qo'shish</b>\n\n1️⃣ Anime nomini kiriting:",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.add_title)
    await callback.answer()

@router_admin.message(AdminStates.add_title)
async def get_anime_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(title=message.text.strip())
    await message.answer(
        f"✅ Nom: <b>{message.text.strip()}</b>\n\n2️⃣ Anime kodini kiriting (masalan: AOT, NRT):",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.add_code)

@router_admin.message(AdminStates.add_code)
async def get_anime_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    code = message.text.strip().upper()
    if get_anime_by_code(code):
        await message.answer(f"❌ <b>{code}</b> kodi allaqachon mavjud! Boshqa kod:", parse_mode="HTML")
        return
    await state.update_data(code=code)
    await message.answer(f"✅ Kod: <b>{code}</b>\n\n3️⃣ Anime janrini kiriting (masalan: Action, Comedy):", parse_mode="HTML")
    await state.set_state(AdminStates.add_genre)

@router_admin.message(AdminStates.add_genre)
async def get_anime_genre(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(genre=message.text.strip())
    await message.answer("4️⃣ Fasllar sonini kiriting (masalan: 1, 2, 3):")
    await state.set_state(AdminStates.add_seasons)

@router_admin.message(AdminStates.add_seasons)
async def get_anime_seasons(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        seasons = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Raqam kiriting! Masalan: 1")
        return
    await state.update_data(seasons=seasons)
    await message.answer(
        "5️⃣ Anime poster yuboring (rasm yoki qisqa video):\n<i>/skip — o'tkazib yuborish</i>",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.add_poster)

@router_admin.message(AdminStates.add_poster, F.photo)
async def get_poster_photo(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(poster_id=message.photo[-1].file_id, poster_type="photo")
    await message.answer("6️⃣ Anime haqida qisqacha tavsif yozing:")
    await state.set_state(AdminStates.add_description)

@router_admin.message(AdminStates.add_poster, F.video)
async def get_poster_video(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(poster_id=message.video.file_id, poster_type="video")
    await message.answer("6️⃣ Anime haqida qisqacha tavsif yozing:")
    await state.set_state(AdminStates.add_description)

@router_admin.message(AdminStates.add_poster, F.text == "/skip")
async def skip_poster(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(poster_id=None, poster_type="photo")
    await message.answer("6️⃣ Anime haqida qisqacha tavsif yozing:")
    await state.set_state(AdminStates.add_description)

@router_admin.message(AdminStates.add_description)
async def get_anime_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(description=message.text.strip())
    data = await state.get_data()
    anime_id = add_anime(
        title=data["title"], code=data["code"],
        description=data["description"], genre=data["genre"],
        seasons=data["seasons"], poster_id=data.get("poster_id"),
        poster_type=data.get("poster_type", "photo")
    )
    await state.clear()
    if anime_id:
        await message.answer(
            f"✅ <b>Anime muvaffaqiyatli qo'shildi!</b>\n\n"
            f"🎬 {data['title']} | Kod: {data['code']} | {data['seasons']} fasl\n\n"
            f"Endi <b>Anime video qo'shish</b> orqali videolarni qo'shing.",
            reply_markup=admin_add_anime_menu_kb(), parse_mode="HTML"
        )
    else:
        await message.answer("❌ Xato yuz berdi!", reply_markup=admin_menu_kb())

# --- ANIME VIDEO QO'SHISH ---

@router_admin.callback_query(F.data == "admin_add_video")
async def admin_add_video(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    animes = get_all_anime_list()
    if not animes:
        await callback.answer("❌ Hali anime qo'shilmagan!", show_alert=True)
        return
    buttons = [
        [InlineKeyboardButton(text=f"🎬 {a['title']} [{a['code']}]", callback_data=f"vidanime_{a['id']}")]
        for a in animes
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_add_anime_menu")])
    await callback.message.edit_text(
        "🎬 <b>Qaysi animega video qo'shmoqchisiz?</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )
    await state.set_state(AdminStates.select_anime_for_video)
    await callback.answer()

@router_admin.callback_query(F.data.startswith("vidanime_"))
async def select_anime_for_video(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    anime_id = int(callback.data.replace("vidanime_", ""))
    anime = get_anime_by_id(anime_id)
    await state.update_data(vid_anime_id=anime_id, vid_anime_title=anime["title"])
    seasons_count = anime["seasons"]
    buttons = [
        [InlineKeyboardButton(text=f"📂 {s}-Fasl", callback_data=f"vidseason_{s}")]
        for s in range(1, seasons_count + 1)
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_add_video")])
    await callback.message.edit_text(
        f"🎬 <b>{anime['title']}</b>\n\nQaysi faslga video qo'shmoqchisiz?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )
    await state.set_state(AdminStates.select_season_for_video)
    await callback.answer()

@router_admin.callback_query(F.data.startswith("vidseason_"))
async def select_season_for_video(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    season = int(callback.data.replace("vidseason_", ""))
    await state.update_data(vid_season=season, vid_ep_counter=1)
    data = await state.get_data()
    existing = get_episodes_by_season(data["vid_anime_id"], season)
    next_ep = len(existing) + 1
    await state.update_data(vid_ep_counter=next_ep)
    await callback.message.edit_text(
        f"📂 <b>{data['vid_anime_title']} — {season}-Fasl</b>\n\n"
        f"📺 Hozir: {len(existing)} ta qism\n"
        f"➕ Keyingisi: <b>{next_ep}-qism</b>\n\n"
        f"🎥 Videolarni birin-ketin yuboring.\n"
        f"Barcha videolarni yuborib bo'lgach <b>/done</b> yozing.",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.uploading_videos)
    await callback.answer()

@router_admin.message(AdminStates.uploading_videos, F.video)
async def upload_video(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    ep_num = data["vid_ep_counter"]
    add_episode(data["vid_anime_id"], data["vid_season"], ep_num, message.video.file_id, "video")
    await state.update_data(vid_ep_counter=ep_num + 1)
    await message.answer(
        f"✅ <b>{ep_num}-qism</b> qo'shildi!\n"
        f"Keyingi videoni yuboring yoki /done yozing.",
        parse_mode="HTML"
    )

@router_admin.message(AdminStates.uploading_videos, F.document)
async def upload_document(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    ep_num = data["vid_ep_counter"]
    add_episode(data["vid_anime_id"], data["vid_season"], ep_num, message.document.file_id, "document")
    await state.update_data(vid_ep_counter=ep_num + 1)
    await message.answer(
        f"✅ <b>{ep_num}-qism</b> qo'shildi!\n"
        f"Keyingi videoni yuboring yoki /done yozing.",
        parse_mode="HTML"
    )

@router_admin.message(AdminStates.uploading_videos, F.text == "/done")
async def done_uploading(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    total = data["vid_ep_counter"] - 1
    await state.clear()
    await message.answer(
        f"✅ <b>{total} ta qism muvaffaqiyatli qo'shildi!</b>",
        reply_markup=admin_menu_kb(), parse_mode="HTML"
    )

# --- PREMIUM BERISH ---

@router_admin.callback_query(F.data == "admin_give_premium")
async def admin_give_premium(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text(
        "💎 <b>Foydalanuvchiga Premium berish</b>\n\nTelegram username kiriting (@ belgisisiz):",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.give_premium_user)
    await callback.answer()

@router_admin.message(AdminStates.give_premium_user)
async def find_user_for_premium(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    query = message.text.strip().lstrip("@")
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE tg_username=?", (query,)).fetchone()
    conn.close()
    if not user:
        await message.answer(
            f"❌ <b>@{query}</b> foydalanuvchi topilmadi!\nUsername to'g'ri ekanini tekshiring.",
            parse_mode="HTML"
        )
        return
    await state.clear()
    await message.answer(
        f"👤 Foydalanuvchi: <b>@{user['tg_username']}</b>\n\n💎 Qaysi tarifni bermoqchisiz?",
        reply_markup=admin_give_premium_tariff_kb(user["user_id"]), parse_mode="HTML"
    )

@router_admin.callback_query(F.data.startswith("givepremium_"))
async def give_premium_confirm(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_")
    user_id = int(parts[1])
    tariff = "_".join(parts[2:])
    tariff_data = PREMIUM_PRICES.get(tariff)
    if not tariff_data:
        await callback.answer(f"❌ Tarif topilmadi! ({tariff})", show_alert=True)
        return
    set_premium(user_id, tariff_data["days"])
    user = get_user(user_id)
    await callback.message.edit_text(
        f"✅ <b>@{user['tg_username']}</b> ga {tariff_data['label']} Premium berildi!",
        reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Tabriklaymiz!</b>\n\n"
            f"💎 <b>Aniyoof Pass</b> ({tariff_data['label']}) aktivlashtirildi!\n"
            f"📅 Muddati: {tariff_data['days']} kun",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()

# --- BROADCAST ---

@router_admin.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text(
        "📨 <b>Barcha foydalanuvchilarga xabar yuborish</b>\n\nXabar matnini yozing:",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.broadcast_text)
    await callback.answer()

@router_admin.message(AdminStates.broadcast_text)
async def send_broadcast(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    users = get_all_users()
    sent = 0
    failed = 0
    await message.answer(f"⏳ Yuborilmoqda... ({len(users)} ta foydalanuvchi)")
    for uid in users:
        try:
            await bot.send_message(uid, message.text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
    await state.clear()
    await message.answer(
        f"✅ Yuborildi: <b>{sent}</b>\n❌ Xato: <b>{failed}</b>",
        reply_markup=admin_menu_kb(), parse_mode="HTML"
    )

# TO'LOV TASDIQLASH

@router_admin.callback_query(F.data.startswith("approve_"))
async def approve_premium(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_")
    user_id = int(parts[1])
    tx_id = int(parts[2])
    tariff = "_".join(parts[3:])
    tariff_data = PREMIUM_PRICES.get(tariff)
    if not tariff_data:
        await callback.answer(f"❌ Tarif topilmadi! ({tariff})", show_alert=True)
        return
    set_premium(user_id, tariff_data["days"])
    update_transaction_status(tx_id, "approved")
    user = get_user(user_id)
    await callback.message.edit_caption(
        caption=f"✅ <b>@{user['tg_username']}</b> ga {tariff_data['label']} Premium berildi!",
        parse_mode="HTML"
    )
    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>To'lovingiz tasdiqlandi!</b>\n\n"
            f"💎 <b>Aniyoof Pass</b> ({tariff_data['label']}) aktivlashtirildi!\n"
            f"📅 Muddati: {tariff_data['days']} kun",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("✅ Premium berildi!")

@router_admin.callback_query(F.data.startswith("reject_"))
async def reject_premium(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_")
    user_id = int(parts[1])
    tx_id = int(parts[2])
    update_transaction_status(tx_id, "rejected")
    try:
        await bot.send_message(
            user_id,
            "❌ <b>To'lovingiz tasdiqlanmadi!</b>\n\nMuammo bo'lsa admin bilan bog'laning.",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.message.edit_caption(caption="❌ To'lov rad etildi.", parse_mode="HTML")
    await callback.answer("❌ Rad etildi!")

# ============================================================
#   ANIME HANDLERS
# ============================================================

@router_anime.callback_query(F.data == "search_menu")
async def search_menu_cb(callback: CallbackQuery):
    try:
        await callback.message.edit_text(
            "🔎 <b>Anime izlash</b>\n\nQidirish turini tanlang:",
            reply_markup=search_menu_kb(), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "🔎 <b>Anime izlash</b>", reply_markup=search_menu_kb(), parse_mode="HTML"
        )
    await callback.answer()

@router_anime.callback_query(F.data == "search_name")
async def search_by_name(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔤 Anime nomini yozing:", parse_mode="HTML"
    )
    await state.set_state(SearchStates.by_name)
    await callback.answer()

@router_anime.message(SearchStates.by_name)
async def process_name_search(message: Message, state: FSMContext):
    await state.clear()
    animes = get_anime_by_title(message.text.strip())
    if not animes:
        await message.answer("😔 Anime topilmadi!", reply_markup=back_to_search_kb())
        return
    if len(animes) == 1:
        await show_anime_card(message, animes[0], message.from_user.id)
        return
    buttons = [
        [InlineKeyboardButton(text=f"🎬 {a['title']}", callback_data=f"anime_{a['id']}")]
        for a in animes[:10]
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="search_menu")])
    await message.answer(
        f"🔍 <b>{len(animes)} ta anime topildi:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )

@router_anime.callback_query(F.data == "search_code")
async def search_by_code(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🔢 Anime kodini kiriting:")
    await state.set_state(SearchStates.by_code)
    await callback.answer()

@router_anime.message(SearchStates.by_code)
async def process_code_search(message: Message, state: FSMContext):
    await state.clear()
    anime = get_anime_by_code(message.text.strip().upper())
    if not anime:
        await message.answer("😔 Anime topilmadi!", reply_markup=back_to_search_kb())
        return
    await show_anime_card(message, anime, message.from_user.id)

@router_anime.callback_query(F.data == "search_genre")
async def search_by_genre(callback: CallbackQuery, state: FSMContext):
    await state.update_data(selected_genres=[])
    await callback.message.edit_text(
        "🎭 <b>Janr tanlang:</b>", reply_markup=genres_kb([]), parse_mode="HTML"
    )
    await callback.answer()

@router_anime.callback_query(F.data.startswith("genre_") & ~F.data.startswith("genre_search"))
async def toggle_genre(callback: CallbackQuery, state: FSMContext):
    genre = callback.data.replace("genre_", "")
    data = await state.get_data()
    selected = data.get("selected_genres", [])
    if genre in selected:
        selected.remove(genre)
    else:
        selected.append(genre)
    await state.update_data(selected_genres=selected)
    await callback.message.edit_reply_markup(reply_markup=genres_kb(selected))
    await callback.answer()

@router_anime.callback_query(F.data == "genre_search")
async def do_genre_search(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_genres", [])
    await state.clear()
    if not selected:
        await callback.answer("❌ Hech bo'lmaganda 1 ta janr tanlang!", show_alert=True)
        return
    results = []
    for genre in selected:
        for anime in get_animes_by_genre(genre):
            if anime["id"] not in [r["id"] for r in results]:
                results.append(anime)
    if not results:
        await callback.message.edit_text("😔 Bu janrlarda anime topilmadi!", reply_markup=back_to_search_kb())
        return
    buttons = [
        [InlineKeyboardButton(text=f"🎬 {a['title']}", callback_data=f"anime_{a['id']}")]
        for a in results[:10]
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="search_menu")])
    await callback.message.edit_text(
        f"🎭 {len(results)} ta anime topildi:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )
    await callback.answer()

@router_anime.callback_query(F.data == "search_random")
async def search_random(callback: CallbackQuery):
    anime = get_random_anime()
    if not anime:
        await callback.answer("❌ Anime yo'q!", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_anime_card(callback.message, anime, callback.from_user.id)
    await callback.answer()

@router_anime.callback_query(F.data == "search_recommend")
async def search_recommend(callback: CallbackQuery):
    user_id = callback.from_user.id
    fav_genre = get_favorite_genre(user_id)
    if fav_genre:
        animes = get_animes_by_genre(fav_genre)
        anime = random.choice(animes) if animes else get_random_anime()
    else:
        anime = get_random_anime()
    if not anime:
        await callback.answer("❌ Anime yo'q!", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_anime_card(callback.message, anime, user_id)
    await callback.answer()

@router_anime.callback_query(F.data == "search_top")
async def search_top(callback: CallbackQuery):
    animes = get_top_animes(10)
    if not animes:
        await callback.answer("❌ Anime yo'q!", show_alert=True)
        return
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    buttons = [
        [InlineKeyboardButton(
            text=f"{medals.get(i, str(i)+'.')} {a['title']} — {a['views']:,} 👁",
            callback_data=f"anime_{a['id']}"
        )]
        for i, a in enumerate(animes, 1)
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="search_menu")])
    await callback.message.edit_text(
        "🔥 <b>Eng ko'p ko'rilgan animelar:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )
    await callback.answer()

@router_anime.callback_query(F.data == "all_animes")
async def all_animes_cb(callback: CallbackQuery):
    animes = get_all_animes()
    if not animes:
        await callback.answer("❌ Hali anime yo'q!", show_alert=True)
        return
    await callback.message.edit_text(
        f"📺 <b>Barcha animelar ({len(animes)} ta):</b>",
        reply_markup=all_animes_kb(animes, 0), parse_mode="HTML"
    )
    await callback.answer()

@router_anime.callback_query(F.data.startswith("animes_page_"))
async def animes_page(callback: CallbackQuery):
    page = int(callback.data.replace("animes_page_", ""))
    animes = get_all_animes()
    await callback.message.edit_text(
        f"📺 <b>Barcha animelar ({len(animes)} ta):</b>",
        reply_markup=all_animes_kb(animes, page), parse_mode="HTML"
    )
    await callback.answer()

@router_anime.callback_query(F.data.startswith("anime_"))
async def show_anime_cb(callback: CallbackQuery):
    anime_id = int(callback.data.replace("anime_", ""))
    anime = get_anime_by_id(anime_id)
    if not anime:
        await callback.answer("❌ Anime topilmadi!", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_anime_card(callback.message, anime, callback.from_user.id)
    await callback.answer()

# --- FASLLAR VA QISMLAR ---

@router_anime.callback_query(F.data.startswith("seasons_"))
async def show_seasons(callback: CallbackQuery):
    anime_id = int(callback.data.replace("seasons_", ""))
    anime = get_anime_by_id(anime_id)
    seasons = get_seasons_list(anime_id)
    if not seasons:
        await callback.answer("❌ Hali video qo'shilmagan!", show_alert=True)
        return
    if len(seasons) == 1:
        # 1 ta fasl bo'lsa to'g'ridan qismlarni ko'rsat
        eps = get_episodes_by_season(anime_id, seasons[0])
        is_prem = check_premium(callback.from_user.id)
        await callback.message.answer(
            f"📺 <b>{anime['title']} — {seasons[0]}-Fasl</b>\n\nQismni tanlang:",
            reply_markup=episodes_season_kb(anime_id, seasons[0], eps, is_prem),
            parse_mode="HTML"
        )
    else:
        await callback.message.answer(
            f"📂 <b>{anime['title']}</b>\n\nFaslni tanlang:",
            reply_markup=seasons_kb(anime_id, seasons), parse_mode="HTML"
        )
    await callback.answer()

@router_anime.callback_query(F.data.startswith("season_"))
async def show_season_episodes(callback: CallbackQuery):
    parts = callback.data.replace("season_", "").split("_")
    anime_id = int(parts[0])
    season = int(parts[1])
    anime = get_anime_by_id(anime_id)
    eps = get_episodes_by_season(anime_id, season)
    is_prem = check_premium(callback.from_user.id)
    await callback.message.edit_text(
        f"📺 <b>{anime['title']} — {season}-Fasl</b>\n\nQismni tanlang:",
        reply_markup=episodes_season_kb(anime_id, season, eps, is_prem),
        parse_mode="HTML"
    )
    await callback.answer()

@router_anime.callback_query(F.data.startswith("watch_"))
async def watch_episode(callback: CallbackQuery):
    parts = callback.data.replace("watch_", "").split("_")
    anime_id = int(parts[0])
    season = int(parts[1])
    ep_num = int(parts[2])
    user_id = callback.from_user.id
    is_prem = check_premium(user_id)

    if ep_num > 3 and not is_prem:
        await callback.answer(
            "🔒 Bu qismni ko'rish uchun Aniyoof Pass kerak!\n\n"
            "💎 Premium olish uchun /start bosib Premium bo'limiga kiring.",
            show_alert=True
        )
        return

    anime = get_anime_by_id(anime_id)
    episode = get_episode(anime_id, season, ep_num)
    if not episode:
        await callback.answer("❌ Bu qism hali qo'shilmagan!", show_alert=True)
        return

    record_watch(user_id, anime_id, episode["id"])
    increment_views(anime_id)
    if anime:
        update_favorite_genres(user_id, anime["genre"])

    eps = get_episodes_by_season(anime_id, season)
    nav = []
    if ep_num > 1:
        nav.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"watch_{anime_id}_{season}_{ep_num-1}"))
    if ep_num < len(eps):
        nav.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"watch_{anime_id}_{season}_{ep_num+1}"))

    nav_kb_rows = []
    if nav:
        nav_kb_rows.append(nav)
    nav_kb_rows.append([InlineKeyboardButton(text="📂 Fasllar", callback_data=f"seasons_{anime_id}")])
    nav_kb_rows.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="main_menu")])
    nav_kb = InlineKeyboardMarkup(inline_keyboard=nav_kb_rows)

    caption = f"🎬 <b>{anime['title']}</b> | {season}-Fasl | {ep_num}-Qism"
    try:
        if episode["file_type"] == "video":
            await callback.message.answer_video(
                video=episode["file_id"], caption=caption,
                reply_markup=nav_kb, parse_mode="HTML"
            )
        else:
            await callback.message.answer_document(
                document=episode["file_id"], caption=caption,
                reply_markup=nav_kb, parse_mode="HTML"
            )
    except Exception as e:
        await callback.answer(f"❌ Xato: {e}", show_alert=True)
    await callback.answer()

# --- SEVIMLILAR ---

@router_anime.callback_query(F.data.startswith("fav_"))
async def toggle_favorite_cb(callback: CallbackQuery):
    anime_id = int(callback.data.replace("fav_", ""))
    user_id = callback.from_user.id
    if is_favorite(user_id, anime_id):
        remove_favorite(user_id, anime_id)
        await callback.answer("💔 Sevimlilardan o'chirildi!")
    else:
        add_favorite(user_id, anime_id)
        await callback.answer("❤️ Sevimlilarga qo'shildi!")
    seasons = get_seasons_list(anime_id)
    is_fav = is_favorite(user_id, anime_id)
    try:
        await callback.message.edit_reply_markup(
            reply_markup=anime_card_kb(anime_id, is_fav, has_episodes=len(seasons) > 0)
        )
    except Exception:
        pass

@router_anime.callback_query(F.data == "favorites")
async def show_favorites(callback: CallbackQuery):
    user_id = callback.from_user.id
    favs = get_favorites(user_id)
    if not favs:
        await callback.message.edit_text(
            "❤️ <b>Sevimlilar</b>\n\nHali sevimli animengiz yo'q!",
            reply_markup=back_to_main_kb(), parse_mode="HTML"
        )
        return
    buttons = [
        [InlineKeyboardButton(text=f"❤️ {a['title']}", callback_data=f"anime_{a['id']}")]
        for a in favs
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")])
    await callback.message.edit_text(
        f"❤️ <b>Sevimli animelar ({len(favs)} ta):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )
    await callback.answer()

# --- BAHO BERISH ---

@router_anime.callback_query(F.data.startswith("rate_"))
async def rate_anime_cb(callback: CallbackQuery):
    anime_id = int(callback.data.replace("rate_", ""))
    anime = get_anime_by_id(anime_id)
    await callback.message.answer(
        f"⭐ <b>{anime['title']}</b> ga baho bering (1-10):",
        reply_markup=rating_stars_kb(anime_id), parse_mode="HTML"
    )
    await callback.answer()

@router_anime.callback_query(F.data.startswith("score_"))
async def save_score(callback: CallbackQuery):
    parts = callback.data.replace("score_", "").split("_")
    anime_id = int(parts[0])
    score = int(parts[1])
    rate_anime(callback.from_user.id, anime_id, score)
    avg = get_anime_rating(anime_id)
    await callback.answer(f"✅ Bahoyingiz: {score}/10 saqlandi!\nO'rtacha: {avg}/10", show_alert=True)
    try:
        await callback.message.delete()
    except Exception:
        pass

# --- IZOH ---

@router_anime.callback_query(F.data.startswith("comment_"))
async def add_comment_cb(callback: CallbackQuery, state: FSMContext):
    anime_id = int(callback.data.replace("comment_", ""))
    await state.update_data(comment_anime_id=anime_id)
    comments = get_comments(anime_id)
    text = "💬 <b>Izoh qoldirish</b>\n\n"
    if comments:
        text += "📝 <b>Oxirgi izohlar:</b>\n"
        for c in comments:
            text += f"👤 @{c['tg_username']}: {c['text']}\n"
        text += "\n"
    text += "Izohingizni yozing:"
    await callback.message.answer(text, parse_mode="HTML")
    await state.set_state(SearchStates.comment_text)
    await callback.answer()

@router_anime.message(SearchStates.comment_text)
async def save_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    add_comment(message.from_user.id, data["comment_anime_id"], message.text)
    await state.clear()
    await message.answer("✅ Izohingiz saqlandi!", reply_markup=back_to_main_kb())

@router_anime.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()

# ============================================================
#   PREMIUM HANDLERS
# ============================================================

@router_premium.callback_query(F.data == "premium_menu")
async def premium_menu_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    is_prem = check_premium(user_id)
    if is_prem:
        user = get_user(user_id)
        await callback.message.edit_text(
            f"💎 <b>Aniyoof Pass</b>\n\n✅ Sizda premium faol!\n"
            f"📅 Tugash sanasi: <b>{user['premium_until'][:10]}</b>\n\n"
            f"{PREMIUM_BENEFITS}",
            reply_markup=back_to_main_kb(), parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(
            f"💎 <b>Aniyoof Pass</b>\n\n{PREMIUM_BENEFITS}\n\nQanday olmoqchisiz?",
            reply_markup=premium_menu_kb(), parse_mode="HTML"
        )
    await callback.answer()

@router_premium.callback_query(F.data == "premium_bot")
async def premium_via_bot(callback: CallbackQuery):
    await callback.message.edit_text(
        "💎 <b>Aniyoof Pass narxlari:</b>\n\nTarifni tanlang:",
        reply_markup=premium_tariffs_kb(), parse_mode="HTML"
    )
    await callback.answer()

@router_premium.callback_query(F.data.startswith("buy_"))
async def buy_tariff(callback: CallbackQuery, state: FSMContext):
    tariff = callback.data.replace("buy_", "")
    tariff_data = PREMIUM_PRICES.get(tariff)
    if not tariff_data:
        await callback.answer("❌ Tarif topilmadi!")
        return
    await state.update_data(tariff=tariff)
    profit_text = f"\n✅ <b>{tariff_data['profit']}</b>" if tariff_data["profit"] else ""
    await callback.message.edit_text(
        f"💳 <b>To'lov ma'lumotlari</b>\n\n"
        f"📦 Tarif: <b>{tariff_data['label']}</b>\n"
        f"💰 Narx: <b>{tariff_data['price']:,} so'm</b>{profit_text}\n\n"
        f"💳 Karta raqami:\n<code>{PAYMENT_CARD}</code>\n"
        f"👤 Egasi: <b>{PAYMENT_CARD_OWNER}</b>\n\n"
        f"⚠️ <i>To'lov qilib, chek (screenshot) yuborish uchun quyidagi tugmani bosing</i>",
        reply_markup=payment_confirm_kb(tariff), parse_mode="HTML"
    )
    await callback.answer()

@router_premium.callback_query(F.data.startswith("paid_"))
async def paid_tariff(callback: CallbackQuery, state: FSMContext):
    tariff = callback.data.replace("paid_", "")
    await state.update_data(tariff=tariff)
    await callback.message.edit_text(
        "📸 <b>To'lov cheki</b>\n\nTo'lov cheki (screenshot) rasmini yuboring:",
        parse_mode="HTML"
    )
    await state.set_state(PremiumStates.waiting_screenshot)
    await callback.answer()

@router_premium.message(PremiumStates.waiting_screenshot, F.photo)
async def receive_screenshot(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    tariff = data.get("tariff")
    tariff_data = PREMIUM_PRICES.get(tariff)
    screenshot_id = message.photo[-1].file_id
    user_id = message.from_user.id
    user = get_user(user_id)
    tx_id = add_transaction(user_id, tariff, tariff_data["price"], screenshot_id)
    await message.answer(
        "✅ <b>To'lov cheki qabul qilindi!</b>\n\n"
        "⏳ Admin tasdiqlashini kuting (odatda 1-24 soat ichida).",
        reply_markup=back_to_main_kb(), parse_mode="HTML"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=screenshot_id,
                caption=(
                    f"💳 <b>Yangi to'lov so'rovi!</b>\n\n"
                    f"👤 User: @{user['tg_username']}\n"
                    f"🆔 ID: {user_id}\n"
                    f"📦 Tarif: {tariff_data['label']}\n"
                    f"💰 Summa: {tariff_data['price']:,} so'm\n"
                    f"🔢 Tranzaksiya: #{tx_id}"
                ),
                reply_markup=admin_approve_kb(user_id, tx_id, tariff),
                parse_mode="HTML"
            )
        except Exception:
            pass
    await state.clear()

@router_premium.message(PremiumStates.waiting_screenshot)
async def wrong_screenshot(message: Message):
    await message.answer("📸 Iltimos, <b>screenshot rasm</b> yuboring!", parse_mode="HTML")

@router_premium.callback_query(F.data == "premium_admin")
async def premium_via_admin(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Adminga yozish", url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="premium_menu")]
    ])
    await callback.message.edit_text(
        f"👤 <b>Admin orqali olish</b>\n\nAdmin: {ADMIN_USERNAME}\n\n"
        f"📝 Quyidagi ma'lumotlarni yuboring:\n1. Istalgan tarif\n2. To'lov cheki",
        reply_markup=kb, parse_mode="HTML"
    )
    await callback.answer()

# ============================================================
#   RATING HANDLERS
# ============================================================

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

def format_rating(rows, title):
    if not rows:
        return f"🏆 <b>{title}</b>\n\n😔 Hali hech kim yo'q!"
    text = f"🏆 <b>{title}</b>\n\n"
    for i, row in enumerate(rows, 1):
        medal = MEDALS.get(i, f"{i}.")
        text += f"{medal} @{row['tg_username']} — {row['count']} ta qism\n"
    return text

@router_rating.callback_query(F.data == "rating_menu")
async def rating_menu_cb(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏆 <b>Reyting tizimi</b>\n\nEng ko'p anime ko'rganlar:",
        reply_markup=rating_menu_kb(), parse_mode="HTML"
    )
    await callback.answer()

@router_rating.callback_query(F.data == "rating_daily")
async def rating_daily(callback: CallbackQuery):
    rows = get_top_rating("daily", 10)
    await callback.message.edit_text(format_rating(rows, "Kunlik Reyting"), reply_markup=rating_menu_kb(), parse_mode="HTML")
    await callback.answer()

@router_rating.callback_query(F.data == "rating_weekly")
async def rating_weekly(callback: CallbackQuery):
    rows = get_top_rating("weekly", 10)
    await callback.message.edit_text(format_rating(rows, "Haftalik Reyting"), reply_markup=rating_menu_kb(), parse_mode="HTML")
    await callback.answer()

@router_rating.callback_query(F.data == "rating_monthly")
async def rating_monthly(callback: CallbackQuery):
    rows = get_top_rating("monthly", 10)
    await callback.message.edit_text(format_rating(rows, "Oylik Reyting"), reply_markup=rating_menu_kb(), parse_mode="HTML")
    await callback.answer()

@router_rating.callback_query(F.data == "rating_all_time")
async def rating_all_time(callback: CallbackQuery):
    rows = get_top_rating("all_time", 10)
    await callback.message.edit_text(format_rating(rows, "Barcha Vaqt Reytingi 🏆"), reply_markup=rating_menu_kb(), parse_mode="HTML")
    await callback.answer()

# ============================================================
#   MAIN
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router_start)
    dp.include_router(router_admin)
    dp.include_router(router_anime)
    dp.include_router(router_premium)
    dp.include_router(router_rating)
    start_scheduler()
    logging.info("🤖 Aniyoof Bot ishga tushdi!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())