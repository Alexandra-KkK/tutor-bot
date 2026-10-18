"""Меню ученика и родителя."""
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from ..notify import notify_teacher

from .. import keyboards as kb
from ..db import Database
from ..filters import Role
from ..keyboards import ALL_MENU_BUTTONS, B
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


# ---------------------------------------------------------------- ученик сдаёт домашнее задание

class Sub(StatesGroup):
    content = State()


ASK_HW = ("📸 Пришли выполненное задание: фото, несколько фото или текст.\n"
          "Я передам его преподавателю.")


async def _start_submit(target: Message | CallbackQuery, state: FSMContext, db: Database,
                        account: dict, teacher: dict) -> None:
    kids = await _children(db, account, teacher)
    if not kids:
        await (target.message if isinstance(target, CallbackQuery) else target).answer(GONE)
        return
    student = kids[0]
    hw = await db.open_homework(student["id"])
    await state.set_state(Sub.content)
    await state.update_data(sid=student["id"], hw=hw["id"] if hw else None)
    text = ASK_HW if hw else "📸 Нового задания сейчас нет, но прислать работу можно.\n\n" + ASK_HW
    markup = kb.inline([[("Отмена", "hws:cancel")]])
    if isinstance(target, CallbackQuery):
        await target.answer()
        await target.message.answer(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(F.text == B.SEND_HW)
async def send_homework(message: Message, state: FSMContext, db: Database, account: dict, teacher: dict):
    await state.clear()
    if account["role"] != "student":
        await message.answer("Эта кнопка для ученика.")
        return
    await _start_submit(message, state, db, account, teacher)


@router.callback_query(F.data.regexp(r"^hws:\d+$"))
async def send_homework_cb(call: CallbackQuery, state: FSMContext, db: Database, account: dict, teacher: dict):
    await state.clear()
    await _start_submit(call, state, db, account, teacher)


@router.callback_query(F.data == "hws:cancel")
async def submit_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Отменено")
    await call.message.edit_text("Хорошо, пришлёшь позже 👌")


@router.callback_query(F.data == "hws:done")
async def submit_done(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await call.message.edit_text("Готово 👍 Преподаватель посмотрит и отметит задание.")


@router.message(Sub.content, ~F.text.in_(ALL_MENU_BUTTONS), ~F.text.startswith("/"))
async def submit_homework(message: Message, state: FSMContext, db: Database, bot: Bot,
                          account: dict, teacher: dict):
    data = await state.get_data()
    student = await db.get_student(teacher["id"], data.get("sid", 0))
    if not student:
        await state.clear()
        await message.answer(GONE)
        return
    hw_id = data.get("hw")
    hw = await db.get_homework(hw_id) if hw_id else None
    # Новая попытка начинается, если это первое сообщение или задание вернули на доработку
    first = not data.get("headed") or (hw and hw["status"] in ("sent", "redo"))
    same_album = message.media_group_id and message.media_group_id == data.get("album")
    if first and not hw_id:  # ученик прислал работу сам, без задания от преподавателя
        hw_id = await db.add_homework(teacher["id"], student["id"])
        await state.update_data(hw=hw_id)
    await state.update_data(headed=True, album=message.media_group_id)
    if first:
        await db.set_homework_status(hw_id, "submitted")
    for tg_id in await db.teacher_accounts(teacher["id"]):
        try:
            if first:
                await bot.send_message(tg_id, "📬 Домашнее задание на проверку\n"
                                              f"Ученик: <b>{escape(student['name'])}</b>")
            await message.copy_to(tg_id)
            if first:
                await bot.send_message(
                    tg_id, "Отметьте задание:",
                    reply_markup=kb.inline([[("✅ Зачтено", f"hw:ok:{hw_id}"),
                                             ("🔁 Не зачтено", f"hw:no:{hw_id}")]]))
        except Exception:
            pass
    if same_album:
        return
    await message.answer("✅ Отправлено преподавателю. Можно прислать ещё фото.",
                         reply_markup=kb.inline([[("Готово", "hws:done")]]))


@router.message(Role("student"), F.photo | F.document | F.video)
async def photo_as_homework(message: Message, state: FSMContext, db: Database, bot: Bot,
                            account: dict, teacher: dict):
    """Ученик прислал фото, не нажав кнопку: это почти всегда домашнее задание."""
    kids = await _children(db, account, teacher)
    if not kids:
        await message.answer(GONE)
        return
    hw = await db.open_homework(kids[0]["id"])
    await state.set_state(Sub.content)
    await state.update_data(sid=kids[0]["id"], hw=hw["id"] if hw else None)
    await submit_homework(message, state, db, bot, account, teacher)
