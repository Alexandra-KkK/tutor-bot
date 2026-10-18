"""Домашнее задание: преподаватель отправляет его ученику и родителям и отмечает, зачтено ли."""
from datetime import datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..db import Database
from ..keyboards import ALL_MENU_BUTTONS
from ..logic import WEEKDAYS
from ..notify import notify_family, safe_send
from ..ui import show
from ..views import TeacherSchedule

router = Router(name="homework")


class Hw(StatesGroup):
    content = State()


def due_text(due: str) -> str:
    """'2026-10-22 15:00' -> 'четверг, 22.10 в 15:00'."""
    if not due:
        return ""
    day, time = due.split(" ")
    d = datetime.strptime(day, "%Y-%m-%d").date()
    return f"{WEEKDAYS[d.weekday()].lower()}, {d.strftime('%d.%m')} в {time}"


async def next_lesson_due(db: Database, teacher: dict, student_id: int) -> str:
    sch = await TeacherSchedule.load(db, teacher)
    lessons = sch.next_lessons(student_id)
    return f"{lessons[0].day.isoformat()} {lessons[0].time}" if lessons else ""


def deadline_line(due: str, name: str = "") -> str:
    when = due_text(due)
    if name:
        return (f"Нужно выполнить до следующего урока ({when})." if when
                else "Нужно выполнить до следующего урока.")
    return f"Сделать до следующего урока: {when}." if when else "Сделать до следующего урока."


# ---------------------------------------------------------------- преподаватель отправляет задание

@router.callback_query(F.data.regexp(r"^hw:\d+$"))
async def homework_ask(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    sid = int(call.data.split(":")[1])
    s = await db.get_student(teacher["id"], sid)
    if not s:
        await call.answer("Ученик не найден", show_alert=True)
        return
    await state.set_state(Hw.content)
    await state.update_data(sid=sid)
    await show(call, f"📝 Домашнее задание. Ученик: <b>{escape(s['name'])}</b>.\n\n"
                     "Пришлите задание: текст, фото (можно несколько сразу, подпись к фото тоже дойдёт) "
                     "или файл. Я перешлю ученику и родителям.\n"
                     "Когда всё отправите, нажмите «Готово»: ученик получит напоминание прислать фото "
                     "выполненного задания.",
               kb.inline([kb.back(f"stu:card:{sid}", "Отмена")]))


@router.message(Hw.content, ~F.text.in_(ALL_MENU_BUTTONS), ~F.text.startswith("/"))
async def homework_send(message: Message, state: FSMContext, db: Database, bot: Bot, teacher: dict):
    data = await state.get_data()
    s = data.get("sid") and await db.get_student(teacher["id"], data["sid"])
    if not s:
        await state.clear()
        await message.answer("Ученик не найден.")
        return
    accounts = await db.student_accounts(s["id"])
    if not accounts:
        await state.clear()
        await message.answer(f"{escape(s['name'])} и родители ещё не подключены к боту. "
                             "Отправьте им личную ссылку, и в следующий раз задание дойдёт.",
                             reply_markup=kb.inline([[("📨 Ссылка для ученика и родителей", f"stu:inv:{s['id']}")]]))
        return
    # Несколько сообщений (например, альбом из фото) уходят одним заданием: заголовок — только перед первым
    first = not data.get("headed")
    same_album = message.media_group_id and message.media_group_id == data.get("album")
    if first:
        due = await next_lesson_due(db, teacher, s["id"])
        hw_id = await db.add_homework(teacher["id"], s["id"], due)
        await state.update_data(hw=hw_id, due=due)
    else:
        due = data.get("due", "")
    await state.update_data(headed=True, album=message.media_group_id)
    sent = 0
    for acc in accounts:
        if acc["role"] == "student":
            head = f"📝 Домашнее задание\n{deadline_line(due)}"
        else:
            head = (f"📝 Домашнее задание: <b>{escape(s['name'])}</b>\n"
                    f"{deadline_line(due, s['name'])}")
        try:
            if first:
                await bot.send_message(acc["tg_id"], f"{head}\nПреподаватель: {escape(teacher['name'])}")
            await message.copy_to(acc["tg_id"])
            sent += 1
        except Exception:
            pass
    if same_album:
        return  # подтверждение уже было на первое фото альбома
    await message.answer(f"✅ Отправлено ученику и родителям ({sent} из {len(accounts)}).\n"
                         "Можно прислать ещё фото или текст, они дойдут к этому же заданию.",
                         reply_markup=kb.inline([[("Готово", f"hw:done:{s['id']}")]]))


@router.callback_query(F.data.regexp(r"^hw:done:\d+$"))
async def homework_done(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot, teacher: dict):
    data = await state.get_data()
    await state.clear()
    sid = int(call.data.split(":")[2])
    hw_id = data.get("hw")
    waiting = ""
    if hw_id:
        # ученику — напоминание прислать выполненное задание
        for acc in await db.student_accounts(sid, role="student"):
            await safe_send(bot, acc["tg_id"],
                            "📸 Жду фото выполненного задания.\n"
                            "Как сделаешь, нажми кнопку ниже или «📸 Сдать домашку» в меню.",
                            reply_markup=kb.inline([[("📸 Сдать домашку", f"hws:{hw_id}")]]))
            waiting = "\nУченику отправлено напоминание прислать фото выполненного задания."
    await show(call, f"Готово 👍{waiting}", kb.inline([[("Карточка ученика", f"stu:card:{sid}")]]))


# ---------------------------------------------------------------- отметка «зачтено / не зачтено»

@router.callback_query(F.data.regexp(r"^hw:(ok|no):\d+$"))
async def homework_check(call: CallbackQuery, db: Database, bot: Bot, teacher: dict):
    _, mark, hw_id = call.data.split(":")
    hw = await db.get_homework(int(hw_id))
    s = hw and await db.get_student(teacher["id"], hw["student_id"])
    if not hw or not s or hw["teacher_id"] != teacher["id"]:
        await call.answer("Это задание уже не найдено.", show_alert=True)
        return
    ok = mark == "ok"
    await db.set_homework_status(hw["id"], "done" if ok else "redo")
    label = "✅ Зачтено" if ok else "🔁 Не зачтено"
    await call.answer(label)
    try:
        await call.message.edit_reply_markup(reply_markup=kb.inline([[(f"Отмечено: {label}", "menu:close")]]))
    except Exception:
        pass
    for acc in await db.student_accounts(s["id"], role="student"):
        if ok:
            await safe_send(bot, acc["tg_id"], "✅ Домашнее задание зачтено. Молодец! 🎉")
        else:
            await safe_send(bot, acc["tg_id"],
                            "🔁 Домашнее задание пока не зачтено, нужно доделать.\n"
                            "Как исправишь, пришли ещё раз кнопкой «📸 Сдать домашку».",
                            reply_markup=kb.inline([[("📸 Сдать домашку", f"hws:{hw['id']}")]]))
    await notify_family(bot, db, teacher, s["id"],
                        f"{'✅' if ok else '🔁'} <b>{escape(s['name'])}</b>: домашнее задание "
                        + ("зачтено." if ok else "нужно доделать."),
                        roles=("parent",))
