from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from .logic import SHORT, WEEKDAYS


class B:
    """Тексты кнопок главного меню."""
    SCHEDULE = "📅 Расписание"
    STUDENTS = "👥 Ученики"
    INCOME = "💰 Доход"
    MY_SUB = "⭐ Моя подписка"
    CANCEL_SUB = "Отменить подписку"
    SECRETS = "🔗 Пригласить учеников"
    OLD_SECRETS = "🔑 Мои секреты"  # старое название кнопки, у кого осталась старая клавиатура
    SETTINGS = "⚙️ Настройки"
    SUPPORT = "💬 Обратиться в поддержку"
    CANCEL = "✖️ Отмена"
    RESTART = "🔄 Рестарт профиля"
    # ученик
    MY_SCHEDULE = "📅 Моё расписание"
    NEXT_LESSON = "⏰ Ближайшее занятие"
    MY_TEACHER = "👩‍🏫 Мой преподаватель"
    ADMIN = "🛠 Админ-панель"


# Telegram ID владельцев бота, задаётся при запуске из ADMIN_IDS
ADMIN_IDS: set[int] = set()


TEACHER_MENU_BUTTONS = {
    B.SCHEDULE, B.STUDENTS, B.INCOME, B.MY_SUB, B.CANCEL_SUB, B.SECRETS,
    B.SETTINGS, B.SUPPORT, B.CANCEL, B.RESTART, B.OLD_SECRETS,
}
STUDENT_MENU_BUTTONS = {B.MY_SCHEDULE, B.NEXT_LESSON, B.MY_TEACHER, B.SUPPORT, B.CANCEL, B.RESTART}
ALL_MENU_BUTTONS = TEACHER_MENU_BUTTONS | STUDENT_MENU_BUTTONS | {B.ADMIN}


def teacher_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    from . import subscription
    kb = ReplyKeyboardBuilder()
    if subscription.ENABLED:
        # «Отменить подписку» — внутри «Моя подписка», чтобы не перегружать меню
        buttons = (B.SCHEDULE, B.STUDENTS, B.INCOME, B.SECRETS, B.MY_SUB, B.SETTINGS, B.SUPPORT)
        layout = (1, 2, 2, 2, 2)
    else:
        buttons = (B.SCHEDULE, B.STUDENTS, B.INCOME, B.SECRETS, B.SETTINGS, B.SUPPORT)
        layout = (1, 2, 1, 2, 2)
    for text in buttons:
        kb.button(text=text)
    kb.button(text=B.CANCEL)
    kb.button(text=B.RESTART)
    if is_admin:
        kb.button(text=B.ADMIN)
        layout = (*layout, 1)
    kb.adjust(*layout)
    return kb.as_markup(resize_keyboard=True, is_persistent=True)


def student_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    for text in (B.MY_SCHEDULE, B.NEXT_LESSON, B.MY_TEACHER, B.SUPPORT, B.CANCEL, B.RESTART):
        kb.button(text=text)
    kb.adjust(1, 1, 1, 1, 2)
    return kb.as_markup(resize_keyboard=True, is_persistent=True)


def inline(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """inline([[("Текст", "data")], [("A", "a"), ("B", "b")]])"""
    kb = InlineKeyboardBuilder()
    for row in rows:
        kb.row(*[_btn(text, data) for text, data in row])
    return kb.as_markup()


def _btn(text: str, data: str):
    from aiogram.types import InlineKeyboardButton
    if data.startswith("url:"):
        return InlineKeyboardButton(text=text, url=data[4:])
    return InlineKeyboardButton(text=text, callback_data=data)


def back(data: str, text: str = "Вернуться") -> list[tuple[str, str]]:
    return [(text, data)]


# ---------------------------------------------------------------- регистрация

def start_roles() -> InlineKeyboardMarkup:
    return inline([
        [("👩‍🏫 Я преподаватель", "reg:teacher")],
        [("🎒 Я ученик", "reg:student")],
        [("👪 Я родитель", "reg:parent")],
    ])


# ---------------------------------------------------------------- расписание

def schedule_menu() -> InlineKeyboardMarkup:
    return inline([
        [("Просмотр расписания", "sch:view")],
        [("Редактирование расписания", "sch:edit")],
        [("Удалить расписание", "sch:del")],
        back("menu:close"),
    ])


def schedule_view() -> InlineKeyboardMarkup:
    rows = [[(name, f"sch:day:{i}")] for i, name in enumerate(WEEKDAYS)]
    rows.append([("Сегодня", "sch:today"), ("Завтра", "sch:tomorrow")])
    rows.append([("Вся неделя", "sch:week"), ("План на дату", "sch:date")])
    rows.append(back("sch:menu"))
    return inline(rows)


def schedule_edit() -> InlineKeyboardMarkup:
    return inline([
        [("Изменить все расписание", "sch:bulk")],
        [("Добавить регулярное занятие", "sch:add")],
        [("Разовое занятие на дату", "sch:single")],
        [("Отменить или перенести занятие", "sch:cancel")],
        back("sch:menu"),
    ])


def schedule_delete() -> InlineKeyboardMarkup:
    return inline([
        [("Удалить одно занятие", "sch:delone")],
        [("Удалить всё расписание", "sch:delall")],
        back("sch:menu"),
    ])


def weekdays_picker(prefix: str, back_data: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, short in enumerate(SHORT):
        kb.button(text=short, callback_data=f"{prefix}{i}")
    kb.adjust(7)
    kb.row(_btn("Вернуться", back_data))
    return kb.as_markup()


def durations(prefix: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for minutes in (45, 60, 90, 120):
        kb.button(text=f"{minutes} мин", callback_data=f"{prefix}{minutes}")
    kb.adjust(4)
    return kb.as_markup()


def students_picker(students: list[dict], prefix: str, back_data: str, add_new: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in students:
        kb.row(_btn(s["name"], f"{prefix}{s['id']}"))
    if add_new:
        kb.row(_btn("+ Новый ученик", add_new))
    kb.row(_btn("Вернуться", back_data))
    return kb.as_markup()


def confirm(yes: str, no: str, yes_text: str = "Да, удалить", no_text: str = "Нет") -> InlineKeyboardMarkup:
    return inline([[(yes_text, yes), (no_text, no)]])


# ---------------------------------------------------------------- подписка

def subscription(teacher: dict, active: bool, price: int, trial_days: int) -> InlineKeyboardMarkup:
    rows = []
    if not teacher["trial_used"]:
        rows.append([(f"Возьмите бесплатные {trial_days} дней", "sub:trial")])
    rows.append([(f"Оплатите подписку ({price}₽/мес, авто)", "sub:pay")])
    rows.append([("🎁 Ввести промокод", "sub:promo")])
    rows.append([("Отменить подписку", "sub:cancel")])
    return inline(rows)
