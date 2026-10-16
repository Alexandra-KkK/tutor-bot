"""Меню ученика и родителя."""
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from ..notify import notify_teacher

from .. import keyboards as kb
from ..db import Database
from ..keyboards import B
from ..views import TeacherSchedule

router = Router(name="family")


async def _children(db: Database, account: dict, teacher: dict) -> list[dict]:
    result = []
    for sid in await db.children(account["tg_id"]):
        st = await db.get_student(teacher["id"], sid)
        if st and st["active"]:
            result.append(st)
    return result


GONE = "Вы больше не числитесь у преподавателя. Нажмите «🔄 Рестарт профиля»."


@router.message(F.text == B.MY_SCHEDULE)
async def my_schedule(message: Message, state: FSMContext, db: Database, account: dict, teacher: dict):
    await state.clear()
    kids = await _children(db, account, teacher)
    if not kids:
        await message.answer(GONE)
        return
    sch = await TeacherSchedule.load(db, teacher)
    blocks = []
    for st in kids:
        body = sch.range_text(sch.today, 14, student_id=st["id"], show_price=False, show_name=False,
                              empty_days=False)
        who = "Твоё расписание" if account["role"] == "student" else f"Расписание: <b>{escape(st['name'])}</b>"
        blocks.append(f"📅 {who} на 2 недели\n\n{body}")
    text = "\n\n➖➖➖\n\n".join(blocks)
    if teacher["schedule_link"]:
        text += f"\n\n📎 Общее расписание: {escape(teacher['schedule_link'])}"
    await message.answer(text, disable_web_page_preview=True)


@router.message(F.text == B.NEXT_LESSON)
async def next_lesson(message: Message, state: FSMContext, db: Database, account: dict, teacher: dict):
    await state.clear()
    kids = await _children(db, account, teacher)
    if not kids:
        await message.answer(GONE)
        return
    sch = await TeacherSchedule.load(db, teacher)
    lines = []
    for st in kids:
        lessons = sch.next_lessons(st["id"])
        prefix = f"{escape(st['name'])}: " if account["role"] == "parent" and len(kids) > 1 else ""
        if not lessons:
            lines.append(f"{prefix}ближайших занятий пока нет 🙂")
            continue
        l = lessons[0]
        days = (l.day - sch.today).days
        when = "сегодня" if days == 0 else "завтра" if days == 1 else sch.lesson_word(l).split(" в ")[0]
        lines.append(f"{prefix}<b>{when}, {l.time}–{l.end}</b>")
    await message.answer("⏰ Ближайшее занятие:\n" + "\n".join(lines)
                         + f"\n\nПреподаватель: {escape(teacher['name'])}")


@router.message(F.text == B.MY_TEACHER)
async def my_teacher(message: Message, state: FSMContext, teacher: dict):
    await state.clear()
    await message.answer(f"👩‍🏫 Ваш преподаватель: <b>{escape(teacher['name'])}</b>\n\n"
                         "Написать преподавателю можно прямо здесь — ответ придёт в этот чат.",
                         reply_markup=kb.inline([[("✍️ Написать преподавателю", "sup:start")]]))


# ---------------------------------------------------------------- ответ на напоминание

@router.callback_query(F.data.startswith("rem:ok:"))
async def will_come(call: CallbackQuery):
    await call.answer("Отлично, ждём! 💪")
    await call.message.edit_reply_markup(reply_markup=kb.inline([[("👍 Отмечено: буду", "rem:noop")]]))


@router.callback_query(F.data == "rem:noop")
async def noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("rem:no:"))
async def cant_come(call: CallbackQuery, bot: Bot, db: Database, account: dict, teacher: dict):
    key = call.data[len("rem:no:"):]
    day_iso, kind, lid = key.split(":")
    sch = await TeacherSchedule.load(db, teacher)
    from datetime import date
    kids = await db.children(account["tg_id"])
    lesson = next((l for l in sch.on(date.fromisoformat(day_iso))
                   if l.kind == kind and l.id == int(lid) and l.student_id in kids), None)
    if not lesson:
        await call.answer("Этого урока уже нет в расписании.", show_alert=True)
        return
    if (lesson.day, lesson.time) <= (sch.today, sch.now.strftime("%H:%M")):
        await call.answer("Урок уже начался. Напишите преподавателю через «Мой преподаватель».", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=kb.inline([[("😔 Отмечено: не смогу", "rem:noop")]]))
    await call.answer()
    await call.message.answer("Передал преподавателю 👌 Если урок перенесут, я напишу.")
    await notify_teacher(
        bot, db, teacher["id"],
        f"😔 <b>{escape(lesson.student_name)}</b> не сможет прийти на урок {sch.lesson_word(lesson)}.",
        reply_markup=kb.inline([
            [("❌ Отменить урок", f"sch:cx:{key}"), ("🔁 Перенести", f"sch:mv:{key}")],
            [("Оставить как есть", "menu:close")],
        ]))
