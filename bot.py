import subprocess
subprocess.run(["pip", "install", "python-telegram-bot==21.6"], check=True)

"""
╔══════════════════════════════════════════════╗
║         CORVUS AREA BOT — TAM SÜRÜM          ║
║  Rose Bot yetkinlikleri + Özel Puan Sistemi  ║
╚══════════════════════════════════════════════╝
"""

import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta
from functools import wraps
from telegram import Update, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ChatMemberHandler, ContextTypes, filters, CallbackQueryHandler
)
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════
# AYARLAR
# ══════════════════════════════════════════════
BOT_TOKEN    = "8807959815:AAFNbPD_WiD-i1I6i5dXsKjEWt7VlutmT-s"     # @BotFather'dan al
ADMIN_IDS    = [1240615302]
LOG_CHAT_ID  = None                   # Log kanalı ID (opsiyonel, örn: -100123456)

MESAJ_PUANI         = 2
DAVET_PUANI         = 10
MIN_MESAJ_UZUNLUGU  = 10
COOLDOWN_SANIYE     = 15              # 15 saniye cooldown

SPAM_KELIMELER = {
    "selam","merhaba","sa","hey","hi","hello","slm","günaydın","gunaydin",
    "iyi günler","iyi akşamlar","iyi geceler","kolay gelsin","hayırlı","hayirli",
    "nasılsın","nasilsin","nasılsınız","nasıl gidiyor","iyiyim","iyi","süper",
    "harika","fena değil","idare eder","ok","tamam","tamamdır","anladım",
    "anlıyorum","evet","hayır","ee","hm","hmm","+1","lol","haha",
    "teşekkürler","tesekkurler","teşekkür","sağ ol","sag ol","eyw",
}

DB_PATH = "corvus.db"

# ══════════════════════════════════════════════
# VERİTABANI
# ══════════════════════════════════════════════
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            full_name   TEXT,
            puan        INTEGER DEFAULT 0,
            mesaj_sayisi INTEGER DEFAULT 0,
            davet_sayisi INTEGER DEFAULT 0,
            son_mesaj   TEXT
        );
        CREATE TABLE IF NOT EXISTS warns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER,
            user_id     INTEGER,
            reason      TEXT,
            tarih       TEXT
        );
        CREATE TABLE IF NOT EXISTS notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER,
            anahtar     TEXT,
            icerik      TEXT,
            UNIQUE(chat_id, anahtar)
        );
        CREATE TABLE IF NOT EXISTS filters (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER,
            kelime      TEXT,
            yanit       TEXT,
            UNIQUE(chat_id, kelime)
        );
        CREATE TABLE IF NOT EXISTS settings (
            chat_id     INTEGER,
            anahtar     TEXT,
            deger       TEXT,
            PRIMARY KEY(chat_id, anahtar)
        );
        CREATE TABLE IF NOT EXISTS blocklist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER,
            kelime      TEXT,
            UNIQUE(chat_id, kelime)
        );
        CREATE TABLE IF NOT EXISTS locks (
            chat_id     INTEGER,
            lock_turu   TEXT,
            PRIMARY KEY(chat_id, lock_turu)
        );
        """)

def ayar_al(chat_id: int, anahtar: str, varsayilan: str = None):
    with db() as c:
        r = c.execute("SELECT deger FROM settings WHERE chat_id=? AND anahtar=?",
                      (chat_id, anahtar)).fetchone()
        return r["deger"] if r else varsayilan

def ayar_yaz(chat_id: int, anahtar: str, deger: str):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings(chat_id,anahtar,deger) VALUES(?,?,?)",
                  (chat_id, anahtar, deger))

def kullanici_al_veya_olustur(user_id, username, full_name):
    with db() as c:
        c.execute("""
            INSERT INTO users(user_id,username,full_name)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name
        """, (user_id, username or full_name, full_name))
        return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

# ══════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ══════════════════════════════════════════════
def is_admin(user_id: int):
    return user_id in ADMIN_IDS

async def chat_admin_mi(update: Update, user_id: int) -> bool:
    if is_admin(user_id):
        return True
    admins = await update.effective_chat.get_administrators()
    return any(a.user.id == user_id for a in admins)

def admin_gerekli(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not await chat_admin_mi(update, user.id):
            await update.message.reply_text("🚫 Bu komut sadece yöneticilere özeldir.")
            return
        return await func(update, context)
    return wrapper

def sure_parse(sure_str: str):
    """'2h', '3d', '30m', '1w' → timedelta"""
    m = re.match(r"^(\d+)([mhdw])$", sure_str.lower())
    if not m:
        return None
    sayi, birim = int(m.group(1)), m.group(2)
    return {
        "m": timedelta(minutes=sayi),
        "h": timedelta(hours=sayi),
        "d": timedelta(days=sayi),
        "w": timedelta(weeks=sayi),
    }[birim]

def sure_str(td: timedelta) -> str:
    sn = int(td.total_seconds())
    if sn < 3600:   return f"{sn//60} dakika"
    if sn < 86400:  return f"{sn//3600} saat"
    return f"{sn//86400} gün"

async def log_gonder(context: ContextTypes.DEFAULT_TYPE, metin: str):
    if LOG_CHAT_ID:
        try:
            await context.bot.send_message(LOG_CHAT_ID, metin, parse_mode=ParseMode.MARKDOWN)
        except:
            pass

def spam_mi(metin: str) -> bool:
    if not metin:
        return True
    temiz = metin.strip().lower()
    if len(temiz) < MIN_MESAJ_UZUNLUGU:
        return True
    if temiz in SPAM_KELIMELER:
        return True
    if all(not c.isalpha() for c in temiz):
        return True
    return False

def kilitli_mi(chat_id: int, lock_turu: str) -> bool:
    with db() as c:
        r = c.execute("SELECT 1 FROM locks WHERE chat_id=? AND lock_turu=?",
                      (chat_id, lock_turu)).fetchone()
        return bool(r)

# ══════════════════════════════════════════════
# PUAN SİSTEMİ
# ══════════════════════════════════════════════
async def mesaj_sayici(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    metin = update.message.text or update.message.caption or ""

    # Blocklist kontrolü
    chat_id = update.effective_chat.id
    with db() as c:
        bl = c.execute("SELECT kelime FROM blocklist WHERE chat_id=?", (chat_id,)).fetchall()
    for row in bl:
        if row["kelime"].lower() in metin.lower():
            await update.message.delete()
            return

    # Filtre kontrolü
    with db() as c:
        flt = c.execute("SELECT * FROM filters WHERE chat_id=?", (chat_id,)).fetchall()
    for row in flt:
        if row["kelime"].lower() in metin.lower():
            await update.message.reply_text(row["yanit"])

    # Antiflood kontrolü
    flood_limit = ayar_al(chat_id, "flood_limit")
    if flood_limit:
        key = f"flood_{chat_id}_{user.id}"
        now = datetime.utcnow()
        history = context.chat_data.get(key, [])
        history = [t for t in history if (now - t).total_seconds() < 10]
        history.append(now)
        context.chat_data[key] = history
        if len(history) > int(flood_limit):
            await update.message.delete()
            try:
                until = datetime.utcnow() + timedelta(minutes=5)
                await context.bot.restrict_chat_member(
                    chat_id, user.id,
                    ChatPermissions(can_send_messages=False),
                    until_date=until
                )
                await update.effective_chat.send_message(
                    f"⚠️ *{user.full_name}* flood yaptığı için 5 dakika susturuldu.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
            return

    if spam_mi(metin):
        return

    row = kullanici_al_veya_olustur(user.id, user.username, user.full_name)
    son = row["son_mesaj"]
    if son:
        fark = datetime.utcnow() - datetime.fromisoformat(son)
        if fark < timedelta(seconds=COOLDOWN_SANIYE):
            return

    with db() as c:
        c.execute("""
            UPDATE users SET puan=puan+?, mesaj_sayisi=mesaj_sayisi+1, son_mesaj=?
            WHERE user_id=?
        """, (MESAJ_PUANI, datetime.utcnow().isoformat(), user.id))

async def yeni_uye_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if result.new_chat_member.status not in ("member", "administrator", "creator"):
        return
    if result.old_chat_member.status in ("member", "administrator", "creator", "restricted"):
        return

    davet_eden = result.from_user
    yeni_uye   = result.new_chat_member.user
    chat_id    = result.chat.id

    # Karşılama mesajı
    hosgeldin = ayar_al(chat_id, "welcome")
    if hosgeldin:
        metin = hosgeldin.replace("{name}", f"[{yeni_uye.full_name}](tg://user?id={yeni_uye.id})")
        try:
            await context.bot.send_message(chat_id, metin, parse_mode=ParseMode.MARKDOWN)
        except:
            pass

    if not davet_eden or davet_eden.is_bot or davet_eden.id == yeni_uye.id:
        return

    kullanici_al_veya_olustur(davet_eden.id, davet_eden.username, davet_eden.full_name)
    with db() as c:
        c.execute("UPDATE users SET puan=puan+?, davet_sayisi=davet_sayisi+1 WHERE user_id=?",
                  (DAVET_PUANI, davet_eden.id))

    await context.bot.send_message(
        chat_id,
        f"👋 *{yeni_uye.full_name}* gruba katıldı!\n"
        f"Davet eden: *{davet_eden.full_name}* → *+{DAVET_PUANI} puan* 🎉",
        parse_mode=ParseMode.MARKDOWN
    )

async def uye_ayrildi_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if result.new_chat_member.status not in ("left", "kicked"):
        return
    if result.old_chat_member.status not in ("member", "administrator", "creator", "restricted"):
        return

    chat_id = result.chat.id
    veda = ayar_al(chat_id, "goodbye")
    if veda:
        user = result.new_chat_member.user
        metin = veda.replace("{name}", f"*{user.full_name}*")
        try:
            await context.bot.send_message(chat_id, metin, parse_mode=ParseMode.MARKDOWN)
        except:
            pass

# ══════════════════════════════════════════════
# PUAN KOMUTLARI
# ══════════════════════════════════════════════
async def puan_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    kullanici_al_veya_olustur(user.id, user.username, user.full_name)
    with db() as c:
        k = c.execute("SELECT * FROM users WHERE user_id=?", (user.id,)).fetchone()
    await update.message.reply_text(
        f"📊 *{k['full_name']}* istatistikleri\n\n"
        f"⭐ Toplam Puan: *{k['puan']}*\n"
        f"💬 Mesaj Puanı: *{k['mesaj_sayisi'] * MESAJ_PUANI}* ({k['mesaj_sayisi']} mesaj)\n"
        f"👥 Davet Puanı: *{k['davet_sayisi'] * DAVET_PUANI}* ({k['davet_sayisi']} davet)",
        parse_mode=ParseMode.MARKDOWN
    )

async def siralama_genel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows = c.execute("SELECT * FROM users ORDER BY puan DESC LIMIT 10").fetchall()
    if not rows:
        await update.message.reply_text("Henüz puan yok.")
        return
    madalya = ["🥇","🥈","🥉"]
    metin = "🏆 *GENEL SIRALAMA* (Toplam Puan)\n\n"
    for i, k in enumerate(rows, 1):
        m = madalya[i-1] if i <= 3 else f"{i}."
        metin += f"{m} {k['full_name']} — *{k['puan']} puan*\n"
    await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)

async def siralama_mesaj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows = c.execute("SELECT * FROM users ORDER BY mesaj_sayisi DESC LIMIT 10").fetchall()
    if not rows:
        await update.message.reply_text("Henüz mesaj yok.")
        return
    madalya = ["🥇","🥈","🥉"]
    metin = "💬 *AKTİFLİK SIRALAMASI* (Mesaj Sayısı)\n\n"
    for i, k in enumerate(rows, 1):
        m = madalya[i-1] if i <= 3 else f"{i}."
        metin += f"{m} {k['full_name']} — *{k['mesaj_sayisi']}* mesaj (+{k['mesaj_sayisi']*MESAJ_PUANI} puan)\n"
    await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)

async def siralama_davet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows = c.execute("SELECT * FROM users ORDER BY davet_sayisi DESC LIMIT 10").fetchall()
    if not rows:
        await update.message.reply_text("Henüz davet yok.")
        return
    madalya = ["🥇","🥈","🥉"]
    metin = "👥 *DAVETÇİ SIRALAMASI*\n\n"
    for i, k in enumerate(rows, 1):
        m = madalya[i-1] if i <= 3 else f"{i}."
        metin += f"{m} {k['full_name']} — *{k['davet_sayisi']}* davet (+{k['davet_sayisi']*DAVET_PUANI} puan)\n"
    await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def puan_ver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Bir mesajı yanıtlayarak kullan: `/puanver 20`", parse_mode=ParseMode.MARKDOWN)
        return
    miktar = int(context.args[0]) if context.args else 10
    hedef  = update.message.reply_to_message.from_user
    kullanici_al_veya_olustur(hedef.id, hedef.username, hedef.full_name)
    with db() as c:
        c.execute("UPDATE users SET puan=puan+? WHERE user_id=?", (miktar, hedef.id))
        k = c.execute("SELECT puan FROM users WHERE user_id=?", (hedef.id,)).fetchone()
    await update.message.reply_text(
        f"✅ *{hedef.full_name}* → *+{miktar} puan*\nToplam: *{k['puan']}*",
        parse_mode=ParseMode.MARKDOWN
    )

# ══════════════════════════════════════════════
# MODERASYON — BAN / MUTE / KICK
# ══════════════════════════════════════════════
def hedef_al(update: Update, context):
    """Reply veya args'tan hedef user_id döndür"""
    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        sebep = " ".join(context.args) if context.args else ""
        return u.id, u.full_name, sebep
    if context.args:
        uid_str = context.args[0]
        sebep   = " ".join(context.args[1:])
        try:
            return int(uid_str), uid_str, sebep
        except ValueError:
            uname = uid_str.lstrip("@")
            return uname, uname, sebep
    return None, None, ""

@admin_gerekli
async def ban_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, sebep = hedef_al(update, context)
    if not uid:
        await update.message.reply_text("Bir kullanıcıyı yanıtla veya ID/kullanıcı adı ver.")
        return
    chat = update.effective_chat
    try:
        await chat.ban_member(uid)
        metin = f"🚫 *{isim}* banlandı."
        if sebep: metin += f"\nSebep: {sebep}"
        await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)
        await log_gonder(context, f"🚫 BAN | {isim} | {chat.title} | Sebep: {sebep or '-'}")
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {e}")

@admin_gerekli
async def tban_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, _ = hedef_al(update, context)
    if not uid or not context.args:
        await update.message.reply_text("Kullanım: `/tban @user 2h sebep`", parse_mode=ParseMode.MARKDOWN)
        return
    sure_arg = context.args[1] if update.message.reply_to_message else context.args[1] if len(context.args) > 1 else context.args[0]
    # reply ise args[0] süre, yoksa args[1]
    if update.message.reply_to_message:
        sure_arg = context.args[0] if context.args else None
        sebep    = " ".join(context.args[1:])
    else:
        sure_arg = context.args[1] if len(context.args) > 1 else None
        sebep    = " ".join(context.args[2:])
    if not sure_arg:
        await update.message.reply_text("Süre belirtmelisin. Örn: `2h`, `3d`, `30m`", parse_mode=ParseMode.MARKDOWN)
        return
    td = sure_parse(sure_arg)
    if not td:
        await update.message.reply_text("Geçersiz süre. Kullan: `30m`, `2h`, `3d`, `1w`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        until = datetime.utcnow() + td
        await update.effective_chat.ban_member(uid, until_date=until)
        metin = f"⏱ *{isim}* geçici banlandı ({sure_str(td)})."
        if sebep: metin += f"\nSebep: {sebep}"
        await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

@admin_gerekli
async def dban_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Banlanacak mesajı yanıtla.")
        return
    u = update.message.reply_to_message.from_user
    sebep = " ".join(context.args) if context.args else ""
    try:
        await update.message.reply_to_message.delete()
        await update.effective_chat.ban_member(u.id)
        metin = f"🗑 *{u.full_name}* mesajı silindi ve banlandı."
        if sebep: metin += f"\nSebep: {sebep}"
        await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

@admin_gerekli
async def sban_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, sebep = hedef_al(update, context)
    if not uid:
        await update.message.reply_text("Bir kullanıcıyı yanıtla.")
        return
    try:
        if update.message.reply_to_message:
            await update.message.reply_to_message.delete()
        await update.message.delete()
        await update.effective_chat.ban_member(uid)
        # Sessiz — bildirim yok
    except:
        pass

@admin_gerekli
async def unban_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, _ = hedef_al(update, context)
    if not uid:
        await update.message.reply_text("Kullanıcı belirt.")
        return
    try:
        await update.effective_chat.unban_member(uid)
        await update.message.reply_text(f"✅ *{isim}* banı kaldırıldı.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

@admin_gerekli
async def mute_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, sebep = hedef_al(update, context)
    if not uid:
        await update.message.reply_text("Bir kullanıcıyı yanıtla.")
        return
    try:
        await update.effective_chat.restrict_member(uid, ChatPermissions(can_send_messages=False))
        metin = f"🔇 *{isim}* susturuldu."
        if sebep: metin += f"\nSebep: {sebep}"
        await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)
        await log_gonder(context, f"🔇 MUTE | {isim} | {update.effective_chat.title}")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

@admin_gerekli
async def tmute_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, _ = hedef_al(update, context)
    if not uid:
        await update.message.reply_text("Kullanım: `/tmute @user 2h`", parse_mode=ParseMode.MARKDOWN)
        return
    if update.message.reply_to_message:
        sure_arg = context.args[0] if context.args else None
    else:
        sure_arg = context.args[1] if len(context.args) > 1 else None
    if not sure_arg:
        await update.message.reply_text("Süre belirt: `30m`, `2h`, `3d`", parse_mode=ParseMode.MARKDOWN)
        return
    td = sure_parse(sure_arg)
    if not td:
        await update.message.reply_text("Geçersiz süre.", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        until = datetime.utcnow() + td
        await update.effective_chat.restrict_member(
            uid, ChatPermissions(can_send_messages=False), until_date=until
        )
        await update.message.reply_text(
            f"🔇 *{isim}* {sure_str(td)} süreyle susturuldu.", parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

@admin_gerekli
async def unmute_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, _ = hedef_al(update, context)
    if not uid:
        await update.message.reply_text("Kullanıcı belirt.")
        return
    try:
        await update.effective_chat.restrict_member(uid, ChatPermissions(
            can_send_messages=True, can_send_media_messages=True,
            can_send_polls=True, can_send_other_messages=True
        ))
        await update.message.reply_text(f"🔊 *{isim}* susturması kaldırıldı.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

@admin_gerekli
async def kick_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, sebep = hedef_al(update, context)
    if not uid:
        await update.message.reply_text("Bir kullanıcıyı yanıtla.")
        return
    try:
        await update.effective_chat.ban_member(uid)
        await update.effective_chat.unban_member(uid)  # kick = ban + hemen unban
        metin = f"👢 *{isim}* gruptan atıldı."
        if sebep: metin += f"\nSebep: {sebep}"
        await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

# ══════════════════════════════════════════════
# UYARILAR (WARNINGS)
# ══════════════════════════════════════════════
@admin_gerekli
async def warn_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, sebep = hedef_al(update, context)
    chat_id = update.effective_chat.id
    if not uid:
        await update.message.reply_text("Bir kullanıcıyı yanıtla.")
        return
    if not isinstance(uid, int):
        await update.message.reply_text("Geçerli bir kullanıcı belirt.")
        return
    with db() as c:
        c.execute("INSERT INTO warns(chat_id,user_id,reason,tarih) VALUES(?,?,?,?)",
                  (chat_id, uid, sebep or "-", datetime.utcnow().isoformat()))
        sayi = c.execute("SELECT COUNT(*) as cnt FROM warns WHERE chat_id=? AND user_id=?",
                         (chat_id, uid)).fetchone()["cnt"]
    limit = int(ayar_al(chat_id, "warn_limit") or 3)
    mod   = ayar_al(chat_id, "warn_mode") or "mute"
    metin = f"⚠️ *{isim}* uyarıldı. ({sayi}/{limit})"
    if sebep: metin += f"\nSebep: {sebep}"
    if sayi >= limit:
        metin += f"\n\n🔴 Uyarı limitine ulaşıldı! Eylem: *{mod}*"
        if mod == "ban":
            await update.effective_chat.ban_member(uid)
        elif mod == "kick":
            await update.effective_chat.ban_member(uid)
            await update.effective_chat.unban_member(uid)
        else:  # mute (varsayılan)
            await update.effective_chat.restrict_member(uid, ChatPermissions(can_send_messages=False))
        with db() as c:
            c.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, uid))
    await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def unwarn_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, _ = hedef_al(update, context)
    chat_id = update.effective_chat.id
    if not uid or not isinstance(uid, int):
        await update.message.reply_text("Bir kullanıcıyı yanıtla.")
        return
    with db() as c:
        last = c.execute("SELECT id FROM warns WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                         (chat_id, uid)).fetchone()
        if last:
            c.execute("DELETE FROM warns WHERE id=?", (last["id"],))
            sayi = c.execute("SELECT COUNT(*) as cnt FROM warns WHERE chat_id=? AND user_id=?",
                             (chat_id, uid)).fetchone()["cnt"]
            await update.message.reply_text(
                f"✅ *{isim}* son uyarısı silindi. Kalan: {sayi}", parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(f"*{isim}* için uyarı bulunamadı.", parse_mode=ParseMode.MARKDOWN)

async def warns_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, isim, _ = hedef_al(update, context)
    chat_id = update.effective_chat.id
    if not uid or not isinstance(uid, int):
        uid  = update.effective_user.id
        isim = update.effective_user.full_name
    with db() as c:
        rows = c.execute("SELECT * FROM warns WHERE chat_id=? AND user_id=? ORDER BY tarih DESC",
                         (chat_id, uid)).fetchall()
    if not rows:
        await update.message.reply_text(f"*{isim}* için uyarı yok.", parse_mode=ParseMode.MARKDOWN)
        return
    limit = ayar_al(chat_id, "warn_limit") or 3
    metin = f"⚠️ *{isim}* — {len(rows)}/{limit} uyarı\n\n"
    for i, r in enumerate(rows, 1):
        metin += f"{i}. {r['reason']} — {r['tarih'][:10]}\n"
    await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def warnlimit_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        limit = ayar_al(update.effective_chat.id, "warn_limit") or 3
        await update.message.reply_text(f"Mevcut uyarı limiti: *{limit}*", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        yeni = int(context.args[0])
        ayar_yaz(update.effective_chat.id, "warn_limit", str(yeni))
        await update.message.reply_text(f"✅ Uyarı limiti *{yeni}* olarak ayarlandı.", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("Geçerli bir sayı gir.")

@admin_gerekli
async def warnmode_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0] not in ("ban","kick","mute"):
        await update.message.reply_text("Kullanım: `/warnmode ban|kick|mute`", parse_mode=ParseMode.MARKDOWN)
        return
    ayar_yaz(update.effective_chat.id, "warn_mode", context.args[0])
    await update.message.reply_text(f"✅ Uyarı modu: *{context.args[0]}*", parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════
# NOTLAR
# ══════════════════════════════════════════════
@admin_gerekli
async def save_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Kullanım: `/save notadi içerik`", parse_mode=ParseMode.MARKDOWN)
        return
    anahtar = context.args[0].lower()
    icerik  = " ".join(context.args[1:])
    chat_id = update.effective_chat.id
    with db() as c:
        c.execute("INSERT OR REPLACE INTO notes(chat_id,anahtar,icerik) VALUES(?,?,?)",
                  (chat_id, anahtar, icerik))
    await update.message.reply_text(f"✅ Not kaydedildi: *{anahtar}*", parse_mode=ParseMode.MARKDOWN)

async def get_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/get notadi`", parse_mode=ParseMode.MARKDOWN)
        return
    anahtar = context.args[0].lower()
    chat_id = update.effective_chat.id
    with db() as c:
        r = c.execute("SELECT icerik FROM notes WHERE chat_id=? AND anahtar=?",
                      (chat_id, anahtar)).fetchone()
    if r:
        await update.message.reply_text(r["icerik"], parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ `{anahtar}` adında not bulunamadı.", parse_mode=ParseMode.MARKDOWN)

async def notes_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with db() as c:
        rows = c.execute("SELECT anahtar FROM notes WHERE chat_id=? ORDER BY anahtar",
                         (chat_id,)).fetchall()
    if not rows:
        await update.message.reply_text("Bu grupta kayıtlı not yok.")
        return
    liste = ", ".join(f"`{r['anahtar']}`" for r in rows)
    await update.message.reply_text(f"📋 Notlar: {liste}", parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def clearnote_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/clearnote notadi`", parse_mode=ParseMode.MARKDOWN)
        return
    anahtar = context.args[0].lower()
    chat_id = update.effective_chat.id
    with db() as c:
        c.execute("DELETE FROM notes WHERE chat_id=? AND anahtar=?", (chat_id, anahtar))
    await update.message.reply_text(f"🗑 `{anahtar}` notu silindi.", parse_mode=ParseMode.MARKDOWN)

async def hashtag_not_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """#notadi ile not çağır"""
    if not update.message or not update.message.text:
        return
    m = re.search(r"#(\w+)", update.message.text)
    if not m:
        return
    anahtar = m.group(1).lower()
    chat_id = update.effective_chat.id
    with db() as c:
        r = c.execute("SELECT icerik FROM notes WHERE chat_id=? AND anahtar=?",
                      (chat_id, anahtar)).fetchone()
    if r:
        await update.message.reply_text(r["icerik"], parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════
# FİLTRELER
# ══════════════════════════════════════════════
@admin_gerekli
async def filter_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Kullanım: `/filter kelime yanıt`", parse_mode=ParseMode.MARKDOWN)
        return
    kelime = context.args[0].lower()
    yanit  = " ".join(context.args[1:])
    chat_id = update.effective_chat.id
    with db() as c:
        c.execute("INSERT OR REPLACE INTO filters(chat_id,kelime,yanit) VALUES(?,?,?)",
                  (chat_id, kelime, yanit))
    await update.message.reply_text(f"✅ Filtre eklendi: `{kelime}`", parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def stop_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/stop kelime`", parse_mode=ParseMode.MARKDOWN)
        return
    kelime  = context.args[0].lower()
    chat_id = update.effective_chat.id
    with db() as c:
        c.execute("DELETE FROM filters WHERE chat_id=? AND kelime=?", (chat_id, kelime))
    await update.message.reply_text(f"🗑 Filtre silindi: `{kelime}`", parse_mode=ParseMode.MARKDOWN)

async def filters_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with db() as c:
        rows = c.execute("SELECT kelime FROM filters WHERE chat_id=? ORDER BY kelime",
                         (chat_id,)).fetchall()
    if not rows:
        await update.message.reply_text("Aktif filtre yok.")
        return
    liste = ", ".join(f"`{r['kelime']}`" for r in rows)
    await update.message.reply_text(f"🔍 Filtreler: {liste}", parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════
# KURALLAR
# ══════════════════════════════════════════════
async def rules_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    kurallar = ayar_al(chat_id, "rules")
    if kurallar:
        await update.message.reply_text(f"📜 *Grup Kuralları*\n\n{kurallar}", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("Bu grup için kural belirlenmemiş.")

@admin_gerekli
async def setrules_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/setrules kural metni`", parse_mode=ParseMode.MARKDOWN)
        return
    kurallar = " ".join(context.args)
    ayar_yaz(update.effective_chat.id, "rules", kurallar)
    await update.message.reply_text("✅ Kurallar güncellendi.", parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def clearrules_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        c.execute("DELETE FROM settings WHERE chat_id=? AND anahtar='rules'",
                  (update.effective_chat.id,))
    await update.message.reply_text("🗑 Kurallar silindi.")

# ══════════════════════════════════════════════
# KİLİTLER (LOCKS)
# ══════════════════════════════════════════════
LOCK_TURLERI = {
    "link":     "link içeren mesajlar",
    "forward":  "yönlendirilen mesajlar",
    "sticker":  "sticker mesajlar",
    "gif":      "GIF mesajlar",
    "photo":    "fotoğraf mesajlar",
    "video":    "video mesajlar",
    "audio":    "ses dosyaları",
    "document": "döküman dosyaları",
    "voice":    "sesli mesajlar",
    "poll":     "anket mesajlar",
    "bot":      "bot mesajları",
}

@admin_gerekli
async def lock_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0] not in LOCK_TURLERI:
        liste = ", ".join(f"`{k}`" for k in LOCK_TURLERI)
        await update.message.reply_text(f"Kullanım: `/lock tür`\nMevcut türler: {liste}", parse_mode=ParseMode.MARKDOWN)
        return
    tur     = context.args[0]
    chat_id = update.effective_chat.id
    with db() as c:
        c.execute("INSERT OR IGNORE INTO locks(chat_id,lock_turu) VALUES(?,?)", (chat_id, tur))
    await update.message.reply_text(f"🔒 `{tur}` kilitlendi.", parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def unlock_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/unlock tür`", parse_mode=ParseMode.MARKDOWN)
        return
    tur     = context.args[0]
    chat_id = update.effective_chat.id
    with db() as c:
        c.execute("DELETE FROM locks WHERE chat_id=? AND lock_turu=?", (chat_id, tur))
    await update.message.reply_text(f"🔓 `{tur}` kilidi kaldırıldı.", parse_mode=ParseMode.MARKDOWN)

async def locks_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with db() as c:
        rows = c.execute("SELECT lock_turu FROM locks WHERE chat_id=?", (chat_id,)).fetchall()
    if not rows:
        await update.message.reply_text("Aktif kilit yok.")
        return
    liste = "\n".join(f"🔒 `{r['lock_turu']}` — {LOCK_TURLERI.get(r['lock_turu'], '')}" for r in rows)
    await update.message.reply_text(f"*Aktif Kilitler:*\n{liste}", parse_mode=ParseMode.MARKDOWN)

async def kilit_kontrol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mesajları kilit kurallarına göre sil"""
    if not update.message:
        return
    chat_id = update.effective_chat.id
    msg     = update.message

    async def sil(sebep: str):
        try:
            await msg.delete()
        except:
            pass

    if kilitli_mi(chat_id, "link"):
        if msg.text and re.search(r"(https?://|t\.me/|www\.)", msg.text or ""):
            await sil("link"); return
    if kilitli_mi(chat_id, "forward") and msg.forward_origin:
        await sil("forward"); return
    if kilitli_mi(chat_id, "sticker") and msg.sticker:
        await sil("sticker"); return
    if kilitli_mi(chat_id, "gif") and msg.animation:
        await sil("gif"); return
    if kilitli_mi(chat_id, "photo") and msg.photo:
        await sil("photo"); return
    if kilitli_mi(chat_id, "video") and msg.video:
        await sil("video"); return
    if kilitli_mi(chat_id, "audio") and msg.audio:
        await sil("audio"); return
    if kilitli_mi(chat_id, "document") and msg.document:
        await sil("document"); return
    if kilitli_mi(chat_id, "voice") and msg.voice:
        await sil("voice"); return
    if kilitli_mi(chat_id, "poll") and msg.poll:
        await sil("poll"); return

# ══════════════════════════════════════════════
# BLOCKLİST
# ══════════════════════════════════════════════
@admin_gerekli
async def addbl_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/addbl kelime`", parse_mode=ParseMode.MARKDOWN)
        return
    kelime  = " ".join(context.args).lower()
    chat_id = update.effective_chat.id
    with db() as c:
        c.execute("INSERT OR IGNORE INTO blocklist(chat_id,kelime) VALUES(?,?)", (chat_id, kelime))
    await update.message.reply_text(f"✅ Blocklist'e eklendi: `{kelime}`", parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def rmbl_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/rmbl kelime`", parse_mode=ParseMode.MARKDOWN)
        return
    kelime  = " ".join(context.args).lower()
    chat_id = update.effective_chat.id
    with db() as c:
        c.execute("DELETE FROM blocklist WHERE chat_id=? AND kelime=?", (chat_id, kelime))
    await update.message.reply_text(f"🗑 Blocklist'ten silindi: `{kelime}`", parse_mode=ParseMode.MARKDOWN)

async def bl_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with db() as c:
        rows = c.execute("SELECT kelime FROM blocklist WHERE chat_id=? ORDER BY kelime",
                         (chat_id,)).fetchall()
    if not rows:
        await update.message.reply_text("Blocklist boş.")
        return
    liste = "\n".join(f"• `{r['kelime']}`" for r in rows)
    await update.message.reply_text(f"🚫 *Blocklist:*\n{liste}", parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════
# KARŞILAMA & VEDA
# ══════════════════════════════════════════════
@admin_gerekli
async def setwelcome_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Kullanım: `/setwelcome Merhaba {name}, gruba hoş geldin!`\n`{name}` → yeni üyenin adı",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    metin = " ".join(context.args)
    ayar_yaz(update.effective_chat.id, "welcome", metin)
    await update.message.reply_text("✅ Karşılama mesajı ayarlandı.", parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def setgoodbye_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/setgoodbye {name} gruptan ayrıldı.`", parse_mode=ParseMode.MARKDOWN)
        return
    metin = " ".join(context.args)
    ayar_yaz(update.effective_chat.id, "goodbye", metin)
    await update.message.reply_text("✅ Veda mesajı ayarlandı.", parse_mode=ParseMode.MARKDOWN)

@admin_gerekli
async def welcome_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.args and context.args[0].lower() == "off":
        with db() as c:
            c.execute("DELETE FROM settings WHERE chat_id=? AND anahtar='welcome'", (chat_id,))
        await update.message.reply_text("🔕 Karşılama mesajı kapatıldı.")
    else:
        mevcut = ayar_al(chat_id, "welcome") or "Ayarlanmamış"
        await update.message.reply_text(f"Mevcut karşılama:\n`{mevcut}`", parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════
# ANTİFLOOD
# ══════════════════════════════════════════════
@admin_gerekli
async def setflood_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        limit = ayar_al(chat_id, "flood_limit") or "Kapalı"
        await update.message.reply_text(f"Mevcut flood limiti: *{limit}*\nKapatmak: `/setflood off`", parse_mode=ParseMode.MARKDOWN)
        return
    if context.args[0].lower() == "off":
        with db() as c:
            c.execute("DELETE FROM settings WHERE chat_id=? AND anahtar='flood_limit'", (chat_id,))
        await update.message.reply_text("🔕 Antiflood kapatıldı.")
        return
    try:
        limit = int(context.args[0])
        ayar_yaz(chat_id, "flood_limit", str(limit))
        await update.message.reply_text(f"✅ Flood limiti *{limit}* mesaj/10sn olarak ayarlandı.", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("Geçerli bir sayı gir.")

# ══════════════════════════════════════════════
# TEMIZLE (PURGE)
# ══════════════════════════════════════════════
@admin_gerekli
async def purge_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Silinecek ilk mesajı yanıtla.")
        return
    baslangic = update.message.reply_to_message.message_id
    bitis     = update.message.message_id
    chat_id   = update.effective_chat.id
    silinebilir = 0
    for msg_id in range(baslangic, bitis + 1):
        try:
            await context.bot.delete_message(chat_id, msg_id)
            silinebilir += 1
        except:
            pass
    bildirim = await update.effective_chat.send_message(f"🗑 {silinebilir} mesaj silindi.")
    import asyncio
    await asyncio.sleep(3)
    try:
        await bildirim.delete()
    except:
        pass

# ══════════════════════════════════════════════
# PİN
# ══════════════════════════════════════════════
@admin_gerekli
async def pin_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Pinlenecek mesajı yanıtla.")
        return
    sessiz = context.args and context.args[0].lower() in ("silent","sessiz")
    try:
        await update.message.reply_to_message.pin(disable_notification=sessiz)
        if not sessiz:
            await update.message.reply_text("📌 Mesaj pinlendi.")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

@admin_gerekli
async def unpin_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message.reply_to_message:
            await update.message.reply_to_message.unpin()
        else:
            await update.effective_chat.unpin_all_messages()
        await update.message.reply_text("📌 Mesaj(lar) pinden çıkarıldı.")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

# ══════════════════════════════════════════════
# RAPOR
# ══════════════════════════════════════════════
async def report_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Raporlamak istediğin mesajı yanıtla.")
        return
    sorunlu  = update.message.reply_to_message.from_user
    raporlayan = update.effective_user
    chat_id   = update.effective_chat.id
    admins    = await update.effective_chat.get_administrators()
    admin_str = " ".join(f"[{a.user.first_name}](tg://user?id={a.user.id})" for a in admins if not a.user.is_bot)
    sebep = " ".join(context.args) if context.args else "-"
    await update.effective_chat.send_message(
        f"🚨 *Rapor*\n\n"
        f"Şikayet eden: [{raporlayan.full_name}](tg://user?id={raporlayan.id})\n"
        f"Şikayet edilen: [{sorunlu.full_name}](tg://user?id={sorunlu.id})\n"
        f"Sebep: {sebep}\n\n"
        f"Yöneticiler: {admin_str}",
        parse_mode=ParseMode.MARKDOWN
    )

# ══════════════════════════════════════════════
# YÖNETİCİ LİSTESİ
# ══════════════════════════════════════════════
async def admins_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = await update.effective_chat.get_administrators()
    metin  = "👑 *Yöneticiler:*\n\n"
    for a in admins:
        if not a.user.is_bot:
            metin += f"• [{a.user.full_name}](tg://user?id={a.user.id})\n"
    await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════
# KULLANICI BİLGİSİ
# ══════════════════════════════════════════════
async def info_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
    else:
        u = update.effective_user
    chat_id = update.effective_chat.id
    with db() as c:
        warns_count = c.execute("SELECT COUNT(*) as cnt FROM warns WHERE chat_id=? AND user_id=?",
                                (chat_id, u.id)).fetchone()["cnt"]
        ku = c.execute("SELECT * FROM users WHERE user_id=?", (u.id,)).fetchone()
    puan = ku["puan"] if ku else 0
    metin = (
        f"👤 *Kullanıcı Bilgisi*\n\n"
        f"Ad: [{u.full_name}](tg://user?id={u.id})\n"
        f"ID: `{u.id}`\n"
        f"Kullanıcı adı: @{u.username or '-'}\n"
        f"⭐ Puan: *{puan}*\n"
        f"⚠️ Uyarı: *{warns_count}*"
    )
    await update.message.reply_text(metin, parse_mode=ParseMode.MARKDOWN)

async def id_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        u   = update.message.reply_to_message.from_user
        cid = update.effective_chat.id
        await update.message.reply_text(f"Kullanıcı ID: `{u.id}`\nGrup ID: `{cid}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(
            f"Kullanıcı ID: `{update.effective_user.id}`\nGrup ID: `{update.effective_chat.id}`",
            parse_mode=ParseMode.MARKDOWN
        )

# ══════════════════════════════════════════════
# YARDIM
# ══════════════════════════════════════════════
YARDIM_METNI = """
🦅 *CORVUS AREA BOT* — Komut Listesi

━━━ 📊 PUAN SİSTEMİ ━━━
`/puan` — Kendi puanını görüntüle
`/siralama` — Genel puan sıralaması
`/siralama_mesaj` — Aktiflik sıralaması (mesaj)
`/siralama_davet` — Davetçi sıralaması
`/puanver` [reply] — Admin: kullanıcıya puan ver

━━━ 🔨 MODERASYON ━━━
`/ban` `/tban` `/dban` `/sban` — Banlama
`/unban` — Ban kaldır
`/mute` `/tmute` `/unmute` — Sustur/çöz
`/kick` — Gruptan at
`/warn` `/unwarn` `/warns` — Uyarı sistemi
`/warnlimit [sayi]` — Uyarı limiti
`/warnmode ban|kick|mute` — Uyarı modu

━━━ 📌 NOTLAR ━━━
`/save [ad] [içerik]` — Not kaydet
`/get [ad]` ya da `#notadi` — Not çağır
`/notes` — Tüm notları listele
`/clearnote [ad]` — Not sil

━━━ 🔍 FİLTRELER & BLOCKLİST ━━━
`/filter [kelime] [yanıt]` — Filtre ekle
`/stop [kelime]` — Filtre sil
`/filters` — Filtreleri listele
`/addbl [kelime]` — Blocklist'e ekle
`/rmbl [kelime]` — Blocklist'ten çıkar
`/bl` — Blocklist'i görüntüle

━━━ 🔒 KİLİTLER ━━━
`/lock [tür]` — İçerik kilitle
`/unlock [tür]` — Kilidi kaldır
`/locks` — Aktif kilitler
Türler: link, forward, sticker, gif, photo, video, audio, document, voice, poll

━━━ 📜 KURALLAR & AYARLAR ━━━
`/rules` — Grup kuralları
`/setrules` — Kural yaz (admin)
`/setwelcome` — Karşılama mesajı
`/setgoodbye` — Veda mesajı
`/welcome off` — Karşılamayı kapat
`/setflood [sayi|off]` — Flood limiti
`/purge` [reply] — Mesajları sil
`/pin` [reply] — Mesajı pinle
`/unpin` — Mesajı unpin

━━━ 👥 GENEL ━━━
`/admins` — Yönetici listesi
`/report` [reply] — Yöneticiyi çağır
`/info` [reply] — Kullanıcı bilgisi
`/id` — ID bilgisi
"""

async def yardim_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(YARDIM_METNI, parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
def main():
    db_init()
    app = Application.builder().token(BOT_TOKEN).build()

    # Puan & genel
    app.add_handler(CommandHandler("puan",            puan_komutu))
    app.add_handler(CommandHandler("siralama",        siralama_genel))
    app.add_handler(CommandHandler("siralama_mesaj",  siralama_mesaj))
    app.add_handler(CommandHandler("siralama_davet",  siralama_davet))
    app.add_handler(CommandHandler("puanver",         puan_ver))
    app.add_handler(CommandHandler("yardim",          yardim_komutu))
    app.add_handler(CommandHandler("start",           yardim_komutu))

    # Moderasyon
    app.add_handler(CommandHandler("ban",     ban_komutu))
    app.add_handler(CommandHandler("tban",    tban_komutu))
    app.add_handler(CommandHandler("dban",    dban_komutu))
    app.add_handler(CommandHandler("sban",    sban_komutu))
    app.add_handler(CommandHandler("unban",   unban_komutu))
    app.add_handler(CommandHandler("mute",    mute_komutu))
    app.add_handler(CommandHandler("tmute",   tmute_komutu))
    app.add_handler(CommandHandler("unmute",  unmute_komutu))
    app.add_handler(CommandHandler("kick",    kick_komutu))

    # Uyarılar
    app.add_handler(CommandHandler("warn",      warn_komutu))
    app.add_handler(CommandHandler("unwarn",    unwarn_komutu))
    app.add_handler(CommandHandler("warns",     warns_komutu))
    app.add_handler(CommandHandler("warnlimit", warnlimit_komutu))
    app.add_handler(CommandHandler("warnmode",  warnmode_komutu))

    # Notlar
    app.add_handler(CommandHandler("save",      save_komutu))
    app.add_handler(CommandHandler("get",       get_komutu))
    app.add_handler(CommandHandler("notes",     notes_komutu))
    app.add_handler(CommandHandler("clearnote", clearnote_komutu))

    # Filtreler
    app.add_handler(CommandHandler("filter",  filter_komutu))
    app.add_handler(CommandHandler("stop",    stop_komutu))
    app.add_handler(CommandHandler("filters", filters_komutu))

    # Blocklist
    app.add_handler(CommandHandler("addbl", addbl_komutu))
    app.add_handler(CommandHandler("rmbl",  rmbl_komutu))
    app.add_handler(CommandHandler("bl",    bl_komutu))

    # Kilitler
    app.add_handler(CommandHandler("lock",   lock_komutu))
    app.add_handler(CommandHandler("unlock", unlock_komutu))
    app.add_handler(CommandHandler("locks",  locks_komutu))

    # Kurallar & Ayarlar
    app.add_handler(CommandHandler("rules",      rules_komutu))
    app.add_handler(CommandHandler("setrules",   setrules_komutu))
    app.add_handler(CommandHandler("clearrules", clearrules_komutu))
    app.add_handler(CommandHandler("setwelcome", setwelcome_komutu))
    app.add_handler(CommandHandler("setgoodbye", setgoodbye_komutu))
    app.add_handler(CommandHandler("welcome",    welcome_komutu))
    app.add_handler(CommandHandler("setflood",   setflood_komutu))
    app.add_handler(CommandHandler("purge",      purge_komutu))
    app.add_handler(CommandHandler("pin",        pin_komutu))
    app.add_handler(CommandHandler("unpin",      unpin_komutu))

    # Genel
    app.add_handler(CommandHandler("admins", admins_komutu))
    app.add_handler(CommandHandler("report", report_komutu))
    app.add_handler(CommandHandler("info",   info_komutu))
    app.add_handler(CommandHandler("id",     id_komutu))

    # Mesaj handler'ları (sıra önemli)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, kilit_kontrol), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, hashtag_not_handler), group=2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mesaj_sayici), group=3)

    # Üye olayları
    app.add_handler(ChatMemberHandler(yeni_uye_handler,  ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(uye_ayrildi_handler, ChatMemberHandler.CHAT_MEMBER))

    logger.info("🦅 Corvus Area Bot başlatıldı!")
    app.run_polling(allowed_updates=["message", "chat_member", "callback_query"])

if __name__ == "__main__":
    main()
