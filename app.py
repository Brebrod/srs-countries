import streamlit as st
import json
from datetime import datetime, timedelta
from pathlib import Path

# ====================== НАСТРОЙКИ ======================
DATA_FILE = Path(__file__).parent / "countries.json"
INITIAL_NEXT_REVIEW = datetime(2000, 1, 1, 0, 0, 0)

st.set_page_config(
    page_title="Страны и столицы — Интервальные повторения",
    page_icon="🌍",
    layout="centered",
)

# ====================== ЗАГРУЗКА ДАННЫХ ======================
@st.cache_data
def load_countries():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def init_cards():
    """Создаёт начальный список карточек"""
    countries = load_countries()
    cards = []
    for item in countries:
        cards.append({
            "question": item["country"],
            "answer": item["capital"],
            "streak": 0,
            "next_review": INITIAL_NEXT_REVIEW,
        })
    return cards


# ====================== АЛГОРИТМ ======================
def normalize(text: str) -> str:
    """Приводит ответ к единому виду для сравнения"""
    if not text:
        return ""
    t = text.strip().lower()
    t = t.replace("ё", "е")
    # заменяем дефисы и подобные на пробел, потом чистим
    for ch in "-—–":
        t = t.replace(ch, " ")
    for ch in ".,;:!?«»\"'()":
        t = t.replace(ch, "")
    t = " ".join(t.split())
    return t


def is_correct_answer(user_answer: str, correct_answer: str) -> bool:
    return normalize(user_answer) == normalize(correct_answer)


def calculate_next_review(streak: int, correct: bool, now: datetime) -> datetime:
    """
    При правильном ответе: now + 2^(streak-1) минут
    При неправильном: now
    """
    if not correct:
        return now
    minutes = 2 ** (streak - 1)
    return now + timedelta(minutes=minutes)


def select_next_card(cards: list, now: datetime):
    """
    1. Среди просроченных (next_review <= now) берём ту, у которой next_review ближе всего к now
    2. Если просроченных нет — берём ближайшую в будущем
    """
    if not cards:
        return None

    overdue = [c for c in cards if c["next_review"] <= now]
    if overdue:
        return max(overdue, key=lambda c: c["next_review"])

    future = [c for c in cards if c["next_review"] > now]
    if future:
        return min(future, key=lambda c: c["next_review"])

    return cards[0]


def process_answer(card: dict, correct: bool, now: datetime):
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


# ====================== UI ======================
def main():
    st.title("🌍 Страны и столицы")
    st.caption("Интервальные повторения — бот сам проверяет ответ")

    # Инициализация
    if "cards" not in st.session_state:
        st.session_state.cards = init_cards()
        st.session_state.current_card = None
        st.session_state.phase = "question"  # question | result
        st.session_state.last_feedback = None
        st.session_state.stats = {"correct": 0, "wrong": 0, "total": 0}
        st.session_state.table_page = 0

    cards = st.session_state.cards
    now = datetime.now()

    # ---------- Боковая панель ----------
    with st.sidebar:
        st.header("Статистика")
        total = len(cards)
        learned = sum(1 for c in cards if c["streak"] >= 3)
        overdue_count = sum(1 for c in cards if c["next_review"] <= now)

        st.metric("Всего карточек", total)
        st.metric("С стриком ≥ 3", learned)
        st.metric("Просрочено сейчас", overdue_count)
        st.metric("Правильных", st.session_state.stats["correct"])
        st.metric("Ошибок", st.session_state.stats["wrong"])

        st.divider()
        if st.button("🔄 Сбросить весь прогресс", type="secondary"):
            st.session_state.cards = init_cards()
            st.session_state.current_card = None
            st.session_state.phase = "question"
            st.session_state.last_feedback = None
            st.session_state.stats = {"correct": 0, "wrong": 0, "total": 0}
            st.session_state.table_page = 0
            st.rerun()

        st.divider()
        st.markdown("**Алгоритм**")
        st.markdown("""
        - Правильный → стрик +1, интервал = 2^(стрик−1) мин  
        - Неправильный → стрик = 0, **сразу та же карточка**  
        - Следующая (после правильного): ближайшая просроченная,  
          иначе ближайшая в будущем
        """)

    # ---------- Выбор текущей карточки ----------
    if st.session_state.current_card is None:
        st.session_state.current_card = select_next_card(cards, now)
        st.session_state.phase = "question"
        st.session_state.last_feedback = None

    card = st.session_state.current_card
    if card is None:
        st.success("Карточек нет!")
        return

    # ---------- Основной интерфейс ----------
    if st.session_state.phase == "question":
        st.subheader("Вопрос")
        st.markdown(f"### Какая столица у страны **{card['question']}**?")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.caption(f"Стрик: **{card['streak']}**")
        with col2:
            st.caption(f"Статус: **{format_next_review(card['next_review'], now)}**")
        with col3:
            st.caption(f"Ответов: {st.session_state.stats['total']}")

        st.divider()

        with st.form(key="answer_form", clear_on_submit=True):
            user_answer = st.text_input(
                "Введите столицу:",
                placeholder="Например: Москва",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button("Проверить", type="primary", use_container_width=True)

            if submitted:
                if not user_answer.strip():
                    st.warning("Введите ответ")
                else:
                    correct = is_correct_answer(user_answer, card["answer"])
                    process_answer(card, correct, now)

                    st.session_state.stats["total"] += 1
                    if correct:
                        st.session_state.stats["correct"] += 1
                    else:
                        st.session_state.stats["wrong"] += 1

                    st.session_state.last_feedback = {
                        "correct": correct,
                        "user_answer": user_answer.strip(),
                        "right_answer": card["answer"],
                        "streak": card["streak"],
                        "next_review": card["next_review"],
                        "question": card["question"],
                    }
                    st.session_state.phase = "result"
                    st.rerun()

        if st.button("Не знаю", use_container_width=True):
            # Считаем как неправильный ответ
            process_answer(card, False, now)
            st.session_state.stats["total"] += 1
            st.session_state.stats["wrong"] += 1

            st.session_state.last_feedback = {
                "correct": False,
                "user_answer": "",
                "right_answer": card["answer"],
                "streak": card["streak"],
                "next_review": card["next_review"],
                "question": card["question"],
            }
            st.session_state.phase = "result"
            st.rerun()

    else:  # phase == "result"
        fb = st.session_state.last_feedback

        if fb["correct"]:
            st.success("✅ Правильно!")
        else:
            st.error("❌ Неправильно")

        st.markdown(f"**Страна:** {fb['question']}")
        if fb["user_answer"]:
            st.markdown(f"**Ваш ответ:** `{fb['user_answer']}`")
        st.markdown(f"**Правильный ответ:** `{fb['right_answer']}`")
        st.markdown(f"**Стрик сейчас:** {fb['streak']}")
        st.markdown(f"**Следующее повторение:** {format_next_review(fb['next_review'], now)}")

        st.divider()

        if st.button("➡️ Следующий вопрос", type="primary", use_container_width=True):
            # После ошибки обязательно повторяем ту же карточку
            if fb["correct"]:
                st.session_state.current_card = None  # выбрать новую
            # если неправильно — current_card остаётся тем же
            st.session_state.phase = "question"
            st.session_state.last_feedback = None
            st.rerun()

    # ---------- Таблица расписания ----------
    st.divider()
    st.subheader("📅 Расписание повторений")

    sorted_cards = sorted(cards, key=lambda c: c["next_review"], reverse=True)
    page_size = 10
    total_pages = max(1, (len(sorted_cards) + page_size - 1) // page_size)
    page = st.session_state.table_page
    page = max(0, min(page, total_pages - 1))
    st.session_state.table_page = page

    start = page * page_size
    end = start + page_size
    page_cards = sorted_cards[start:end]

    rows = []
    for i, c in enumerate(page_cards, start=start + 1):
        rows.append({
            "№": i,
            "Страна": c["question"],
            "Столица": c["answer"],
            "Стрик": c["streak"],
            "Следующее повторение": format_next_review(c["next_review"], now),
            "Время": c["next_review"].strftime("%d.%m.%Y %H:%M:%S"),
        })

    st.dataframe(rows, use_container_width=True, hide_index=True)

    col_prev, col_info, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("← Предыдущие 10", disabled=(page <= 0), use_container_width=True):
            st.session_state.table_page = page - 1
            st.rerun()
    with col_info:
        st.markdown(f"<div style='text-align:center'>Страница {page + 1} из {total_pages}</div>", unsafe_allow_html=True)
    with col_next:
        if st.button("Следующие 10 →", disabled=(page >= total_pages - 1), use_container_width=True):
            st.session_state.table_page = page + 1
            st.rerun()


if __name__ == "__main__":
    main()
