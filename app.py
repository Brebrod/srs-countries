import streamlit as st
import json
from datetime import datetime, timedelta
from pathlib import Path
import copy

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
    """Создаёт начальный список карточек в session_state"""
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
def calculate_next_review(streak: int, correct: bool, now: datetime) -> datetime:
    """
    При правильном ответе: now + 3^(streak-1) минут
    При неправильном: now
    """
    if not correct:
        return now
    # streak уже увеличен до текущего значения
    minutes = 3 ** (streak - 1)
    return now + timedelta(minutes=minutes)


def select_next_card(cards: list, now: datetime):
    """
    Выбирает следующую карточку:
    1. Среди просроченных (next_review <= now) берём ту, у которой next_review ближе всего к now
       (т.е. самую "свежую" просрочку — максимальную дату среди <= now)
    2. Если просроченных нет — берём ближайшую в будущем
    """
    if not cards:
        return None

    overdue = [c for c in cards if c["next_review"] <= now]
    if overdue:
        # Самая близкая к now среди просроченных = максимальный next_review
        return max(overdue, key=lambda c: c["next_review"])

    # Нет просроченных — берём ближайшую в будущем
    future = [c for c in cards if c["next_review"] > now]
    if future:
        return min(future, key=lambda c: c["next_review"])

    return cards[0]  # fallback


def process_answer(card: dict, correct: bool, now: datetime):
    """Обновляет карточку после ответа"""
    if correct:
        card["streak"] += 1
    else:
        card["streak"] = 0

    card["next_review"] = calculate_next_review(card["streak"], correct, now)
    return card


# ====================== UI ======================
def main():
    st.title("🌍 Страны и столицы")
    st.caption("Алгоритм интервального повторения с динамическим выбором следующей карточки")

    # Инициализация
    if "cards" not in st.session_state:
        st.session_state.cards = init_cards()
        st.session_state.current_card = None
        st.session_state.show_answer = False
        st.session_state.last_result = None  # "correct" / "wrong" / None
        st.session_state.stats = {"correct": 0, "wrong": 0, "total": 0}

    cards = st.session_state.cards
    now = datetime.now()

    # --- Боковая панель со статистикой ---
    with st.sidebar:
        st.header("Статистика")
        total = len(cards)
        learned = sum(1 for c in cards if c["streak"] >= 3)
        overdue_count = sum(1 for c in cards if c["next_review"] <= now)

        st.metric("Всего карточек", total)
        st.metric("С стриком ≥ 3", learned)
        st.metric("Просрочено сейчас", overdue_count)
        st.metric("Правильных ответов", st.session_state.stats["correct"])
        st.metric("Ошибок", st.session_state.stats["wrong"])

        st.divider()
        if st.button("🔄 Сбросить весь прогресс", type="secondary"):
            st.session_state.cards = init_cards()
            st.session_state.current_card = None
            st.session_state.show_answer = False
            st.session_state.last_result = None
            st.session_state.stats = {"correct": 0, "wrong": 0, "total": 0}
            st.rerun()

        st.divider()
        st.markdown("**Как работает алгоритм**")
        st.markdown("""
        - Правильный ответ → стрик +1, интервал = 3^(стрик-1) минут  
        - Неправильный → стрик = 0, карточка сразу снова в очереди  
        - Следующая карточка: самая близкая просроченная,  
          если таких нет — ближайшая в будущем
        """)

    # --- Выбор текущей карточки ---
    if st.session_state.current_card is None:
        st.session_state.current_card = select_next_card(cards, now)
        st.session_state.show_answer = False
        st.session_state.last_result = None

    card = st.session_state.current_card

    if card is None:
        st.success("Карточек нет!")
        return

    # --- Основной интерфейс ---
    st.subheader("Вопрос")
    st.markdown(f"### Какая столица у страны **{card['question']}**?")

    # Показываем подсказку по текущему состоянию карточки
    col1, col2, col3 = st.columns(3)
    with col1:
        st.caption(f"Стрик: **{card['streak']}**")
    with col2:
        delta = card["next_review"] - now
        if delta.total_seconds() <= 0:
            st.caption("Статус: **просрочена**")
        else:
            mins = int(delta.total_seconds() // 60)
            st.caption(f"Через: **{mins} мин**")
    with col3:
        st.caption(f"Всего ответов: {st.session_state.stats['total']}")

    st.divider()

    # Режим: либо ввод ответа, либо оценка после показа
    if not st.session_state.show_answer:
        user_answer = st.text_input(
            "Введите столицу:",
            key="user_input",
            placeholder="Например: Москва",
            label_visibility="collapsed",
        )

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Проверить", type="primary", use_container_width=True):
                if not user_answer.strip():
                    st.warning("Введите ответ")
                else:
                    st.session_state.show_answer = True
                    st.session_state.user_answer = user_answer.strip()
                    st.rerun()
        with col_b:
            if st.button("Не знаю / Показать ответ", use_container_width=True):
                st.session_state.show_answer = True
                st.session_state.user_answer = ""
                st.rerun()
    else:
        # Показываем правильный ответ и кнопки оценки
        correct_answer = card["answer"]
        user_ans = st.session_state.get("user_answer", "")

        st.markdown(f"**Правильный ответ:** `{correct_answer}`")

        if user_ans:
            # Простое сравнение (без учёта регистра и лишних пробелов)
            is_exact = user_ans.lower().replace("ё", "е") == correct_answer.lower().replace("ё", "е")
            if is_exact:
                st.success("Точное совпадение!")
            else:
                st.info(f"Вы написали: `{user_ans}`")

        st.write("")
        st.write("Оцените свой ответ:")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Правильно", type="primary", use_container_width=True):
                process_answer(card, correct=True, now=now)
                st.session_state.stats["correct"] += 1
                st.session_state.stats["total"] += 1
                st.session_state.current_card = None
                st.session_state.show_answer = False
                st.session_state.last_result = "correct"
                st.rerun()
        with col2:
            if st.button("❌ Неправильно", use_container_width=True):
                process_answer(card, correct=False, now=now)
                st.session_state.stats["wrong"] += 1
                st.session_state.stats["total"] += 1
                st.session_state.current_card = None
                st.session_state.show_answer = False
                st.session_state.last_result = "wrong"
                st.rerun()

    # Небольшая подсказка внизу
    st.divider()
    with st.expander("Показать ближайшие карточки (отладка)"):
        sorted_cards = sorted(cards, key=lambda c: c["next_review"])
        rows = []
        for c in sorted_cards[:15]:
            delta = c["next_review"] - now
            status = "просрочена" if delta.total_seconds() <= 0 else f"через {int(delta.total_seconds()//60)} мин"
            rows.append({
                "Страна": c["question"],
                "Стрик": c["streak"],
                "Следующее": status,
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
