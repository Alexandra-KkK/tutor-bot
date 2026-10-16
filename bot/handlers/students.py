"""Раздел «Ученики»."""
from datetime import timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..db import Database
from ..filters import TEXT_INPUT
from ..keyboards import B
from ..logic import SHORT, money, parse_amount, plural
from ..texts import invite_link
from ..ui import show
from ..views import TeacherSchedule, earned_by_student

router = Router(name="students")


class Stu(StatesGroup):
    add_name = State()
    add_price = State()
    price = State()
    rename = State()
    note = State()


async def _list(target, db: Database, teacher: dict):
    students = await db.students(teacher["id"])
    linked = {a["student_id"] for a in await db.family_accounts(teacher["id"])}
    rows = []
    for s in students:
        mark = " 📱" if s["id"] in linked else ""
        price = f" · {money(s['price'])}" if s["price"] else ""
        rows.append([(f"{s['name']}{price}{mark}", f"stu:card:{s['id']}")])
    rows.append([("➕ Добавить ученика", "stu:add")])
    rows.append([("📨 Пригласить в бот", "stu:invite")])
    rows.append(kb.back("menu:close"))
    if students:
        text = (f"👥 Ваши ученики ({len(students)}):\n"
                "📱 — ученик или родитель подключён к боту и получает уведомления.")
    else:
        text = "👥 Учеников пока нет. Добавьте первого или отправьте ученикам ссылку-приглашение."
    await show(target, text, kb.inline(rows))


@router.message(F.text == B.STUDENTS)
async def open_students(message: Message, state: FSMContext, db: Database, teacher: dict):
    await state.clear()
    await _list(message, db, teacher)


@router.callback_query(F.data == "stu:list")
async def students_list(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await state.clear()
    await _list(call, db, teacher)


@router.callback_query(F.data == "stu:invite")
async def invite(call: CallbackQuery, bot: Bot, teacher: dict):
    link = await invite_link(bot, teacher)
    await call.answer()
    await call.message.answer(
        "Перешлите это сообщение ученикам и родителям 👇")
    await call.message.answer(
        f"Здравствуйте! Я веду расписание занятий в Telegram-боте. "
        f"Подключитесь по ссылке — бот будет присылать расписание на неделю и напоминать об уроках:\n{link}",
        disable_web_page_preview=True)


@router.callback_query(F.data.startswith("stu:inv:"))
async def invite_personal(call: CallbackQuery, bot: Bot, db: Database, teacher: dict):
    s = await db.get_student(teacher["id"], int(call.data.split(":")[2]))
    if not s:
        await call.answer("Ученик не найден", show_alert=True)
        return
    link = await invite_link(bot, teacher, s["id"])
    await call.answer()
    await call.message.answer(f"Ученик: <b>{escape(s['name'])}</b>. Перешлите сообщение ниже "
                              "ученику и родителям. По ней не нужно ничего выбирать, бот сразу узнает ученика 👇")
    await call.message.answer(
        f"Здравствуйте! Расписание занятий теперь в Telegram-боте. "
        f"Нажмите на ссылку — бот будет присылать расписание на неделю и напоминать об уроках:\n{link}",
        disable_web_page_preview=True)


# ---------------------------------------------------------------- карточка

async def _card(target, db: Database, teacher: dict, student_id: int):
    s = await db.get_student(teacher["id"], student_id)
    if not s or not s["active"]:
        await show(target, "Ученик не найден.", kb.inline([kb.back("stu:list")]))
        return
    sch = await TeacherSchedule.load(db, teacher)
    lines = [f"👤 <b>{escape(s['name'])}</b>", f"Цена занятия: {money(s['price']) if s['price'] else 'не указана'}"]
    if s["note"]:
        lines.append(f"Заметка: {escape(s['note'])}")
    weekly = [w for w in sch.weekly_template() if w["student_id"] == s["id"]]
    if weekly:
        lines.append("Регулярно: " + ", ".join(f"{SHORT[w['weekday']]} {w['time']}" for w in weekly))
    nxt = sch.next_lessons(s["id"])
    if nxt:
        lines.append(f"Ближайшее: {sch.lesson_word(nxt[0])}")
    accounts = await db.student_accounts(s["id"])
    kids = sum(1 for a in accounts if a["role"] == "student")
    parents = sum(1 for a in accounts if a["role"] == "parent")
    if accounts:
        lines.append(f"В боте: ученик — {'да' if kids else 'нет'}, родителей — {parents}")
    else:
        lines.append("В боте: не подключён")
    cnt, earned = earned_by_student(sch).get(s["id"], (0, 0))
    paid = sum(p["amount"] for p in await db.payments(teacher["id"], student_id=s["id"]))
    lines.append(f"\nПроведено занятий: {cnt} на {money(earned)}")
    lines.append(f"Оплачено: {money(paid)}")
    balance = paid - earned
    if balance < 0:
        lines.append(f"Долг: <b>{money(-balance)}</b>")
    elif balance > 0:
        lines.append(f"Предоплата: {money(balance)}")
    sid = s["id"]
    await show(target, "\n".join(lines), kb.inline([
        [("💵 Внести оплату", f"inc:pay:{sid}"), ("📅 Занятия", f"stu:sch:{sid}")],
        [("📨 Ссылка для ученика и родителей", f"stu:inv:{sid}")],
        [("📋 Отчёт об уроке", f"rep:last:{sid}"), ("📝 ДЗ", f"hw:{sid}")],
        [("Цена", f"stu:price:{sid}"), ("Имя", f"stu:rename:{sid}"), ("Заметка", f"stu:note:{sid}")],
        [("🗑 Удалить ученика", f"stu:del:{sid}")],
        kb.back("stu:list"),
    ]))


@router.callback_query(F.data.startswith("stu:card:"))
async def card(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await state.clear()
    await _card(call, db, teacher, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("stu:sch:"))
async def student_schedule(call: CallbackQuery, db: Database, teacher: dict):
    sid = int(call.data.split(":")[2])
    s = await db.get_student(teacher["id"], sid)
    if not s:
        await show(call, "Ученик не найден.", kb.inline([kb.back("stu:list")]))
        return
    sch = await TeacherSchedule.load(db, teacher)
    text = sch.range_text(sch.today, 14, student_id=sid, show_name=False, empty_days=False)
    await show(call, f"📅 {escape(s['name'])}: занятия на 2 недели\n\n{text}",
               kb.inline([kb.back(f"stu:card:{sid}")]))


# ---------------------------------------------------------------- добавление

@router.callback_query(F.data == "stu:add")
async def add_ask(call: CallbackQuery, state: FSMContext):
    await state.set_state(Stu.add_name)
    await show(call, "Как зовут ученика? Можно с фамилией: <i>Маша Иванова</i>",
               kb.inline([kb.back("stu:list")]))


@router.message(Stu.add_name, TEXT_INPUT)
async def add_name(message: Message, state: FSMContext, db: Database, teacher: dict):
    name = message.text.strip()[:64]
    if await db.find_student_by_name(teacher["id"], name):
        await message.answer("Ученик с таким именем уже есть. Напишите другое имя (например, с фамилией):")
        return
    await state.update_data(name=name)
    await state.set_state(Stu.add_price)
    await message.answer(f"Ученик: <b>{escape(name)}</b>. Сколько стоит одно занятие? Напишите сумму в рублях, например <code>1500</code>.",
                         reply_markup=kb.inline([[("Пропустить", "stu:add:skip")]]))


async def _add_done(message: Message, state: FSMContext, db: Database, teacher: dict, price: int):
    data = await state.get_data()
    await state.clear()
    sid = await db.add_student(teacher["id"], data["name"], price)
    await message.answer(f"✅ Ученик <b>{escape(data['name'])}</b> добавлен.", reply_markup=kb.inline([
        [("📅 Добавить регулярное занятие", "sch:add")],
        [("📨 Ссылка для ученика и родителей", f"stu:inv:{sid}")],
        [("Карточка ученика", f"stu:card:{sid}")],
        kb.back("stu:list", "К списку учеников"),
    ]))


@router.message(Stu.add_price, TEXT_INPUT)
async def add_price(message: Message, state: FSMContext, db: Database, teacher: dict):
    price = parse_amount(message.text)
    if price is None:
        await message.answer("Напишите сумму числом, например <code>1500</code>.")
        return
    await _add_done(message, state, db, teacher, price)


@router.callback_query(Stu.add_price, F.data == "stu:add:skip")
async def add_skip(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await _add_done(call.message, state, db, teacher, 0)


# ---------------------------------------------------------------- изменение

PROMPTS = {
    "price": (Stu.price, "Новая цена занятия в рублях:"),
    "rename": (Stu.rename, "Новое имя ученика:"),
    "note": (Stu.note, "Заметка об ученике (класс, цели, контакты родителей). Отправьте «-», чтобы очистить:"),
}


@router.callback_query(F.data.regexp(r"^stu:(price|rename|note):\d+$"))
async def edit_ask(call: CallbackQuery, state: FSMContext):
    _, what, sid = call.data.split(":")
    st, prompt = PROMPTS[what]
    await state.set_state(st)
    await state.update_data(sid=int(sid))
    await show(call, prompt, kb.inline([kb.back(f"stu:card:{sid}")]))


@router.message(Stu.price, TEXT_INPUT)
async def edit_price(message: Message, state: FSMContext, db: Database, teacher: dict):
    price = parse_amount(message.text)
    if price is None:
        await message.answer("Напишите сумму числом, например <code>1500</code>.")
        return
    sid = (await state.get_data())["sid"]
    await state.clear()
    await db.update_student(teacher["id"], sid, price=price)
    await message.answer("✅ Цена обновлена. Она применяется ко всем занятиям ученика, "
                         "для которых не указана отдельная цена.")
    await _card(message, db, teacher, sid)


@router.message(Stu.rename, TEXT_INPUT)
async def edit_name(message: Message, state: FSMContext, db: Database, teacher: dict):
    sid = (await state.get_data())["sid"]
    await state.clear()
    await db.update_student(teacher["id"], sid, name=message.text.strip()[:64])
    await _card(message, db, teacher, sid)


@router.message(Stu.note, TEXT_INPUT)
async def edit_note(message: Message, state: FSMContext, db: Database, teacher: dict):
    sid = (await state.get_data())["sid"]
    await state.clear()
    note = "" if message.text.strip() == "-" else message.text.strip()[:500]
    await db.update_student(teacher["id"], sid, note=note)
    await _card(message, db, teacher, sid)


# ---------------------------------------------------------------- удаление

@router.callback_query(F.data.regexp(r"^stu:del:\d+$"))
async def delete_ask(call: CallbackQuery, db: Database, teacher: dict):
    sid = int(call.data.split(":")[2])
    s = await db.get_student(teacher["id"], sid)
    if not s:
        await call.answer("Ученик не найден", show_alert=True)
        return
    await show(call, f"Удалить ученика <b>{escape(s['name'])}</b>?\n"
                     "Его будущие занятия уберутся из расписания, ученик и родители отключатся от бота. "
                     "Доход за прошлые занятия сохранится.",
               kb.confirm(f"stu:del:yes:{sid}", f"stu:card:{sid}"))


@router.callback_query(F.data.startswith("stu:del:yes:"))
async def delete(call: CallbackQuery, db: Database, teacher: dict):
    sid = int(call.data.split(":")[3])
    if not await db.get_student(teacher["id"], sid):
        await call.answer("Ученик не найден", show_alert=True)
        return
    sch = await TeacherSchedule.load(db, teacher)
    ends = {w["id"]: sch.last_day_for(w) for w in sch.weekly_template() if w["student_id"] == sid}
    await db.archive_student(teacher["id"], sid, ends, sch.today.isoformat(), sch.now.strftime("%H:%M"))
    await call.answer("Ученик удалён")
    await _list(call, db, teacher)


# ---------------------------------------------------------------- исправить привязку

@router.callback_query(F.data.regexp(r"^rel:\d+:\d+$"))
async def relink_pick(call: CallbackQuery, db: Database, teacher: dict):
    _, tg, old = call.data.split(":")
    acc = await db.get_account(int(tg))
    if not acc or acc["teacher_id"] != teacher["id"] or int(old) not in await db.children(acc["tg_id"]):
        await call.answer("Этот аккаунт уже не подключён.", show_alert=True)
        return
    students = [s for s in await db.students(teacher["id"]) if s["id"] != int(old)]
    rows = [[(s["name"], f"rln:{tg}:{old}:{s['id']}")] for s in students]
    rows.append([("Оставить как есть", "menu:close")])
    await call.answer()
    await call.message.answer(f"К какому ученику привязать {escape(acc['full_name'])}?", reply_markup=kb.inline(rows))


@router.callback_query(F.data.regexp(r"^rln:\d+:\d+:\d+$"))
async def relink(call: CallbackQuery, db: Database, teacher: dict):
    _, tg, old, new = (int(x) if x.isascii() and x.isdigit() else x for x in call.data.split(":"))
    acc = await db.get_account(tg)
    target = await db.get_student(teacher["id"], new)
    if not acc or acc["teacher_id"] != teacher["id"] or not target or old not in await db.children(tg):
        await call.answer("Не получилось — аккаунт или ученик не найден.", show_alert=True)
        return
    await db.relink_child(tg, old, new)
    # Ученик, созданный при подключении по ошибке, удаляется, если за ним ничего нет
    if not await db.student_has_history(old):
        await db.delete_student(teacher["id"], old)
    await show(call, f"✅ {escape(acc['full_name'])} теперь привязан к ученику <b>{escape(target['name'])}</b>.")
