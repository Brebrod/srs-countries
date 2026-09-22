#!/usr/bin/env python3
"""
Telegram-бот: Страны и столицы с интервальным повторением.
У каждого пользователя свой независимый прогресс.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ====================== НАСТРОЙКИ ======================
BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "countries.json"
DB_FILE = BASE_DIR / "bot_progress.db"
INITIAL_NEXT_REVIEW = datetime(2000, 1, 1, 0, 0, 0)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ====================== ДАННЫЕ ======================
def load_countries() -> list[dict]:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


COUNTRIES = load_countries()  # [{"country": "...", "capital": "..."}, ...]


# ====================== БАЗА ДАННЫХ ======================
def get_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            streak INTEGER NOT NULL DEFAULT 0,
            next_review TEXT NOT NULL,
            PRIMARY KEY (user_id, question)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            correct INTEGER NOT NULL DEFAULT 0,
            wrong INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def ensure_user(user_id: int):
    """Создаёт записи прогресса для нового пользователя, если их ещё нет."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM user_progress WHERE user_id = ? LIMIT 1", (user_id,))
    if cur.fetchone() is None:
        initial = INITIAL_NEXT_REVIEW.isoformat()
        rows = [
            (user_id, item["country"], item["capital"], 0, initial)
            for item in COUNTRIES
        ]
        cur.executemany(
            "INSERT INTO user_progress (user_id, question, answer, streak, next_review) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        cur.execute(
            "INSERT OR IGNORE INTO user_stats (user_id, correct, wrong, total) VALUES (?, 0, 0, 0)",
            (user_id,),
        )
        conn.commit()
    conn.close()


def get_user_cards(user_id: int) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT question, answer, streak, next_review FROM user_progress WHERE user_id = ?",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    cards = []
    for r in rows:
        cards.append({
            "question": r["question"],
            "answer": r["answer"],
            "streak": r["streak"],
            "next_review": datetime.fromisoformat(r["next_review"]),
        })
    return cards


def update_card(user_id: int, question: str, streak: int, next_review: datetime):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE user_progress SET streak = ?, next_review = ? WHERE user_id = ? AND question = ?",
        (streak, next_review.isoformat(), user_id, question),
    )
    conn.commit()
    conn.close()


def get_stats(user_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT correct, wrong, total FROM user_stats WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"correct": row["correct"], "wrong": row["wrong"], "total": row["total"]}
    return {"correct": 0, "wrong": 0, "total": 0}


def update_stats(user_id: int, correct: bool):
    conn = get_connection()
    cur = conn.cursor()
    if correct:
        cur.execute(
            "UPDATE user_stats SET correct = correct + 1, total = total + 1 WHERE user_id = ?",
            (user_id,),
        )
    else:
        cur.execute(
            "UPDATE user_stats SET wrong = wrong + 1, total = total + 1 WHERE user_id = ?",
            (user_id,),
        )
    conn.commit()
    conn.close()


def reset_user(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_progress WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM user_stats WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    ensure_user(user_id)


# ====================== АЛГОРИТМ ======================
def normalize(text: str) -> str:
    if not text:
        return ""
    t = text.strip().lower().replace("ё", "е")
    for ch in "-—–":
        t = t.replace(ch, " ")
    for ch in ".,;:!?«»\"'()":
        t = t.replace(ch, "")
    return " ".join(t.split())


def is_correct_answer(user_answer: str, correct_answer: str) -> bool:
    return normalize(user_answer) == normalize(correct_answer)


def calculate_next_review(streak: int, correct: bool, now: datetime) -> datetime:
    if not correct:
        return now
    minutes = 2 ** (streak - 1)
    return now + timedelta(minutes=minutes)


def select_next_card(cards: list, now: datetime) -> dict | None:
    if not cards:
        return None
    overdue = [c for c in cards if c["next_review"] <= now]
    if overdue:
        return max(overdue, key=lambda c: c["next_review"])
    future = [c for c in cards if c["next_review"] > now]
    if future:
        return min(future, key=lambda c: c["next_review"])
    return cards[0]


def process_answer(card: dict, correct: bool, now: datetime) -> dict:
    if correct:
        card["streak"] += 1
    else:
        card["streak"] = 0
    card["next_review"] = calculate_next_review(card["streak"], correct, now)
    return card


def format_next_review(dt: datetime, now: datetime) -> str:
    delta = dt - now
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "сейчас (просрочено)"
    minutes = seconds // 60
    if minutes < 60:
        return f"через {minutes} мин. ({dt.strftime('%H:%M:%S')})"
    hours = minutes // 60
    mins = minutes % 60
    if hours < 24:
        return f"через {hours} ч. {mins} мин. ({dt.strftime('%H:%M')})"
    days = hours // 24
    return f"через {days} дн. ({dt.strftime('%d.%m %H:%M')})"


# ====================== КЛАВИАТУРЫ ======================
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Не знаю")],
            [KeyboardButton("📊 Статистика"), KeyboardButton("📅 Расписание")],
            [KeyboardButton("🔄 Сбросить прогресс")],
        ],
        resize_keyboard=True,
    )


def after_answer_keyboard(correct: bool):
    if correct:
        return ReplyKeyboardMarkup(
            [[KeyboardButton("➡️ Следующий вопрос")]],
            resize_keyboard=True,
        )
    # после ошибки сразу можно отвечать снова — показываем ту же клавиатуру
    return main_keyboard()


# ====================== ОБРАБОТЧИКИ ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    context.user_data.clear()
    context.user_data["phase"] = "question"

    cards = get_user_cards(user_id)
    now = datetime.now()
    card = select_next_card(cards, now)
    context.user_data["current_card"] = card

    await update.message.reply_text(
        "🌍 Привет! Это бот для запоминания столиц стран.\n\n"
        "Я сам проверяю ответы.\n"
        "После ошибки сразу снова та же страна.\n"
        "Интервал: 2^(стрик−1) минут.\n\n"
        "Просто пиши название столицы.",
        reply_markup=main_keyboard(),
    )
    await send_question(update, context)


async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    card = context.user_data.get("current_card")
    if not card:
        user_id = update.effective_user.id
        cards = get_user_cards(user_id)
        card = select_next_card(cards, datetime.now())
        context.user_data["current_card"] = card

    if not card:
        await update.message.reply_text("Карточек нет.")
        return

    now = datetime.now()
    text = (
        f"🏛 Какая столица у страны **{card['question']}**?\n\n"
        f"Стрик: {card['streak']} · {format_next_review(card['next_review'], now)}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())
    context.user_data["phase"] = "question"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    phase = context.user_data.get("phase", "question")

    ensure_user(user_id)

    # Команды с клавиатуры
    if text == "📊 Статистика":
        await show_stats(update, context)
        return
    if text == "📅 Расписание":
        await show_schedule(update, context)
        return
    if text == "🔄 Сбросить прогресс":
        reset_user(user_id)
        context.user_data.clear()
        context.user_data["phase"] = "question"
        await update.message.reply_text("Прогресс сброшен. Начинаем заново.")
        await start(update, context)
        return
    if text == "➡️ Следующий вопрос":
        # только после правильного ответа
        context.user_data["current_card"] = None
        context.user_data["phase"] = "question"
        await send_question(update, context)
        return

    if phase != "question":
        # если ждём кнопку "Следующий", а пришло что-то другое
        await update.message.reply_text("Нажми «➡️ Следующий вопрос» или выбери действие на клавиатуре.")
        return

    card = context.user_data.get("current_card")
    if not card:
        cards = get_user_cards(user_id)
        card = select_next_card(cards, datetime.now())
        context.user_data["current_card"] = card
        if not card:
            await update.message.reply_text("Карточек нет.")
            return

    now = datetime.now()

    # "Не знаю" = неправильный ответ
    if text == "Не знаю":
        correct = False
        user_answer = ""
    else:
        correct = is_correct_answer(text, card["answer"])
        user_answer = text

    # обновляем карточку
    process_answer(card, correct, now)
    update_card(user_id, card["question"], card["streak"], card["next_review"])
    update_stats(user_id, correct)

    # сохраняем обновлённую карточку в user_data
    context.user_data["current_card"] = card

    # формируем ответ
    if correct:
        msg = (
            f"✅ Правильно!\n\n"
            f"Страна: {card['question']}\n"
            f"Столица: {card['answer']}\n"
            f"Стрик: {card['streak']}\n"
            f"Следующее повторение: {format_next_review(card['next_review'], now)}"
        )
        context.user_data["phase"] = "result_correct"
        await update.message.reply_text(msg, reply_markup=after_answer_keyboard(True))
    else:
        msg = (
            f"❌ Неправильно\n\n"
            f"Страна: {card['question']}\n"
        )
        if user_answer:
            msg += f"Твой ответ: {user_answer}\n"
        msg += (
            f"Правильный ответ: {card['answer']}\n"
            f"Стрик: {card['streak']}\n"
            f"Следующее повторение: сейчас (эта же карточка)\n\n"
            f"Попробуй ещё раз — какая столица у **{card['question']}**?"
        )
        context.user_data["phase"] = "question"  # сразу можно отвечать снова
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_keyboard())


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    stats = get_stats(user_id)
    cards = get_user_cards(user_id)
    now = datetime.now()
    learned = sum(1 for c in cards if c["streak"] >= 3)
    overdue = sum(1 for c in cards if c["next_review"] <= now)

    text = (
        f"📊 Твоя статистика\n\n"
        f"Всего карточек: {len(cards)}\n"
        f"Со стриком ≥ 3: {learned}\n"
        f"Просрочено сейчас: {overdue}\n"
        f"Правильных ответов: {stats['correct']}\n"
        f"Ошибок: {stats['wrong']}\n"
        f"Всего ответов: {stats['total']}"
    )
    await update.message.reply_text(text)


async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    cards = get_user_cards(user_id)
    now = datetime.now()
    # сортировка наоборот (сначала самые дальние), как в веб-версии
    sorted_cards = sorted(cards, key=lambda c: c["next_review"], reverse=True)[:10]

    lines = ["📅 Ближайшие (первые 10, сначала дальние):\n"]
    for i, c in enumerate(sorted_cards, 1):
        lines.append(
            f"{i}. {c['question']} → {c['answer']}\n"
            f"   стрик {c['streak']} · {format_next_review(c['next_review'], now)}"
        )
    await update.message.reply_text("\n".join(lines))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "/start — начать / продолжить\n"
        "/stats — статистика\n"
        "/schedule — расписание (10 карточек)\n"
        "/reset — сбросить свой прогресс\n\n"
        "Или пользуйся кнопками внизу."
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_stats(update, context)


async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_schedule(update, context)


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_user(user_id)
    context.user_data.clear()
    await update.message.reply_text("Твой прогресс сброшен.")
    await start(update, context)


# ====================== ЗАПУСК ======================
def main():
    token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print(
            "Ошибка: не задан токен бота.\n"
            "Установи переменную окружения BOT_TOKEN или TELEGRAM_BOT_TOKEN.\n"
            "Получить токен: @BotFather в Telegram → /newbot"
        )
        return

    init_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен. Остановка: Ctrl+C")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
