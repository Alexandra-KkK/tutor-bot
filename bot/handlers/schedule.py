"""Раздел «Расписание» преподавателя."""
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
from ..logic import SHORT, WEEKDAYS, parse_date, parse_schedule_text, parse_time_range, plural
from ..notify import notify_family
from ..ui import show
from ..logic import minutes
from ..views import TeacherSchedule, conflict_note, day_block, plan_title

router = Router(name="schedule")


class Sch(StatesGroup):
    bulk = State()
    new_student = State()
    time = State()
    date = State()
    plan_date = State()
    cancel_date = State()
    move_date = State()
    move_time = State()


BULK_HELP = (
    "Отправьте всё расписание одним сообщением, по одному занятию в строке:\n"
    "<code>день время имя [цена] [длительность]</code>\n\n"
    "Например:\n"
    "<code>Пн 15:00 Маша Иванова 1500\n"
    "Ср 17:30-19:00 Петя 2000\n"
    "Пт 10 Аня 1200 45</code>\n\n"
    "Длительность по умолчанию — 60 минут. Новые ученики добавятся сами.\n"
    "⚠️ Текущее регулярное расписание будет заменено. Прошлые занятия и доход сохранятся."
)


def _count(n: int) -> str:
    return f"{n} {plural(n, 'занятие', 'занятия', 'занятий')}"


# ---------------------------------------------------------------- меню

@router.message(F.text == B.SCHEDULE)
async def open_schedule(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Раздел расписания:", reply_markup=kb.schedule_menu())


@router.callback_query(F.data == "sch:menu")
async def schedule_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(call, "Раздел расписания:", kb.schedule_menu())


@router.callback_query(F.data == "sch:view")
async def view_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(call, "Выберите день:", kb.schedule_view())


VIEW_BACK = kb.inline([kb.back("sch:view")])


@router.callback_query(F.data.startswith("sch:day:"))
async def view_day(call: CallbackQuery, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    weekday = int(call.data.split(":")[2])
    day = sch.today + timedelta(days=(weekday - sch.today.weekday()) % 7)
    await show(call, sch.plan_text(day), VIEW_BACK)


@router.callback_query(F.data.in_({"sch:today", "sch:tomorrow"}))
async def view_today(call: CallbackQuery, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    day = sch.today + timedelta(days=1 if call.data == "sch:tomorrow" else 0)
    await show(call, sch.plan_text(day), VIEW_BACK)


@router.callback_query(F.data == "sch:week")
async def view_week(call: CallbackQuery, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    await show(call, "📅 Расписание на 7 дней:\n\n" + sch.range_text(sch.today, 7), VIEW_BACK)


@router.callback_query(F.data == "sch:date")
async def view_date_ask(call: CallbackQuery, state: FSMContext):
    await state.set_state(Sch.plan_date)
    await show(call, "Напишите дату, например <code>25.10</code> или <code>2026-10-25</code>:", VIEW_BACK)


@router.message(Sch.plan_date, TEXT_INPUT)
async def view_date(message: Message, state: FSMContext, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    day = parse_date(message.text, sch.today)
    if not day:
        await message.answer("Не понял дату. Пример: <code>25.10</code>")
        return
    await state.clear()
    await message.answer(sch.plan_text(day), reply_markup=VIEW_BACK)


# ---------------------------------------------------------------- редактирование

@router.callback_query(F.data == "sch:edit")
async def edit_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(call, "Выберите вариант редактирования:", kb.schedule_edit())


EDIT_BACK = kb.inline([kb.back("sch:edit")])


@router.callback_query(F.data == "sch:bulk")
async def bulk_start(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    current = sch.bulk_text()
    text = BULK_HELP
    if current:
        text += "\n\nСейчас (можно скопировать и поправить):\n<code>" + escape(current) + "</code>"
    await state.set_state(Sch.bulk)
    await show(call, text, EDIT_BACK)


@router.message(Sch.bulk, TEXT_INPUT)
async def bulk_text(message: Message, state: FSMContext):
    entries, errors = parse_schedule_text(message.text)
    if errors:
        shown = "\n".join(escape(e[:150]) for e in errors[:10])
        if len(errors) > 10:
            shown += f"\n…и ещё {len(errors) - 10}"
        await message.answer("Не получилось разобрать строки:\n" + shown
                             + "\n\nИсправьте и отправьте расписание ещё раз.")
        return
    if not entries:
        await message.answer("Не нашёл ни одного занятия. Пример строки: <code>Пн 15:00 Маша 1500</code>")
        return
    entries.sort(key=lambda e: (e.weekday, e.time))
    await state.update_data(bulk=[e.__dict__ for e in entries])
    overlaps = []
    for a, b in zip(entries, entries[1:]):
        if a.weekday == b.weekday and minutes(b.time) < minutes(a.time) + (a.duration or 60):
            overlaps.append(f"{SHORT[a.weekday]}: {escape(a.name)} {a.time} и {escape(b.name)} {b.time}")
    lines = []
    for e in entries:
        line = f"{SHORT[e.weekday]} {e.time} — {escape(e.name)}"
        if e.price:
            line += f", {e.price} ₽"
        if e.duration:
            line += f", {e.duration} мин"
        lines.append(line)
    await message.answer(
        f"Новое расписание ({_count(len(entries))} в неделю):\n\n" + "\n".join(lines)
        + ("\n\n⚠️ Пересекаются по времени:\n" + "\n".join(overlaps) if overlaps else "")
        + "\n\nСохранить?",
        reply_markup=kb.inline([[("✅ Сохранить", "sch:bulk:save"), ("Отмена", "sch:edit")]]),
    )


@router.callback_query(F.data == "sch:bulk:save")
async def bulk_save(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    data = await state.get_data()
    entries = data.get("bulk")
    if not entries:
        await show(call, "Расписание не найдено, отправьте его ещё раз.", EDIT_BACK)
        return
    sch = await TeacherSchedule.load(db, teacher)
    # отмены и переносы будущих уроков переходят на такие же уроки нового расписания
    old = {w["id"]: (w["student_id"], w["weekday"], w["time"]) for w in sch.weekly_template()}
    keep: dict[tuple, list[dict]] = {}
    for c in await db.future_cancellations(teacher["id"], sch.today.isoformat()):
        if c["lesson_id"] in old:
            keep.setdefault(old[c["lesson_id"]], []).append(c)
    await sch.end_all(db)
    created = []
    for e in entries:
        student = await db.find_student_by_name(teacher["id"], e["name"])
        if not student:
            sid = await db.add_student(teacher["id"], e["name"], e["price"] or 0)
            student = await db.get_student(teacher["id"], sid)
            created.append(e["name"])
        elif e["price"] and not student["price"]:
            await db.update_student(teacher["id"], student["id"], price=e["price"])
            student["price"] = e["price"]
        price = e["price"] if e["price"] is not None and e["price"] != student["price"] else None
        first_day = sch.first_day_for(e["weekday"], e["time"])
        lid = await db.add_weekly(teacher["id"], student["id"], e["weekday"], e["time"], e["duration"] or 60, price,
                                  first_day)
        for c in keep.pop((student["id"], e["weekday"], e["time"]), []):
            if c["date"] >= first_day:
                await db.cancel_on(lid, c["date"], c["moved_to"])
    await state.clear()
    text = f"✅ Расписание сохранено: {_count(len(entries))} в неделю."
    if created:
        text += "\nНовые ученики: " + ", ".join(escape(n) for n in created)
    await show(call, text, kb.inline([[("Посмотреть неделю", "sch:week")], kb.back("sch:menu")]))


# ----- выбор ученика для добавления занятия

async def _pick_student(target, db: Database, teacher: dict, flow: str, state: FSMContext):
    await state.clear()
    await state.update_data(flow=flow)
    students = await db.students(teacher["id"])
    title = "Регулярное занятие" if flow == "add" else "Разовое занятие"
    await show(target, f"{title}. Выберите ученика:",
               kb.students_picker(students, "sch:st:", "sch:edit", add_new="sch:st:new"))


@router.callback_query(F.data == "sch:add")
async def add_start(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await _pick_student(call, db, teacher, "add", state)


@router.callback_query(F.data == "sch:single")
async def single_start(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await _pick_student(call, db, teacher, "single", state)


@router.callback_query(F.data == "sch:st:new")
async def new_student_ask(call: CallbackQuery, state: FSMContext):
    await state.set_state(Sch.new_student)
    await show(call, "Напишите имя нового ученика:", EDIT_BACK)


@router.message(Sch.new_student, TEXT_INPUT)
async def new_student_name(message: Message, state: FSMContext, db: Database, teacher: dict):
    name = message.text.strip()[:64]
    student = await db.find_student_by_name(teacher["id"], name)
    sid = student["id"] if student else await db.add_student(teacher["id"], name)
    await _student_chosen(message, state, sid, name)


@router.callback_query(F.data.regexp(r"^sch:st:\d+$"))
async def student_chosen(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    student = await db.get_student(teacher["id"], int(call.data.split(":")[2]))
    if not student:
        await call.answer("Ученик не найден", show_alert=True)
        return
    await _student_chosen(call, state, student["id"], student["name"])


async def _student_chosen(target, state: FSMContext, student_id: int, name: str):
    await state.update_data(student_id=student_id, student_name=name)
    data = await state.get_data()
    if data.get("flow") == "add":
        await state.set_state(None)
        await show(target, f"Ученик: <b>{escape(name)}</b>.\nВ какой день недели занятие?",
                   kb.weekdays_picker("sch:wd:", "sch:edit"))
    else:
        await state.set_state(Sch.date)
        await show(target, f"Ученик: <b>{escape(name)}</b>.\nНа какую дату? Например <code>25.10</code>, "
                           "<code>завтра</code> или <code>пт</code>.", EDIT_BACK)


@router.callback_query(F.data.startswith("sch:wd:"))
async def weekday_chosen(call: CallbackQuery, state: FSMContext):
    if (await state.get_data()).get("flow") != "add":
        await show(call, "Эта кнопка устарела, начните заново.", kb.schedule_edit())
        return
    weekday = int(call.data.split(":")[2])
    await state.update_data(weekday=weekday)
    await state.set_state(Sch.time)
    await show(call, f"{WEEKDAYS[weekday]}. Во сколько? Например <code>15:00</code> или <code>15:00-16:30</code>.",
               EDIT_BACK)


@router.message(Sch.date, TEXT_INPUT)
async def single_date(message: Message, state: FSMContext, teacher: dict, db: Database):
    sch = await TeacherSchedule.load(db, teacher)
    day = parse_date(message.text, sch.today)
    if not day:
        await message.answer("Не понял дату. Пример: <code>25.10</code>")
        return
    if day < sch.today:
        await message.answer("Эта дата уже прошла. Напишите другую:")
        return
    await state.update_data(date=day.isoformat())
    await state.set_state(Sch.time)
    lessons = sch.on(day)
    busy = ("\n\nВ этот день уже есть:\n" + day_block(lessons, show_price=False)) if lessons else ""
    await message.answer(f"{plan_title(day, sch.today)[:-1]}. Во сколько занятие?{busy}")


@router.message(Sch.time, TEXT_INPUT)
async def time_entered(message: Message, state: FSMContext, db: Database, teacher: dict, bot: Bot):
    parsed = parse_time_range(message.text)
    if not parsed:
        await message.answer("Не понял время. Пример: <code>15:00</code> или <code>15:00-16:30</code>")
        return
    time, duration = parsed
    await state.update_data(time=time)
    if duration:
        await _save_lesson(message, state, db, teacher, bot, duration)
    else:
        await state.set_state(None)
        await message.answer("Сколько длится занятие?", reply_markup=kb.durations("sch:dur:"))


@router.callback_query(F.data.startswith("sch:dur:"))
async def duration_chosen(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict, bot: Bot):
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await _save_lesson(call.message, state, db, teacher, bot, int(call.data.split(":")[2]))


async def _save_lesson(message: Message, state: FSMContext, db: Database, teacher: dict, bot: Bot, duration: int):
    data = await state.get_data()
    need = "weekday" if data.get("flow") == "add" else "date"
    if "student_id" not in data or "time" not in data or need not in data:
        await state.clear()
        await message.answer("Что-то пошло не так, начните заново.", reply_markup=kb.schedule_edit())
        return
    sch = await TeacherSchedule.load(db, teacher)
    name = escape(data["student_name"])
    if data.get("flow") == "add":
        await db.add_weekly(teacher["id"], data["student_id"], data["weekday"], data["time"], duration, None,
                            sch.first_day_for(data["weekday"], data["time"]))
        text = f"✅ Добавлено: каждый {SHORT[data['weekday']]} в {data['time']} — {name} ({duration} мин)."
        text += conflict_note(sch.weekly_conflicts(data["weekday"], data["time"], duration))
        family = (f"📅 Новое регулярное занятие: каждый {WEEKDAYS[data['weekday']].lower()} "
                  f"в {data['time']} ({duration} мин).")
    else:
        await db.add_single(teacher["id"], data["student_id"], data["date"], data["time"], duration, None)
        day = sch.today.fromisoformat(data["date"])
        text = f"✅ Разовое занятие: {day.strftime('%d.%m.%Y')} в {data['time']} — {name} ({duration} мин)."
        text += conflict_note(sch.conflicts(day, data["time"], duration))
        family = f"📅 Назначено занятие: {WEEKDAYS[day.weekday()].lower()}, {day.strftime('%d.%m')} в {data['time']}."
    await state.clear()
    await notify_family(bot, db, teacher, data["student_id"], family)
    await message.answer(text, reply_markup=kb.inline([
        [("Добавить ещё", "sch:add" if data.get("flow") == "add" else "sch:single")],
        kb.back("sch:edit"),
    ]))


# ----- отмена и перенос

@router.callback_query(F.data == "sch:cancel")
async def cancel_ask(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    await state.set_state(Sch.cancel_date)
    rows = []
    for i in range(0, 7, 4):
        row = []
        for j in range(i, min(i + 4, 7)):
            d = sch.today + timedelta(days=j)
            row.append((f"{SHORT[d.weekday()]} {d.strftime('%d.%m')}", f"sch:cd:{d.isoformat()}"))
        rows.append(row)
    rows.append(kb.back("sch:edit"))
    await show(call, "На какую дату отменить или перенести занятие? Выберите или напишите дату:", kb.inline(rows))


async def _lessons_for_cancel(target, db: Database, teacher: dict, day, state: FSMContext):
    sch = await TeacherSchedule.load(db, teacher)
    lessons = sch.on(day)
    cancelled = sch.cancelled_on(day)
    await state.set_state(None)
    if not lessons and not cancelled:
        await show(target, f"{plan_title(day, sch.today)}\n\nЗанятий нет.", kb.inline([kb.back("sch:cancel")]))
        return
    rows = [[(f"{l.time} {l.student_name}", f"sch:cl:{day.isoformat()}:{l.kind}:{l.id}")] for l in lessons]
    for l in cancelled:
        label = "Отменить перенос" if await db.moved_to(l.id, day.isoformat()) else "Вернуть отменённое"
        rows.append([(f"↩️ {label}: {l.time} {l.student_name}", f"sch:rs:{day.isoformat()}:{l.id}")])
    rows.append(kb.back("sch:cancel"))
    await show(target, f"{plan_title(day, sch.today)}\n\nВыберите занятие:", kb.inline(rows))


@router.callback_query(F.data.startswith("sch:rs:"))
async def restore_lesson(call: CallbackQuery, db: Database, teacher: dict, bot: Bot, state: FSMContext):
    from datetime import date
    _, _, day_iso, lid = call.data.split(":")
    if not await db.get_weekly(teacher["id"], int(lid)):
        await call.answer("Занятие не найдено", show_alert=True)
        return
    moved = await db.moved_to(int(lid), day_iso)
    if moved:
        await db.delete_single(teacher["id"], moved)  # перенос отменяется: урок возвращается на прежнее время
    await db.uncancel_on(int(lid), day_iso)
    sch = await TeacherSchedule.load(db, teacher)
    l = next((x for x in sch.on(date.fromisoformat(day_iso)) if x.kind == "w" and x.id == int(lid)), None)
    if l:
        extra = " Перенос отменён." if moved else ""
        await notify_family(bot, db, teacher, l.student_id,
                            f"✅ Занятие {sch.lesson_word(l)} снова в расписании.{extra}")
    await _lessons_for_cancel(call, db, teacher, date.fromisoformat(day_iso), state)


@router.callback_query(F.data.startswith("sch:cd:"))
async def cancel_day_picked(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    from datetime import date
    await _lessons_for_cancel(call, db, teacher, date.fromisoformat(call.data[7:]), state)


@router.message(Sch.cancel_date, TEXT_INPUT)
async def cancel_day_typed(message: Message, state: FSMContext, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    day = parse_date(message.text, sch.today)
    if not day:
        await message.answer("Не понял дату. Пример: <code>25.10</code>")
        return
    await _lessons_for_cancel(message, db, teacher, day, state)


def _find(sch: TeacherSchedule, day_iso: str, kind: str, lesson_id: int):
    from datetime import date
    day = date.fromisoformat(day_iso)
    for l in sch.on(day):
        if l.kind == kind and l.id == lesson_id:
            return l
    return None


@router.callback_query(F.data.startswith("sch:cl:"))
async def cancel_lesson_menu(call: CallbackQuery, db: Database, teacher: dict):
    _, _, day_iso, kind, lid = call.data.split(":")
    sch = await TeacherSchedule.load(db, teacher)
    l = _find(sch, day_iso, kind, int(lid))
    if not l:
        await show(call, "Занятие не найдено — возможно, уже отменено.", kb.inline([kb.back("sch:cancel")]))
        return
    key = f"{day_iso}:{kind}:{lid}"
    await show(call, f"{escape(l.student_name)}, {sch.lesson_word(l)}.\nЧто сделать?", kb.inline([
        [("❌ Отменить занятие", f"sch:cx:{key}")],
        [("🔁 Перенести на другое время", f"sch:mv:{key}")],
        kb.back(f"sch:cd:{day_iso}"),
    ]))


@router.callback_query(F.data.startswith("sch:cx:"))
async def cancel_lesson(call: CallbackQuery, db: Database, teacher: dict, bot: Bot):
    _, _, day_iso, kind, lid = call.data.split(":")
    sch = await TeacherSchedule.load(db, teacher)
    l = _find(sch, day_iso, kind, int(lid))
    if not l:
        await show(call, "Занятие не найдено — возможно, уже отменено.", kb.inline([kb.back("sch:cancel")]))
        return
    if kind == "w":
        await db.cancel_on(l.id, day_iso)
    else:
        await db.delete_single(teacher["id"], l.id)
    await notify_family(bot, db, teacher, l.student_id, f"❌ Занятие {sch.lesson_word(l)} отменено.")
    await show(call, f"Занятие отменено: {escape(l.student_name)}, {sch.lesson_word(l)}.\n"
                     "Ученик и родители получили уведомление, если подключены к боту.",
               kb.inline([[("Отменить ещё", "sch:cancel")], kb.back("sch:menu")]))


@router.callback_query(F.data.startswith("sch:mv:"))
async def move_ask(call: CallbackQuery, state: FSMContext):
    _, _, day_iso, kind, lid = call.data.split(":")
    await state.set_state(Sch.move_date)
    await state.update_data(move=[day_iso, kind, int(lid)])
    await show(call, "На какую дату перенести? Например <code>25.10</code> или <code>завтра</code>.",
               kb.inline([kb.back("sch:cancel")]))


@router.message(Sch.move_date, TEXT_INPUT)
async def move_date(message: Message, state: FSMContext, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    day = parse_date(message.text, sch.today)
    if not day or day < sch.today:
        await message.answer("Не понял дату (или она уже прошла). Пример: <code>25.10</code>")
        return
    await state.update_data(move_to=day.isoformat())
    await state.set_state(Sch.move_time)
    await message.answer("Во сколько? Например <code>16:00</code>.")


@router.message(Sch.move_time, TEXT_INPUT)
async def move_time(message: Message, state: FSMContext, db: Database, teacher: dict, bot: Bot):
    from datetime import date
    parsed = parse_time_range(message.text)
    if not parsed:
        await message.answer("Не понял время. Пример: <code>16:00</code>")
        return
    data = await state.get_data()
    day_iso, kind, lid = data["move"]
    sch = await TeacherSchedule.load(db, teacher)
    l = _find(sch, day_iso, kind, lid)
    await state.clear()
    if not l:
        await message.answer("Исходное занятие не найдено — возможно, уже отменено.")
        return
    time, duration = parsed
    raw = next((s for s in sch.singles if s["id"] == l.id), None) if kind == "s" else \
        next((w for w in sch.weekly if w["id"] == l.id), None)
    new_id = await db.add_single(teacher["id"], l.student_id, data["move_to"], time, duration or l.duration,
                                 raw["price"] if raw else None)
    if kind == "w":
        await db.cancel_on(l.id, day_iso, moved_to=new_id)
    else:
        await db.delete_single(teacher["id"], l.id)
    new_day = date.fromisoformat(data["move_to"])
    new_word = f"{WEEKDAYS[new_day.weekday()].lower()}, {new_day.strftime('%d.%m')} в {time}"
    await notify_family(bot, db, teacher, l.student_id,
                        f"🔁 Занятие {sch.lesson_word(l)} перенесено на {new_word}.")
    clash = conflict_note(sch.conflicts(new_day, time, duration or l.duration, skip=(kind, l.id)))
    await message.answer(f"✅ Перенесено: {escape(l.student_name)} — {new_word}.{clash}",
                         reply_markup=kb.inline([kb.back("sch:menu")]))


# ---------------------------------------------------------------- удаление

@router.callback_query(F.data == "sch:del")
async def delete_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(call, "Что удалить?", kb.schedule_delete())


@router.callback_query(F.data == "sch:delone")
async def delete_one_list(call: CallbackQuery, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    rows = []
    for w in sch.weekly_template():
        st = sch.students.get(w["student_id"])
        if st:
            rows.append([(f"Каждый {SHORT[w['weekday']]} {w['time']} · {st['name']}", f"sch:dw:{w['id']}")])
    for s in sch.singles:
        if s["date"] >= sch.today.isoformat():
            st = sch.students.get(s["student_id"])
            d = sch.today.fromisoformat(s["date"])
            if st:
                rows.append([(f"{d.strftime('%d.%m')} {s['time']} · {st['name']} (разовое)", f"sch:ds:{s['id']}")])
    if not rows:
        await show(call, "Расписание пустое.", kb.inline([kb.back("sch:del")]))
        return
    rows.append(kb.back("sch:del"))
    await show(call, "Какое занятие удалить? Прошлые занятия останутся в доходе.", kb.inline(rows))


@router.callback_query(F.data.regexp(r"^sch:d[ws]:\d+$"))
async def delete_one(call: CallbackQuery, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    kind, lid = call.data[5], int(call.data.split(":")[2])
    if kind == "w":
        lesson = await db.get_weekly(teacher["id"], lid)
        if lesson:
            await db.end_weekly(teacher["id"], lid, sch.last_day_for(lesson))
    else:
        await db.delete_single(teacher["id"], lid)
    await delete_one_list(call, db, teacher)


@router.callback_query(F.data == "sch:delall")
async def delete_all_ask(call: CallbackQuery):
    await show(call, "Удалить всё расписание — регулярные и будущие разовые занятия?\n"
                     "Ученики и история дохода останутся.",
               kb.confirm("sch:delall:yes", "sch:del"))


@router.callback_query(F.data == "sch:delall:yes")
async def delete_all(call: CallbackQuery, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    n = await sch.end_all(db)
    m = await db.delete_future_singles(teacher["id"], sch.today.isoformat(), sch.now.strftime("%H:%M"))
    await show(call, f"Расписание удалено (регулярных: {n}, разовых: {m}).", kb.inline([kb.back("sch:menu")]))
