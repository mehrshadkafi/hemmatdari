import os, sqlite3, datetime as dt
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo("Asia/Tehran")
DB_PATH = os.environ.get("DB_PATH", "bot.db")

MIN_PUSH, MIN_PLANK, MIN_JACK = 5, 30, 50
BASE = 100          # hero score for hitting the minimum
BONUS_CAP = 50      # max extra points per day
MISS_PENALTY = -10  # didn't report at all
STREAK_BONUS, STREAK_CAP = 5, 30

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS members(chat_id INTEGER, user_id INTEGER, name TEXT,
  score INTEGER DEFAULT 0, streak INTEGER DEFAULT 0, PRIMARY KEY(chat_id,user_id));
CREATE TABLE IF NOT EXISTS reports(chat_id INTEGER, user_id INTEGER, day TEXT,
  push INTEGER, plank INTEGER, jack INTEGER, pts INTEGER DEFAULT 0, PRIMARY KEY(chat_id,user_id,day));
""")

def today(): return dt.datetime.now(TZ).date().isoformat()

def bonus(p, s, j):
    b = max(0, p - MIN_PUSH) + max(0, s - MIN_PLANK) // 10 + max(0, j - MIN_JACK) // 10
    return min(b, BONUS_CAP)

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    db.execute("INSERT OR IGNORE INTO members(chat_id,user_id,name) VALUES(?,?,?)",
               (u.effective_chat.id, u.effective_user.id, u.effective_user.first_name)); db.commit()
    await u.message.reply_text(
        f"You're in, {u.effective_user.first_name}!\n"
        f"Daily minimum: {MIN_PUSH} pushups · {MIN_PLANK}s plank · {MIN_JACK} flying jacks\n"
        "Report with: /done <pushups> <plank seconds> <jacks>\nExample: /done 15 90 120")

async def rules(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        f"Minimum: {MIN_PUSH} pushups, {MIN_PLANK}s plank, {MIN_JACK} flying jacks\n"
        f"Hit it → {BASE} pts (hero) + up to {BONUS_CAP} bonus for extras\n"
        f"Below minimum → 0 pts (day loser)\nNo report → {MISS_PENALTY} pts (loser)\n"
        f"Streak: +{STREAK_BONUS}/day, max {STREAK_CAP}\nDay closes at 23:59 Tehran time.")

async def done(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        p, s, j = (int(x) for x in c.args)
    except Exception:
        return await u.message.reply_text("Format: /done <pushups> <plank seconds> <jacks>")
    cid, uid, name = u.effective_chat.id, u.effective_user.id, u.effective_user.first_name
    db.execute("INSERT OR IGNORE INTO members(chat_id,user_id,name) VALUES(?,?,?)", (cid, uid, name))
    passed = p >= MIN_PUSH and s >= MIN_PLANK and j >= MIN_JACK
    new_pts = BASE + bonus(p, s, j) if passed else 0
    old = db.execute("SELECT pts FROM reports WHERE chat_id=? AND user_id=? AND day=?", (cid, uid, today())).fetchone()
    old_pts = old[0] if old else 0
    db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?,?,?)", (cid, uid, today(), p, s, j, new_pts))
    db.execute("UPDATE members SET score=score+? WHERE chat_id=? AND user_id=?", (new_pts - old_pts, cid, uid)); db.commit()
    total = db.execute("SELECT score FROM members WHERE chat_id=? AND user_id=?", (cid, uid)).fetchone()[0]
    if passed:
        await u.message.reply_text(f"🦸 Hero! Logged {p}/{s}s/{j} → +{new_pts} pts. Total: {total}")
    else:
        await u.message.reply_text(
            f"😱 LOSER! {p}/{s}s/{j} is below the minimum. SHAME!\n"
            "But you can fix it — the leaderboard wants your shiny name ✨ Resend /done before midnight 💪")

async def me(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = db.execute("SELECT score,streak FROM members WHERE chat_id=? AND user_id=?",
                   (u.effective_chat.id, u.effective_user.id)).fetchone()
    await u.message.reply_text(f"Score: {r[0]} · Streak: {r[1]} days" if r else "Send /start first.")

async def leaderboard(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not db.execute("SELECT 1 FROM members WHERE chat_id=?", (u.effective_chat.id,)).fetchone():
        return await u.message.reply_text("Nobody has joined yet. Send /start.")
    medals = ["🥇", "🥈", "🥉"]
    passed_today = {uid for (uid,) in db.execute(
        "SELECT user_id FROM reports WHERE chat_id=? AND day=? AND pts>0", (u.effective_chat.id, today()))}
    rows = db.execute("SELECT user_id,name,score,streak FROM members WHERE chat_id=? ORDER BY score DESC",
                      (u.effective_chat.id,)).fetchall()
    lines, slackers = [], []
    for i, (uid, n, sc, st) in enumerate(rows):
        mark = "🦸" if uid in passed_today else "💀"
        lines.append(f"{medals[i] if i < 3 else f'{i+1}.'} {mark} {n} — {sc} pts (🔥{st})")
        if uid not in passed_today: slackers.append(n)
    msg = "🏆 Leaderboard (🦸 done today · 💀 not yet)\n" + "\n".join(lines)
    if slackers:
        msg += ("\n\n😱 " + ", ".join(slackers) + " — SHAME! LOSERS!\n"
                "But you can fix it — the leaderboard wants your shiny names ✨ Do it and /done 💪")
    else:
        msg += "\n\n🔥 Everyone is a hero today. Legends."
    await u.message.reply_text(msg)

async def morning(c: ContextTypes.DEFAULT_TYPE):
    for (cid,) in db.execute("SELECT DISTINCT chat_id FROM members"):
        await c.bot.send_message(cid, f"☀️ New day! Minimum: {MIN_PUSH} pushups · {MIN_PLANK}s plank · {MIN_JACK} flying jacks. Report with /done")

async def close_day(c: ContextTypes.DEFAULT_TYPE):
    day = today()
    for (cid,) in db.execute("SELECT DISTINCT chat_id FROM members").fetchall():
        heroes, day_losers, losers = [], [], []
        for uid, name, streak in db.execute("SELECT user_id,name,streak FROM members WHERE chat_id=?", (cid,)).fetchall():
            r = db.execute("SELECT push,plank,jack FROM reports WHERE chat_id=? AND user_id=? AND day=?", (cid, uid, day)).fetchone()
            if r and r[0] >= MIN_PUSH and r[1] >= MIN_PLANK and r[2] >= MIN_JACK:
                streak += 1
                pts = min(streak * STREAK_BONUS, STREAK_CAP)
                heroes.append(f"{name} (streak +{pts})")
            elif r:
                streak, pts = 0, 0
                day_losers.append(name)
            else:
                streak, pts = 0, MISS_PENALTY
                losers.append(name)
            db.execute("UPDATE members SET score=score+?, streak=? WHERE chat_id=? AND user_id=?", (pts, streak, cid, uid))
        db.commit()
        msg = f"🌙 Day closed ({day})\n"
        msg += "🦸 Heroes: " + (", ".join(heroes) or "none") + "\n"
        msg += "😬 Day losers (below minimum): " + (", ".join(day_losers) or "none") + "\n"
        msg += "💀 Losers (no report): " + (", ".join(losers) or "none")
        await c.bot.send_message(cid, msg)

async def register_any(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user and not u.effective_user.is_bot:
        db.execute("INSERT OR IGNORE INTO members(chat_id,user_id,name) VALUES(?,?,?)",
                   (u.effective_chat.id, u.effective_user.id, u.effective_user.first_name)); db.commit()

app = Application.builder().token(TOKEN).build()
for cmd, fn in [("start", start), ("rules", rules), ("done", done), ("me", me), ("leaderboard", leaderboard)]:
    app.add_handler(CommandHandler(cmd, fn))
app.add_handler(MessageHandler(filters.ALL, register_any), group=1)
app.job_queue.run_daily(morning, dt.time(7, 0, tzinfo=TZ))
app.job_queue.run_daily(close_day, dt.time(23, 59, tzinfo=TZ))
app.run_polling()
