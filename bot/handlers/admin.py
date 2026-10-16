"""Админ-панель владельца бота (ADMIN_IDS в .env): статистика, промокоды, ручное продление."""
import secrets
import string
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import subscription
from ..config import Config
from ..db import Database
from ..filters import TEXT_INPUT, IsAdmin
from ..promo import CODE_RE, days_text, promo_link
from ..ui import show

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class Adm(StatesGroup):
    code = State()


async def _panel(target, db: Database):
    s = await db.stats()
    await show(target,
               "🛠 <b>Админ-панель</b>\n\n"
               f"Преподавателей: {s['teachers']}\n"
               f"Учеников: {s['students']}\n"
               f"Аккаунтов: {s['accounts']}\n"
               f"Оплат подписки: {s['sub_payments']['n']} на {s['sub_payments']['s']} ₽\n\n"
               "Ответить на обращение в поддержку: смахните его сообщение влево и напишите ответ.",
               kb.inline([
                   [("🎁 Промокоды", "adm:promos")],
                   [("👩‍🏫 Преподаватели", "adm:teachers")],
               ]))


@router.message(Command("admin"))
@router.message(F.text == kb.B.ADMIN)
async def admin_cmd(message: Message, state: FSMContext, db: Database):
    await state.clear()
    await _panel(message, db)


@router.callback_query(F.data == "adm:menu")
async def admin_menu(call: CallbackQuery, state: FSMContext, db: Database):
    await state.clear()
    await _panel(call, db)


@router.callback_query(F.data == "adm:teachers")
@router.message(Command("teachers"))
async def teachers(event, db: Database):
    lines = []
    for t in await db.all_teachers():
        st = subscription.status(t)
        if st.kind == "free":
            state = "♾ бесплатно"
        elif st.active:
            state = ("пробный до " if st.kind == "trial" else "оплачено до ") + st.until.strftime("%d.%m.%Y")
        else:
            state = "не активна"
        lines.append(f"• {escape(t['name'])} · {state}\n  секрет входа: <code>{t['teacher_secret']}</code>")
    total = len(lines)
    while len("\n".join(lines)) > 3500:  # лимит сообщения Telegram — 4096 символов
        lines.pop()
    more = f"\n…и ещё {total - len(lines)}" if len(lines) < total else ""
    text = f"👩‍🏫 <b>Преподаватели</b> ({total})\n\n" + ("\n".join(lines) + more if lines else "Пока никого.")
    text += "\n\nПродлить вручную: <code>/grant СЕКРЕТ ДНЕЙ</code>"
    await show(event, text, kb.inline([kb.back("adm:menu")]))


# ---------------------------------------------------------------- промокоды

@router.callback_query(F.data == "adm:promos")
async def promos(call: CallbackQuery, state: FSMContext, db: Database):
    await state.clear()
    items = await db.promos()
    rows = []
    for p in items:
        limit = f"{p['used']}/{p['max_uses']}" if p["max_uses"] is not None else f"{p['used']}/∞"
        mark = "" if p["active"] else " (выкл)"
        rows.append([(f"{p['code']} · {limit}{mark}", f"adm:p:{p['code']}")])
    rows.append([("➕ Новый промокод", "adm:new")])
    rows.append(kb.back("adm:menu"))
    text = ("🎁 <b>Промокоды</b>\n\nПромокод даёт репетитору бесплатные дни подписки. "
            "Удобнее всего отправить подруге ссылку с промокодом: она откроет бота, зарегистрируется, "
            "и промокод применится сам.")
    if not items:
        text += "\n\nПромокодов пока нет."
    await show(call, text, kb.inline(rows))


@router.callback_query(F.data == "adm:new")
async def new_days(call: CallbackQuery):
    await show(call, "Сколько бесплатных дней даёт промокод?", kb.inline([
        [("30 дней", "adm:nd:30"), ("90 дней", "adm:nd:90")],
        [("180 дней", "adm:nd:180"), ("365 дней", "adm:nd:365")],
        [("♾ Навсегда", "adm:nd:0")],
        kb.back("adm:promos"),
    ]))


@router.callback_query(F.data.startswith("adm:nd:"))
async def new_uses(call: CallbackQuery):
    days = call.data.split(":")[2]
    await show(call, "Сколько человек смогут его активировать?", kb.inline([
        [("1", f"adm:nu:{days}:1"), ("5", f"adm:nu:{days}:5"), ("10", f"adm:nu:{days}:10")],
        [("Без ограничений", f"adm:nu:{days}:0")],
        kb.back("adm:new"),
    ]))


@router.callback_query(F.data.startswith("adm:nu:"))
async def new_code_ask(call: CallbackQuery, state: FSMContext):
    _, _, days, uses = call.data.split(":")
    await state.set_state(Adm.code)
    await state.update_data(days=int(days), uses=int(uses) or None)
    await show(call, "Напишите сам промокод латинскими буквами и цифрами, например <code>OLGA2026</code>, "
                     "или нажмите «Придумай сам».",
               kb.inline([[("🎲 Придумай сам", "adm:gen")], kb.back("adm:promos")]))


async def _create(target, state: FSMContext, db: Database, bot: Bot, code: str):
    data = await state.get_data()
    if "days" not in data:
        await state.clear()
        await show(target, "Начните заново: /admin")
        return
    code = code.upper()
    if not await db.create_promo(code, data["days"], data["uses"]):
        await show(target, "Такой промокод уже есть. Напишите другой:")
        return
    await state.clear()
    uses = f"{data['uses']} {'активация' if data['uses'] == 1 else 'активаций'}" if data["uses"] else "без ограничений"
    link = await promo_link(bot, code)
    await show(target,
               f"✅ Промокод <code>{code}</code> создан: {days_text(data['days'])}, {uses}.\n\n"
               "Перешлите подруге сообщение ниже 👇",
               kb.inline([kb.back("adm:promos", "К промокодам")]))
    msg = target.message if isinstance(target, CallbackQuery) else target
    await msg.answer(
        f"Привет! Попробуй бота для репетиторов: расписание, ученики, доход и напоминания ученикам.\n"
        f"По этой ссылке тебе {days_text(data['days'])} 🎁\n{link}\n\n"
        f"Или введи промокод <code>{code}</code> в разделе «Моя подписка».",
        disable_web_page_preview=True)


@router.callback_query(Adm.code, F.data == "adm:gen")
async def gen_code(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot):
    code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    await _create(call, state, db, bot, code)


@router.message(Adm.code, TEXT_INPUT)
async def typed_code(message: Message, state: FSMContext, db: Database, bot: Bot):
    code = message.text.strip()
    if not CODE_RE.match(code):
        await message.answer("Только латинские буквы и цифры, от 3 до 24 символов. Например: <code>OLGA2026</code>")
        return
    await _create(message, state, db, bot, code)


@router.callback_query(F.data.startswith("adm:p:"))
async def promo_card(call: CallbackQuery, db: Database, bot: Bot):
    p = await db.get_promo(call.data[6:])
    if not p:
        await call.answer("Промокод не найден", show_alert=True)
        return
    limit = str(p["max_uses"]) if p["max_uses"] is not None else "без ограничений"
    await show(call,
               f"🎁 <code>{p['code']}</code>\n{days_text(p['days'])}\n"
               f"Активаций: {p['used']} из {limit}\n"
               f"Статус: {'действует' if p['active'] else 'выключен'}\n\n"
               f"Ссылка: {await promo_link(bot, p['code'])}",
               kb.inline([
                   [("Выключить" if p["active"] else "Включить", f"adm:t:{p['code']}")],
                   kb.back("adm:promos"),
               ]))


@router.callback_query(F.data.startswith("adm:t:"))
async def promo_toggle(call: CallbackQuery, db: Database, bot: Bot):
    p = await db.get_promo(call.data[6:])
    if p:
        await db.set_promo_active(p["code"], not p["active"])
    p = await db.get_promo(call.data[6:])
    await show(call,
               f"Промокод <code>{p['code']}</code> {'включён' if p['active'] else 'выключен'}.",
               kb.inline([kb.back("adm:promos", "К промокодам")]))


# ---------------------------------------------------------------- ручное продление

@router.message(Command("grant"))
async def grant(message: Message, command: CommandObject, db: Database, bot: Bot, config: Config):
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Формат: <code>/grant СЕКРЕТ_УЧИТЕЛЯ ДНЕЙ</code>")
        return
    teacher = await db.teacher_by_teacher_secret(parts[0])
    if not teacher:
        await message.answer("Преподаватель с таким секретом не найден.")
        return
    days = int(parts[1])
    until = subscription.extend_paid(teacher, days)
    await db.update_teacher(teacher["id"], paid_until=until)
    await db.log_sub_payment(teacher["id"], 0, days, "manual")
    teacher = await db.get_teacher(teacher["id"])
    st = subscription.status(teacher)
    when = subscription.fmt_dt(st.until, teacher["tz_offset"]) if st.until else "без срока"
    await message.answer(f"Готово: {escape(teacher['name'])}, доступ до {when}.")
    for tg_id in await db.teacher_accounts(teacher["id"]):
        try:
            await bot.send_message(tg_id, f"🎁 Подписка продлена на {days} дн.\n\n"
                                   + subscription.describe(teacher, config.price_rub, config.trial_days))
        except Exception:
            pass
