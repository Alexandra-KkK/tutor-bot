"""Отчёт об уроке для родителей: оценки по шкале 1–5 и комментарий. Заполняется по желанию."""
import secrets
from datetime import date, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..db import Database
from ..filters import TEXT_INPUT
from ..notify import safe_send
from ..ui import show
from ..views import TeacherSchedule

router = Router(name="report")

CRITERIA = [("effort", "Старательность"), ("productivity", "Продуктивность"), ("understanding", "Понимание темы")]


class Rep(StatesGroup):
    comment = State()


def stars(value: int) -> str:
    return "★" * value + "☆" * (5 - value)


def report_text(name: str, day: str, time: str, end: str, ratings: dict, comment: str, teacher_name: str) -> str:
    d = date.fromisoformat(day)
    lines = ["📋 <b>Отчёт об уроке</b>", f"Ученик: <b>{escape(name)}</b>", f"{d.strftime('%d.%m')}, {time}–{end}", ""]
    for key, title in CRITERIA:
        if ratings.get(key):
            lines.append(f"{title}: {stars(ratings[key])}")
    if comment:
        lines += ["", f"💬 {escape(comment)}"]
    lines += ["", f"Преподаватель: {escape(teacher_name)}"]
    return "\n".join(lines)


def _ratings_markup(rep: dict) -> kb.InlineKeyboardMarkup:
    ratings, n = rep["ratings"], rep["n"]
    rows = []
    for key, title in CRITERIA:
        rows.append([(title, "rep:noop")])
        rows.append([(f"✅{v}" if ratings.get(key) == v else str(v), f"rep:r:{n}:{key}:{v}") for v in range(1, 6)])
    rows.append([("Дальше →", f"rep:next:{n}"), ("Отмена", "rep:cancel")])
    return kb.inline(rows)


async def _current(call: CallbackQuery, state: FSMContext) -> dict | None:
    """Отчёт, к которому относится кнопка. Кнопки старого отчёта (если начат новый) не срабатывают."""
    rep = (await state.get_data()).get("rep")
    if not rep or call.data.split(":")[2] != rep["n"]:
        await call.answer("Этот отчёт уже закрыт. Начните заново из карточки ученика.", show_alert=True)
        return None
    return rep


async def _start(target, state: FSMContext, lesson, teacher: dict):
    await state.clear()
    rep = {"sid": lesson.student_id, "name": lesson.student_name, "day": lesson.day.isoformat(),
           "time": lesson.time, "end": lesson.end, "ratings": {}, "n": secrets.token_hex(3)}
    await state.update_data(rep=rep)
    await show(target,
               f"📋 Отчёт для родителей\nУченик: <b>{escape(lesson.student_name)}</b> · "
               f"{lesson.day.strftime('%d.%m')} {lesson.time}–{lesson.end}\n\n"
               "Поставьте оценки от 1 до 5 (можно не все) и нажмите «Дальше».",
               _ratings_markup(rep))


@router.callback_query(F.data.regexp(r"^rep:\d{4}-\d{2}-\d{2}:[ws]:\d+$"))
async def from_prompt(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    _, day_iso, kind, lid = call.data.split(":")
    sch = await TeacherSchedule.load(db, teacher)
    lesson = next((l for l in sch.on(date.fromisoformat(day_iso)) if l.kind == kind and l.id == int(lid)), None)
    if not lesson:
        await call.answer("Этого урока уже нет в расписании.", show_alert=True)
        return
    await _start(call, state, lesson, teacher)


@router.callback_query(F.data.regexp(r"^rep:last:\d+$"))
async def from_card(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    sid = int(call.data.split(":")[2])
    sch = await TeacherSchedule.load(db, teacher)
    now = sch.now.strftime("%H:%M")
    for i in range(0, 15):
        d = sch.today - timedelta(days=i)
        held = [l for l in sch.on(d, sid) if i > 0 or l.time <= now]
        if held:
            await _start(call, state, held[-1], teacher)
            return
    await call.answer("За последние две недели у ученика не было уроков.", show_alert=True)


@router.callback_query(F.data == "rep:noop")
async def noop(call: CallbackQuery):
    await call.answer("Нажмите оценку от 1 до 5 в строке ниже")


@router.callback_query(F.data.regexp(r"^rep:r:\w+:\w+:[1-5]$"))
async def rate(call: CallbackQuery, state: FSMContext):
    rep = await _current(call, state)
    if not rep:
        return
    _, _, _, key, value = call.data.split(":")
    rep["ratings"][key] = int(value)
    await state.update_data(rep=rep)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=_ratings_markup(rep))


@router.callback_query(F.data.startswith("rep:next:"))
async def ask_comment(call: CallbackQuery, state: FSMContext):
    rep = await _current(call, state)
    if not rep:
        return
    await state.set_state(Rep.comment)
    await show(call, "Что делали на уроке и как он прошёл? Пара слов для родителей: тема, успехи, "
                     "над чем поработать.\n\nНапишите сообщение или нажмите «Без комментария».",
               kb.inline([[("Без комментария", f"rep:send:{rep['n']}")], [("Отмена", "rep:cancel")]]))


@router.callback_query(F.data == "rep:cancel")
async def cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(call, "Отчёт не отправлен.")


@router.message(Rep.comment, TEXT_INPUT)
async def comment_typed(message: Message, state: FSMContext, db: Database, bot: Bot, teacher: dict):
    await _send(message, state, db, bot, teacher, message.text.strip()[:1500])


@router.callback_query(F.data.startswith("rep:send:"))
async def no_comment(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot, teacher: dict):
    if not await _current(call, state):
        return
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await _send(call.message, state, db, bot, teacher, "")


async def _send(message: Message, state: FSMContext, db: Database, bot: Bot, teacher: dict, comment: str):
    rep = (await state.get_data()).get("rep")
    if not rep:
        await state.clear()
        await message.answer("Начните отчёт заново из карточки ученика.")
        return
    if not rep["ratings"] and not comment:
        await message.answer("Отчёт пустой: поставьте хотя бы одну оценку или напишите пару слов.")
        return
    await state.clear()
    await db.add_report(teacher["id"], rep["sid"], rep["day"], rep["time"], rep["ratings"], comment)
    text = report_text(rep["name"], rep["day"], rep["time"], rep["end"], rep["ratings"], comment, teacher["name"])
    parents = await db.student_accounts(rep["sid"], role="parent")
    sent = sum([await safe_send(bot, p["tg_id"], text) for p in parents])
    if sent:
        await message.answer(f"✅ Отчёт отправлен родителям. Вот как они его видят:\n\n{text}")
    else:
        await message.answer("Отчёт сохранён, но родители ещё не подключены к боту. "
                             "Отправьте им личную ссылку из карточки ученика.",
                             reply_markup=kb.inline([[("📨 Ссылка для ученика и родителей", f"stu:inv:{rep['sid']}")]]))
