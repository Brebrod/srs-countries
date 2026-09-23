#!/usr/bin/env python3
"""
Telegram-бот: интервальные повторения.
Разделы: столицы, флаги, ударения, картины.
У каждого пользователя свой прогресс по каждому разделу.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ====================== НАСТРОЙКИ ======================
BASE_DIR = Path(__file__).parent
DB_FILE = BASE_DIR / "bot_progress.db"
INITIAL_NEXT_REVIEW = datetime(2000, 1, 1, 0, 0, 0)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ====================== ДАННЫЕ ======================
def load_json(name: str):
    with open(BASE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


COUNTRIES = load_json("countries.json")
FLAGS = load_json("flags.json")
STRESS = load_json("stress_words.json")
PAINTINGS = load_json("paintings.json")

MODES = {
    "capitals": "🏛 Столицы",
    "flags": "🚩 Флаги",
    "stress": "🔤 Ударения",
    "paintings": "🖼 Картины",
}


# ====================== БАЗА ======================
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
            mode TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            streak INTEGER NOT NULL DEFAULT 0,
            next_review TEXT NOT NULL,
            extra TEXT,
            PRIMARY KEY (user_id, mode, question)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            correct INTEGER NOT NULL DEFAULT 0,
            wrong INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, mode)
        )
    """)
    conn.commit()
    conn.close()


def ensure_user_mode(user_id: int, mode: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM user_progress WHERE user_id = ? AND mode = ? LIMIT 1",
        (user_id, mode),
    )
    if cur.fetchone() is None:
        initial = INITIAL_NEXT_REVIEW.isoformat()
        rows = []
        if mode == "capitals":
            for item in COUNTRIES:
                rows.append((user_id, mode, item["country"], item["capital"], 0, initial, None))
        elif mode == "flags":
            for item in FLAGS:
                rows.append((user_id, mode, item["emoji"], item["country"], 0, initial, None))
        elif mode == "stress":
            for item in STRESS:
                extra = json.dumps({"options": item["options"], "word": item["word"]}, ensure_ascii=False)
                rows.append((user_id, mode, item["word"], item["correct"], 0, initial, extra))
        elif mode == "paintings":
            for item in PAINTINGS:
                extra = json.dumps({"image": item["image"], "title": item["title"], "artist": item["artist"]}, ensure_ascii=False)
                rows.append((user_id, mode, item["id"], item["answer"], 0, initial, extra))
        if rows:
            cur.executemany(
                "INSERT INTO user_progress (user_id, mode, question, answer, streak, next_review, extra) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        cur.execute(
            "INSERT OR IGNORE INTO user_stats (user_id, mode, correct, wrong, total) VALUES (?, ?, 0, 0, 0)",
            (user_id, mode),
        )
        conn.commit()
    conn.close()


def get_user_cards(user_id: int, mode: str) -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT question, answer, streak, next_review, extra FROM user_progress WHERE user_id = ? AND mode = ?",
        (user_id, mode),
    )
    rows = cur.fetchall()
    conn.close()
    cards = []
    for r in rows:
        card = {
            "question": r["question"],
            "answer": r["answer"],
            "streak": r["streak"],
            "next_review": datetime.fromisoformat(r["next_review"]),
            "extra": json.loads(r["extra"]) if r["extra"] else {},
        }
        cards.append(card)
    return cards


def update_card(user_id: int, mode: str, question: str, streak: int, next_review: datetime):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE user_progress SET streak = ?, next_review = ? WHERE user_id = ? AND mode = ? AND question = ?",
        (streak, next_review.isoformat(), user_id, mode, question),
    )
    conn.commit()
    conn.close()


def update_stats(user_id: int, mode: str, correct: bool):
    conn = get_connection()
    cur = conn.cursor()
    if correct:
        cur.execute(
            "UPDATE user_stats SET correct = correct + 1, total = total + 1 WHERE user_id = ? AND mode = ?",
            (user_id, mode),
        )
    else:
        cur.execute(
            "UPDATE user_stats SET wrong = wrong + 1, total = total + 1 WHERE user_id = ? AND mode = ?",
            (user_id, mode),
        )
    conn.commit()
    conn.close()


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


def is_correct_answer(user_answer: str, correct_answer: str, mode: str = "capitals") -> bool:
    u = normalize(user_answer)
    c = normalize(correct_answer)
    if mode == "paintings":
        parts = [normalize(p) for p in correct_answer.replace("—", "-").split("-")]
        parts = [p.strip() for p in parts if p.strip()]
        if u == c:
            return True
        for p in parts:
            if p and (p in u or u in p):
                return True
        return False
    return u == c


def calculate_next_review(streak: int, correct: bool, now: datetime) -> datetime:
    if not correct:
        return now
    minutes = 2 ** (streak - 1)
    return now + timedelta(minutes=minutes)


def select_next_card(cards: list, now: datetime):
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


# ====================== КЛАВИАТУРЫ ======================
def menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🏛 Столицы"), KeyboardButton("🚩 Флаги")],
            [KeyboardButton("🔤 Ударения"), KeyboardButton("🖼 Картины")],
            [KeyboardButton("📊 Статистика")],
        ],
        resize_keyboard=True,
    )


def study_keyboard(mode: str, options=None):
    if mode == "stress" and options:
        rows = [[KeyboardButton(opt)] for opt in options]
        rows.append([KeyboardButton("Не знаю"), KeyboardButton("📋 Меню")])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True)
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Не знаю")],
            [KeyboardButton("📋 Меню"), KeyboardButton("📊 Статистика")],
        ],
        resize_keyboard=True,
    )


# ====================== ОБРАБОТЧИКИ ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🌍 Выбери раздел для изучения:",
        reply_markup=menu_keyboard(),
    )


async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode")
    card = context.user_data.get("current_card")
    user_id = update.effective_user.id

    if not card:
        cards = get_user_cards(user_id, mode)
        card = select_next_card(cards, datetime.now())
        context.user_data["current_card"] = card

    if not card:
        await update.message.reply_text("В этом разделе пока нет карточек.", reply_markup=menu_keyboard())
        return

    if mode == "capitals":
        text = f"🏛 Какая столица у страны **{card['question']}**?"
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=study_keyboard(mode))

    elif mode == "flags":
        text = f"🚩 Какая это страна?\n\n{card['question']}"
        await update.message.reply_text(text, reply_markup=study_keyboard(mode))

    elif mode == "stress":
        word = card["extra"].get("word", card["question"])
        options = card["extra"].get("options", [])
        text = f"🔤 Куда падает ударение?\n\n**{word}**"
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=study_keyboard(mode, options))

    elif mode == "paintings":
        image_url = card["extra"].get("image")
        caption = "🖼 Кто автор и как называется картина?\n(можно написать автора, название или оба)"
        try:
            await update.message.reply_photo(
                photo=image_url,
                caption=caption,
                reply_markup=study_keyboard(mode),
            )
        except Exception:
            await update.message.reply_text(
                f"{caption}\n\n(не удалось загрузить картинку)\nID: {card['question']}",
                reply_markup=study_keyboard(mode),
            )

    context.user_data["phase"] = "question"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    if text in ("📋 Меню", "/menu"):
        context.user_data.clear()
        await update.message.reply_text("Выбери раздел:", reply_markup=menu_keyboard())
        return

    mode_by_button = {
        "🏛 Столицы": "capitals",
        "🚩 Флаги": "flags",
        "🔤 Ударения": "stress",
        "🖼 Картины": "paintings",
    }
    if text in mode_by_button:
        mode = mode_by_button[text]
        context.user_data.clear()
        context.user_data["mode"] = mode
        ensure_user_mode(user_id, mode)
        cards = get_user_cards(user_id, mode)
        card = select_next_card(cards, datetime.now())
        context.user_data["current_card"] = card
        await update.message.reply_text(f"Раздел: {MODES[mode]}", reply_markup=study_keyboard(mode))
        await send_question(update, context)
        return

    if text == "📊 Статистика":
        await show_stats(update, context)
        return

    mode = context.user_data.get("mode")
    if not mode:
        await update.message.reply_text("Сначала выбери раздел:", reply_markup=menu_keyboard())
        return

    card = context.user_data.get("current_card")
    if not card:
        cards = get_user_cards(user_id, mode)
        card = select_next_card(cards, datetime.now())
        context.user_data["current_card"] = card
        if not card:
            await update.message.reply_text("Карточек нет.", reply_markup=menu_keyboard())
            return

    now = datetime.now()

    if text == "Не знаю":
        correct = False
        user_answer = ""
    else:
        correct = is_correct_answer(text, card["answer"], mode)
        user_answer = text

    process_answer(card, correct, now)
    update_card(user_id, mode, card["question"], card["streak"], card["next_review"])
    update_stats(user_id, mode, correct)

    opts = card["extra"].get("options") if mode == "stress" else None

    if correct:
        await update.message.reply_text("✅ Правильно!", reply_markup=study_keyboard(mode, opts))
        context.user_data["current_card"] = None
        await send_question(update, context)
    else:
        msg = "❌ Неправильно"
        if user_answer:
            msg += f"\nТвой ответ: {user_answer}"
        msg += f"\nПравильный ответ: {card['answer']}"
        await update.message.reply_text(msg, reply_markup=study_keyboard(mode, opts))
        context.user_data["current_card"] = card
        await send_question(update, context)


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode = context.user_data.get("mode")

    if mode:
        ensure_user_mode(user_id, mode)
        cards = get_user_cards(user_id, mode)
        total = len(cards)
        in_progress = sum(1 for c in cards if 1 <= c["streak"] <= 7)
        learned = sum(1 for c in cards if c["streak"] >= 8)
        text = (
            f"📊 Статистика — {MODES.get(mode, mode)}\n\n"
            f"Всего карточек: {total}\n"
            f"В процессе (1–7): {in_progress}\n"
            f"Выучено (8+): {learned}"
        )
        await update.message.reply_text(text, reply_markup=study_keyboard(mode))
    else:
        lines = ["📊 Статистика по разделам:\n"]
        for m, title in MODES.items():
            ensure_user_mode(user_id, m)
            cards = get_user_cards(user_id, m)
            total = len(cards)
            learned = sum(1 for c in cards if c["streak"] >= 8)
            lines.append(f"{title}: {learned}/{total} выучено")
        await update.message.reply_text("\n".join(lines), reply_markup=menu_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Выбери раздел кнопкой внизу.\n"
        "Пиши ответ текстом (или жми вариант в «Ударениях»).\n"
        "«📋 Меню» — сменить раздел.\n"
        "/start — в начало.",
        reply_markup=menu_keyboard(),
    )


def main():
    token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Ошибка: задай BOT_TOKEN")
        return

    init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
