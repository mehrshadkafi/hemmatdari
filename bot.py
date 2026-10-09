import os, sqlite3, datetime as dt
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo("Asia/Tehran")
DB_PATH = os.environ.get("DB_PATH", "bot.db")

MIN_PUSH, MIN_JACK, MIN_PLANK = 3, 25, 30   # shena, parvaneh, plank(s)
BASE = 100        # hero score for hitting the minimum
BONUS_CAP = 50    # max extra points per day

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS members(chat_id INTEGER, user_id INTEGER, name TEXT,
  score INTEGER DEFAULT 0, PRIMARY KEY(chat_id,user_id));
CREATE TABLE IF NOT EXISTS reports(chat_id INTEGER, user_id INTEGER, day TEXT,
  push INTEGER, jack INTEGER, plank INTEGER, pts INTEGER DEFAULT 0, PRIMARY KEY(chat_id,user_id,day));
""")

HELP = (f"Daily minimum: {MIN_PUSH} shena, {MIN_JACK} parvaneh, {MIN_PLANK}s plank\n"
        "Report: /done <shena> <parvaneh> <plank seconds>\n"
        f"Example: /done {MIN_PUSH} {MIN_JACK} {MIN_PLANK}\n"
        f"Hit the minimum = HERO ({BASE} pts + bonus for extra). Otherwise = LOSER (0).\n"
        "/leaderboard  /me  /help")

def today(): return dt.datetime.now(TZ).date().isoformat()

def bonus(p, j, s):
    return min(max(0, p - MIN_PUSH) + max(0, j - MIN_JACK) // 5 + max(0, s - MIN_PLANK) // 10, BONUS_CAP)

def register(cid, uid, name):
    db.execute("INSERT OR IGNORE INTO members(chat_id,user_id,name) VALUES(?,?,?)", (cid, uid, name)); db.commit()

def heroes_and_losers(cid):
    passed = {uid for (uid,) in db.execute(
        "SELECT user_id FROM reports WHERE chat_id=? AND day=? AND pts>0", (cid, today()))}
    rows = db.execute("SELECT user_id,name FROM members WHERE chat_id=?", (cid,)).fetchall()
    return [n for uid, n in rows if uid in passed], [n for uid, n in rows if uid not in passed]

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    register(u.effective_chat.id, u.effective_user.id, u.effective_user.first_name)
    await u.message.reply_text(f"You're in, {u.effective_user.first_name}.\n" + HELP)

async def help_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(HELP)

async def done(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        p, j, s = (int(x) for x in c.args)
    except Exception:
        return await u.message.reply_text("Format: /done <shena> <parvaneh> <plank seconds>")
    cid, uid, name = u.effective_chat.id, u.effective_user.id, u.effective_user.first_name
    register(cid, uid, name)
    passed = p >= MIN_PUSH and j >= MIN_JACK and s >= MIN_PLANK
    new_pts = BASE + bonus(p, j, s) if passed else 0
    old = db.execute("SELECT pts FROM reports WHERE chat_id=? AND user_id=? AND day=?", (cid, uid, today())).fetchone()
    old_pts = old[0] if old else 0
    db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?,?,?)", (cid, uid, today(), p, j, s, new_pts))
    db.execute("UPDATE members SET score=score+? WHERE chat_id=? AND user_id=?", (new_pts - old_pts, cid, uid)); db.commit()
    total = db.execute("SELECT score FROM members WHERE chat_id=? AND user_id=?", (cid, uid)).fetchone()[0]
    if passed:
        await u.message.reply_text(f"HERO. {name} did it: {p} shena, {j} parvaneh, {s}s plank. +{new_pts} pts. Total: {total}. Respect.")
    else:
        await u.message.reply_text(f"LOSER. {p}/{j}/{s}s is below the minimum. Shame.\n"
                                   "You can still fix it today: do the minimum and send /done again.")

async def me(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = db.execute("SELECT score FROM members WHERE chat_id=? AND user_id=?",
                   (u.effective_chat.id, u.effective_user.id)).fetchone()
    await u.message.reply_text(f"Your score: {r[0]}" if r else "Send /start first.")

async def leaderboard(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid = u.effective_chat.id
    rows = db.execute("SELECT user_id,name,score FROM members WHERE chat_id=? ORDER BY score DESC", (cid,)).fetchall()
    if not rows: return await u.message.reply_text("Nobody has joined yet. Send /start.")
    heroes, losers = heroes_and_losers(cid)
    lines = [f"{i+1}. {n} - {sc} pts  [{'HERO' if n in heroes else 'LOSER'}]" for i, (uid, n, sc) in enumerate(rows)]
    msg = "Leaderboard (today's status in brackets)\n" + "\n".join(lines)
    if losers:
        msg += "\n\nSHAME: " + ", ".join(losers) + " - not done yet. Fix it: " + \
               f"{MIN_PUSH} shena, {MIN_JACK} parvaneh, {MIN_PLANK}s plank, then /done."
    else:
        msg += "\n\nEveryone is a HERO today."
    await u.message.reply_text(msg)

async def reset(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid, uid = u.effective_chat.id, u.effective_user.id
    if u.effective_chat.type != "private":
        member = await c.bot.get_chat_member(cid, uid)
        if member.status not in ("administrator", "creator"):
            return await u.message.reply_text("Only group admins can reset.")
    if c.args != ["confirm"]:
        return await u.message.reply_text("This deletes ALL scores and reports for this chat. Send: /reset confirm")
    db.execute("DELETE FROM reports WHERE chat_id=?", (cid,))
    db.execute("UPDATE members SET score=0 WHERE chat_id=?", (cid,)); db.commit()
    await u.message.reply_text("Reset done. Everyone is at 0. New game starts now.")

async def register_any(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user and not u.effective_user.is_bot:
        register(u.effective_chat.id, u.effective_user.id, u.effective_user.first_name)

async def morning(c: ContextTypes.DEFAULT_TYPE):
    for (cid,) in db.execute("SELECT DISTINCT chat_id FROM members").fetchall():
        await c.bot.send_message(cid, f"New day. Minimum: {MIN_PUSH} shena, {MIN_JACK} parvaneh, {MIN_PLANK}s plank. Report with /done")

async def midday(c: ContextTypes.DEFAULT_TYPE):
    for (cid,) in db.execute("SELECT DISTINCT chat_id FROM members").fetchall():
        heroes, losers = heroes_and_losers(cid)
        if losers:
            await c.bot.send_message(cid, "Half the day is gone. Still not done: " + ", ".join(losers) +
                                     f"\n{MIN_PUSH} shena, {MIN_JACK} parvaneh, {MIN_PLANK}s plank, then /done. Don't be a loser.")

async def close_day(c: ContextTypes.DEFAULT_TYPE):
    for (cid,) in db.execute("SELECT DISTINCT chat_id FROM members").fetchall():
        heroes, losers = heroes_and_losers(cid)
        msg = f"Day closed ({today()}).\n\n"
        msg += "HEROES - respect, you showed up: " + (", ".join(heroes) if heroes else "nobody. Really?") + "\n\n"
        msg += "LOSERS - shame on you: " + (", ".join(losers) if losers else "nobody. Perfect day.")
        if losers: msg += "\nTomorrow is a new chance. Don't waste it."
        await c.bot.send_message(cid, msg)

app = Application.builder().token(TOKEN).build()
for cmd, fn in [("start", start), ("help", help_cmd), ("rules", help_cmd), ("done", done),
                ("me", me), ("leaderboard", leaderboard), ("reset", reset)]:
    app.add_handler(CommandHandler(cmd, fn))
app.add_handler(MessageHandler(filters.ALL, register_any), group=1)
app.job_queue.run_daily(morning, dt.time(7, 0, tzinfo=TZ))
app.job_queue.run_daily(midday, dt.time(14, 0, tzinfo=TZ))
app.job_queue.run_daily(close_day, dt.time(23, 59, tzinfo=TZ))
app.run_polling()
