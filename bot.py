import os, sqlite3, datetime as dt
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo("Asia/Tehran")
DB_PATH = os.environ.get("DB_PATH", "bot.db")

MIN_PUSH, MIN_PLANK, MIN_JACK = 10, 60, 100
BASE = 100          # hero score for hitting the minimum
BONUS_CAP = 50      # max extra points per day
MISS_PENALTY = -10  # didn't report at all
STREAK_BONUS, STREAK_CAP = 5, 30

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS members(chat_id INTEGER, user_id INTEGER, name TEXT,
  score INTEGER DEFAULT 0, streak INTEGER DEFAULT 0, PRIMARY KEY(chat_id,user_id));
CREATE TABLE IF NOT EXISTS reports(chat_id INTEGER, user_id INTEGER, day TEXT,
  push INTEGER, plank INTEGER, jack INTEGER, PRIMARY KEY(chat_id,user_id,day));
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
    db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?,?)", (cid, uid, today(), p, s, j)); db.commit()
    if p >= MIN_PUSH and s >= MIN_PLANK and j >= MIN_JACK:
        await u.message.reply_text(f"🦸 Hero! Logged {p}/{s}s/{j}. Bonus today: +{bonus(p,s,j)} (points added at day close).")
    else:
        await u.message.reply_text("Logged, but that's below the minimum — you can resend /done before midnight.")

async def me(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = db.execute("SELECT score,streak FROM members WHERE chat_id=? AND user_id=?",
                   (u.effective_chat.id, u.effective_user.id)).fetchone()
    await u.message.reply_text(f"Score: {r[0]} · Streak: {r[1]} days" if r else "Send /start first.")

async def leaderboard(u: Update, c: ContextTypes.DEFAULT_TYPE):
    rows = db.execute("SELECT name,score,streak FROM members WHERE chat_id=? ORDER BY score DESC",
                      (u.effective_chat.id,)).fetchall()
    if not rows: return await u.message.reply_text("Nobody has joined yet. Send /start.")
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else f'{i+1}.'} {n} — {sc} pts (🔥{st})" for i, (n, sc, st) in enumerate(rows)]
    await u.message.reply_text("🏆 Leaderboard\n" + "\n".join(lines))

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
                pts = BASE + bonus(*r) + min(streak * STREAK_BONUS, STREAK_CAP)
                heroes.append(f"{name} +{pts}")
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

app = Application.builder().token(TOKEN).build()
for cmd, fn in [("start", start), ("rules", rules), ("done", done), ("me", me), ("leaderboard", leaderboard)]:
    app.add_handler(CommandHandler(cmd, fn))
app.job_queue.run_daily(morning, dt.time(7, 0, tzinfo=TZ))
app.job_queue.run_daily(close_day, dt.time(23, 59, tzinfo=TZ))
app.run_polling()
