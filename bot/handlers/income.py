"""Раздел «Доход»: проведённые занятия, оплаты учеников, долги."""
from datetime import date
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..db import Database
from ..filters import TEXT_INPUT
from ..keyboards import B
from ..logic import (MONTHS_NOM, income, money, month_bounds, parse_amount, plural,
                     prev_month_bounds, week_bounds)
from ..ui import show
from ..views import TeacherSchedule, earned_by_student

router = Router(name="income")


class Inc(StatesGroup):
    amount = State()


def _menu() -> kb.InlineKeyboardMarkup:
    return kb.inline([
        [("Эта неделя", "inc:p:week"), ("Этот месяц", "inc:p:month")],
        [("Прошлый месяц", "inc:p:prev")],
        [("💵 Внести оплату", "inc:pay"), ("📒 История оплат", "inc:hist")],
        [("⚠️ Долги учеников", "inc:debts")],
        kb.back("menu:close"),
    ])


def _lessons(n: int) -> str:
    return f"{n} {plural(n, 'занятие', 'занятия', 'занятий')}"


async def _summary(target, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    ws, we = week_bounds(sch.today)
    ms, me = month_bounds(sch.today)
    week = income(ws, we, sch.now, sch.weekly, sch.singles, sch.cancelled, sch.students)
    month = income(ms, me, sch.now, sch.weekly, sch.singles, sch.cancelled, sch.students)
    paid = sum(p["amount"] for p in await db.payments(teacher["id"], ms.isoformat(), me.isoformat()))
    text = (
        "💰 <b>Доход</b>\n\n"
        f"Эта неделя: <b>{money(week.total)}</b> ({_lessons(week.count)})\n"
        f"{MONTHS_NOM[sch.today.month - 1]}: <b>{money(month.total)}</b> ({_lessons(month.count)})\n"
        f"Ещё запланировано в этом месяце: {money(month.planned_total)} ({_lessons(month.planned_count)})\n"
        f"Прогноз на месяц: {money(month.total + month.planned_total)}\n\n"
        f"Получено оплат в этом месяце: {money(paid)}\n\n"
        "<i>Доход считается по проведённым занятиям из расписания: отменённые не учитываются.</i>"
    )
    await show(target, text, _menu())


@router.message(F.text == B.INCOME)
async def open_income(message: Message, state: FSMContext, db: Database, teacher: dict):
    await state.clear()
    await _summary(message, db, teacher)


@router.callback_query(F.data == "inc:menu")
async def income_menu(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await state.clear()
    await _summary(call, db, teacher)


BACK = kb.inline([kb.back("inc:menu")])


@router.callback_query(F.data.startswith("inc:p:"))
async def period(call: CallbackQuery, db: Database, teacher: dict):
    sch = await TeacherSchedule.load(db, teacher)
    which = call.data.split(":")[2]
    if which == "week":
        start, end = week_bounds(sch.today)
        title = f"Неделя {start.strftime('%d.%m')}–{end.strftime('%d.%m')}"
    elif which == "month":
        start, end = month_bounds(sch.today)
        title = f"{MONTHS_NOM[start.month - 1]} {start.year}"
    else:
        start, end = prev_month_bounds(sch.today)
        title = f"{MONTHS_NOM[start.month - 1]} {start.year}"
    inc = income(start, end, sch.now, sch.weekly, sch.singles, sch.cancelled, sch.students)
    paid = sum(p["amount"] for p in await db.payments(teacher["id"], start.isoformat(), end.isoformat()))
    lines = [f"💰 <b>{title}</b>", "", f"Проведено: {_lessons(inc.count)} на <b>{money(inc.total)}</b>"]
    if inc.planned_count:
        lines.append(f"Впереди: {_lessons(inc.planned_count)} на {money(inc.planned_total)}")
    lines.append(f"Получено оплат: {money(paid)}")
    if inc.by_student:
        lines.append("\nПо ученикам:")
        for name, cnt, amount in sorted(inc.by_student.values(), key=lambda x: -x[2]):
            lines.append(f"• {escape(name)} — {_lessons(cnt)}, {money(amount)}")
    await show(call, "\n".join(lines), BACK)


# ---------------------------------------------------------------- оплаты

@router.callback_query(F.data == "inc:pay")
async def pay_pick(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await state.clear()
    students = await db.students(teacher["id"])
    if not students:
        await show(call, "Сначала добавьте учеников в разделе «Ученики».", BACK)
        return
    await show(call, "От кого оплата?", kb.students_picker(students, "inc:pay:", "inc:menu"))


@router.callback_query(F.data.regexp(r"^inc:pay:\d+$"))
async def pay_amount_ask(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    sid = int(call.data.split(":")[2])
    s = await db.get_student(teacher["id"], sid)
    if not s:
        await call.answer("Ученик не найден", show_alert=True)
        return
    await state.set_state(Inc.amount)
    await state.update_data(sid=sid)
    rows = []
    if s["price"]:
        rows.append([(money(s["price"] * n), f"inc:amt:{sid}:{s['price'] * n}") for n in (1, 4, 8)])
    rows.append(kb.back("inc:pay"))
    await show(call, f"Оплата · <b>{escape(s['name'])}</b>. Выберите сумму или напишите её:", kb.inline(rows))


async def _save_payment(message: Message, state: FSMContext, db: Database, teacher: dict, amount: int):
    data = await state.get_data()
    await state.clear()
    sch = await TeacherSchedule.load(db, teacher)
    s = await db.get_student(teacher["id"], data["sid"])
    await db.add_payment(teacher["id"], data["sid"], amount, sch.today.isoformat())
    await message.answer(f"✅ Оплата записана: {escape(s['name'])} — {money(amount)}.",
                         reply_markup=kb.inline([[("Карточка ученика", f"stu:card:{s['id']}")], kb.back("inc:menu")]))


@router.message(Inc.amount, TEXT_INPUT)
async def pay_amount_typed(message: Message, state: FSMContext, db: Database, teacher: dict):
    amount = parse_amount(message.text)
    if not amount:
        await message.answer("Напишите сумму числом, например <code>6000</code>.")
        return
    await _save_payment(message, state, db, teacher, amount)


@router.callback_query(Inc.amount, F.data.regexp(r"^inc:amt:\d+:\d+$"))
async def pay_amount_button(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    _, _, sid, amount = call.data.split(":")
    if (await state.get_data()).get("sid") != int(sid):  # кнопка от другого ученика
        await call.answer("Эта кнопка устарела", show_alert=True)
        return
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await _save_payment(call.message, state, db, teacher, int(amount))


@router.callback_query(F.data == "inc:hist")
async def history(call: CallbackQuery, db: Database, teacher: dict):
    payments = (await db.payments(teacher["id"]))[:15]
    students = await db.students_map(teacher["id"])
    if not payments:
        await show(call, "Оплат пока нет. Нажмите «Внести оплату», когда ученик заплатит.", BACK)
        return
    rows = []
    lines = ["📒 Последние оплаты:\n"]
    for p in payments:
        name = students.get(p["student_id"], {}).get("name", "—")
        d = date.fromisoformat(p["date"]).strftime("%d.%m")
        lines.append(f"{d} · {escape(name)} · {money(p['amount'])}")
        rows.append([(f"🗑 {d} {name} {money(p['amount'])}", f"inc:delp:{p['id']}")])
    rows.append(kb.back("inc:menu"))
    lines.append("\nОшиблись? Нажмите на оплату, чтобы удалить её.")
    await show(call, "\n".join(lines), kb.inline(rows))


@router.callback_query(F.data.startswith("inc:delp:"))
async def delete_payment(call: CallbackQuery, db: Database, teacher: dict):
    await db.delete_payment(teacher["id"], int(call.data.split(":")[2]))
    await call.answer("Оплата удалена")
    await history(call, db, teacher)


async def balances(db: Database, teacher: dict) -> dict[int, int]:
    """student_id -> оплачено минус стоимость проведённых занятий."""
    sch = await TeacherSchedule.load(db, teacher)
    result = {sid: -amount for sid, (_, amount) in earned_by_student(sch).items()}
    for p in await db.payments(teacher["id"]):
        if p["student_id"]:
            result[p["student_id"]] = result.get(p["student_id"], 0) + p["amount"]
    return result


@router.callback_query(F.data == "inc:debts")
async def debts(call: CallbackQuery, db: Database, teacher: dict):
    bal = await balances(db, teacher)
    lines, rows = [], []
    for s in await db.students(teacher["id"], include_inactive=True):
        balance = bal.get(s["id"], 0)
        if not s["active"] and balance >= 0:
            continue  # удалённые ученики показываются, только если остался долг
        if balance < 0:
            mark = "" if s["active"] else " (удалён)"
            lines.append(f"• {escape(s['name'])}{mark} — долг <b>{money(-balance)}</b>")
            rows.append([(f"💵 {s['name']}: внести оплату", f"inc:pay:{s['id']}")])
        elif balance > 0:
            lines.append(f"• {escape(s['name'])} — предоплата {money(balance)}")
    rows.append(kb.back("inc:menu"))
    text = "⚠️ Расчёты с учениками:\n\n" + ("\n".join(lines) if lines else "Все рассчитались 🎉")
    text += "\n\n<i>Долг = стоимость проведённых занятий минус внесённые оплаты.</i>"
    await show(call, text, kb.inline(rows))
