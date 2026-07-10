import os
import html
import json
import logging
import psycopg2
from contextlib import closing

from dotenv import load_dotenv
import telebot
from telebot import types
from flask import Flask, request

# Setup
load_dotenv()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found. Check your environment variables.")

# Vercel Postgres provides the connection URL in the DATABASE_URL variable
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found. Please link Vercel Postgres.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("salary_bot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

MAX_JOB_NAME_LEN = 40
MAX_MONTH_LEN = 20
MAX_RATE = 100_000
MAX_HOURS_PER_ENTRY = 24


def get_db():
    """Establish a connection to the PostgreSQL database."""
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Initialize Postgres tables. Runs automatically on Vercel cold starts."""
    with closing(get_db()) as conn:
        with conn.cursor() as cur:
            # Jobs Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    chat_id BIGINT NOT NULL,
                    job_name TEXT NOT NULL,
                    rate REAL NOT NULL,
                    PRIMARY KEY (chat_id, job_name)
                )
            """)
            # Time Logs Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS time_logs (
                    chat_id BIGINT NOT NULL,
                    job_name TEXT NOT NULL,
                    month TEXT NOT NULL,
                    hours REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (chat_id, job_name, month),
                    FOREIGN KEY (chat_id, job_name) REFERENCES jobs(chat_id, job_name)
                        ON DELETE CASCADE
                )
            """)
            # Serverless State Management Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_states (
                    chat_id BIGINT PRIMARY KEY,
                    state_data JSONB NOT NULL DEFAULT '{}'::jsonb
                )
            """)
        conn.commit()


# Run init_db on cold start
try:
    init_db()
    logger.info("Database initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")


# --- STATE MANAGEMENT (Now powered by Postgres for Vercel) ---

def set_state(chat_id, **kwargs):
    current_state = get_state(chat_id)
    current_state.update(kwargs)
    with closing(get_db()) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_states (chat_id, state_data)
                VALUES (%s, %s)
                ON CONFLICT (chat_id)
                DO UPDATE SET state_data = EXCLUDED.state_data
            """, (chat_id, json.dumps(current_state)))
        conn.commit()


def get_state(chat_id):
    with closing(get_db()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT state_data FROM user_states WHERE chat_id = %s", (chat_id,))
            row = cur.fetchone()
            return row[0] if row else {}


def clear_state(chat_id):
    with closing(get_db()) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_states WHERE chat_id = %s", (chat_id,))
        conn.commit()


# --- DATABASE HELPERS ---

def job_exists(chat_id, job_name) -> bool:
    with closing(get_db()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM jobs WHERE chat_id = %s AND job_name = %s",
                (chat_id, job_name),
            )
            return cur.fetchone() is not None


def add_job(chat_id, job_name, rate):
    with closing(get_db()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (chat_id, job_name, rate) VALUES (%s, %s, %s)",
                (chat_id, job_name, rate),
            )
        conn.commit()


def add_hours(chat_id, job_name, month, hours):
    with closing(get_db()) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO time_logs (chat_id, job_name, month, hours)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(chat_id, job_name, month)
                DO UPDATE SET hours = time_logs.hours + EXCLUDED.hours
                RETURNING hours
            """, (chat_id, job_name, month, hours))
            new_hours = cur.fetchone()[0]
        conn.commit()
        return new_hours


def get_all_jobs(chat_id):
    with closing(get_db()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT job_name, rate FROM jobs WHERE chat_id = %s ORDER BY job_name",
                (chat_id,),
            )
            jobs = cur.fetchall()
            
            result = []
            for job_name, rate in jobs:
                cur.execute(
                    "SELECT month, hours FROM time_logs WHERE chat_id=%s AND job_name=%s ORDER BY month",
                    (chat_id, job_name),
                )
                months = cur.fetchall()
                result.append((job_name, rate, months))
            return result


# --- UTILS ---

def esc(text: str) -> str:
    """Escape user-supplied text before putting it into an HTML-mode message."""
    return html.escape(str(text))


def is_cancel_or_command(text: str) -> bool:
    return text is not None and text.strip().startswith('/')


def bail_if_command(message) -> bool:
    if is_cancel_or_command(message.text):
        clear_state(message.chat.id)
        bot.send_message(
            message.chat.id,
            "Cancelled. Send /help to see available commands.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return True
    return False


# --- BOT LOGIC ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    clear_state(message.chat.id)
    text = (
        "👋 <b>Welcome to the Salary Bot!</b>\n\n"
        "Here is what you can do:\n"
        "🛠 /addjob - Add a new part-time job\n"
        "⏱ /logtime - Log hours for a specific job &amp; month\n"
        "📊 /overview - View your total earnings breakdown\n"
        "🚫 /cancel - Cancel whatever you're doing"
    )
    bot.send_message(message.chat.id, text)


@bot.message_handler(commands=['cancel'])
def cancel(message):
    clear_state(message.chat.id)
    bot.send_message(
        message.chat.id, "Okay, cancelled.", reply_markup=types.ReplyKeyboardRemove()
    )


# --- ADD A JOB ---
@bot.message_handler(commands=['addjob'])
def add_job_start(message):
    msg = bot.reply_to(message, "What is the name of this job? (e.g., Cafe, Tutor, Job 1)\nSend /cancel to stop.")
    bot.register_next_step_handler(msg, process_job_name)


def process_job_name(message):
    chat_id = message.chat.id
    if bail_if_command(message):
        return

    job_name = message.text.strip()

    if not job_name:
        msg = bot.reply_to(message, "Job name can't be empty. Try again:")
        bot.register_next_step_handler(msg, process_job_name)
        return

    if len(job_name) > MAX_JOB_NAME_LEN:
        msg = bot.reply_to(message, f"Please keep the job name under {MAX_JOB_NAME_LEN} characters. Try again:")
        bot.register_next_step_handler(msg, process_job_name)
        return

    if job_exists(chat_id, job_name):
        msg = bot.reply_to(
            message,
            f"You already have a job called '{esc(job_name)}'. Pick a different name (or /cancel):"
        )
        bot.register_next_step_handler(msg, process_job_name)
        return

    set_state(chat_id, new_job_name=job_name)
    msg = bot.reply_to(message, f"Great. What is your hourly rate for {esc(job_name)}? (e.g., 15.50)")
    bot.register_next_step_handler(msg, process_job_rate)


def process_job_rate(message):
    chat_id = message.chat.id
    if bail_if_command(message):
        return

    state = get_state(chat_id)
    job_name = state.get('new_job_name')
    if not job_name:
        bot.send_message(chat_id, "Something went wrong — let's start over. Use /addjob.")
        return

    try:
        rate = float(message.text.strip())
    except ValueError:
        msg = bot.reply_to(message, "⚠️ Please enter a valid number for the rate, e.g. 15.50:")
        bot.register_next_step_handler(msg, process_job_rate)
        return

    if rate <= 0 or rate > MAX_RATE:
        msg = bot.reply_to(message, f"⚠️ Rate must be greater than 0 and less than {MAX_RATE}. Try again:")
        bot.register_next_step_handler(msg, process_job_rate)
        return

    if job_exists(chat_id, job_name):
        bot.send_message(chat_id, f"'{esc(job_name)}' already exists now — nothing changed. Use /overview to check.")
        clear_state(chat_id)
        return

    add_job(chat_id, job_name, rate)
    clear_state(chat_id)
    bot.send_message(
        chat_id,
        f"✅ Job '{esc(job_name)}' added with a rate of ${rate:.2f}/hr!\nUse /logtime to add hours.",
    )


# --- LOG TIME ---
@bot.message_handler(commands=['logtime'])
def log_time_start(message):
    chat_id = message.chat.id
    jobs = get_all_jobs(chat_id)

    if not jobs:
        bot.send_message(chat_id, "You haven't added any jobs yet! Use /addjob first.")
        return

    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    for job_name, _rate, _months in jobs:
        markup.add(job_name)

    msg = bot.send_message(chat_id, "Which job are you logging hours for? (/cancel to stop)", reply_markup=markup)
    bot.register_next_step_handler(msg, process_log_job)


def process_log_job(message):
    chat_id = message.chat.id
    if bail_if_command(message):
        return

    job_name = message.text.strip()

    if not job_exists(chat_id, job_name):
        bot.send_message(
            chat_id,
            "Job not found. Please use /logtime and pick a job from the menu.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    set_state(chat_id, log_job=job_name)
    msg = bot.send_message(chat_id, "Which month is this for? (e.g., January, Feb)", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, process_log_month)


def process_log_month(message):
    chat_id = message.chat.id
    if bail_if_command(message):
        return

    month = message.text.strip().capitalize()
    if not month:
        msg = bot.send_message(chat_id, "Month can't be empty. Try again:")
        bot.register_next_step_handler(msg, process_log_month)
        return
    if len(month) > MAX_MONTH_LEN:
        msg = bot.send_message(chat_id, f"Please keep the month under {MAX_MONTH_LEN} characters. Try again:")
        bot.register_next_step_handler(msg, process_log_month)
        return

    set_state(chat_id, log_month=month)
    msg = bot.send_message(chat_id, "Enter your shift(s) as 'hours minutes', comma-separated (e.g., '8 45, 4, 7 30'):")
    bot.register_next_step_handler(msg, process_log_time)


def process_log_time(message):
    chat_id = message.chat.id
    if bail_if_command(message):
        return

    state = get_state(chat_id)
    job_name = state.get('log_job')
    month = state.get('log_month')
    if not job_name or not month:
        bot.send_message(chat_id, "Something went wrong — let's start over. Use /logtime.")
        clear_state(chat_id)
        return

    time_entries = message.text.split(',')
    total_added = 0.0
    valid_count = 0
    errors = []

    for entry in time_entries:
        entry = entry.strip()
        if not entry:
            continue

        parts = entry.split()
        decimal_time = None
        try:
            if len(parts) == 1:
                decimal_time = float(parts[0])
            elif len(parts) == 2:
                hrs = int(parts[0])
                mins = int(parts[1])
                if mins < 0 or mins > 59 or hrs < 0:
                    raise ValueError
                decimal_time = hrs + (mins / 60)
            else:
                raise ValueError

            if decimal_time <= 0 or decimal_time > MAX_HOURS_PER_ENTRY:
                raise ValueError

        except ValueError:
            errors.append(entry)
            continue

        total_added += decimal_time
        valid_count += 1

    if errors:
        bot.send_message(
            chat_id,
            "⚠️ Ignored invalid entr" + ("y" if len(errors) == 1 else "ies") + ": "
            + ", ".join(esc(e) for e in errors)
            + f".\nEach entry must be like '8 45' or '4', and no more than {MAX_HOURS_PER_ENTRY} hours.",
        )

    if valid_count > 0:
        new_total = add_hours(chat_id, job_name, month, total_added)
        clear_state(chat_id)
        bot.send_message(
            chat_id,
            f"✅ Added {total_added:.2f} hours across {valid_count} shift(s) to "
            f"{esc(job_name)} for {esc(month)}.\n"
            f"(Total for {esc(month)}: {new_total:.2f} hours)\n\n"
            f"Use /logtime to add more or /overview to see totals.",
        )
    else:
        bot.send_message(chat_id, "❌ No valid times found. Please use /logtime again.")


# --- OVERVIEW ---
@bot.message_handler(commands=['overview'])
def show_overview(message):
    chat_id = message.chat.id
    jobs = get_all_jobs(chat_id)

    if not jobs:
        bot.send_message(chat_id, "You don't have any jobs set up yet! Use /addjob.")
        return

    lines = ["📊 <b>Your Salary Overview</b> 📊\n"]
    grand_total = 0.0

    for job_name, rate, months in jobs:
        lines.append(f"💼 <b>{esc(job_name)}</b> (${rate:.2f}/hr)")
        if not months:
            lines.append("  <i>No hours logged yet.</i>")
        else:
            for month, hours in months:
                pay = hours * rate
                grand_total += pay
                lines.append(f"  🔹 {esc(month)}: {hours:.2f} hrs -&gt; <b>${pay:.2f}</b>")
        lines.append("")

    lines.append("------------------------")
    lines.append(f"🏆 <b>Grand Total Earned: ${grand_total:.2f}</b>")

    bot.send_message(chat_id, "\n".join(lines))


@bot.message_handler(func=lambda m: True, content_types=['text'])
def fallback(message):
    bot.send_message(message.chat.id, "I didn't understand that. Send /help to see what I can do.")


# --- FLASK WEBHOOK ROUTES ---

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    return "Forbidden", 403

@app.route('/')
def index():
    return "Bot is running on Vercel!"