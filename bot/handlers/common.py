"""Старт, регистрация, «Отмена» и «Рестарт профиля»."""
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from .. import keyboards as kb
from .. import subscription
from ..config import Config
from ..db import Database
from ..filters import TEXT_INPUT, NoAccount
from ..keyboards import B
from ..promo import apply_promo
from ..texts import WELCOME, TEACHER_HELP, family_help, need_subscription_text, secrets_text
from ..ui import send_menu, show

router = Router(name="common")


class Reg(StatesGroup):
    teacher_name = State()
    teacher_secret = State()
    student_secret = State()
    child_name = State()


# ---------------------------------------------------------------- /start

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, db: Database,
                    account: dict | None, teacher: dict | None):
    await state.clear()
    arg = (command.args or "").strip()
    if arg.startswith("promo_"):
        code = arg[len("promo_"):]
        if account and account["role"] == "teacher":
            ok, text = await apply_promo(db, teacher, code, message.from_user.id)
            await send_menu(message, account, text)
            return
        if not account:
            await state.update_data(pending_promo=code)
            await message.answer(f"🎁 У вас промокод <b>{escape(code.upper())}</b>. "
                                 "Зарегистрируйтесь как преподаватель, и он применится сам.")
            await message.answer(WELCOME, reply_markup=kb.start_roles())
            return
        arg = ""
    if account and not arg:
        name = escape(teacher["name"]) if account["role"] == "teacher" else ""
        await send_menu(message, account, f"С возвращением{', ' + name if name else ''}! Главное меню:")
        return
    if arg:
        secret, _, sid = arg.partition("_")
        target = await db.teacher_by_student_secret(secret)
        if target:
            if account and account["role"] == "teacher":
                await message.answer("Это приглашение для учеников и родителей, а вы вошли как преподаватель.")
                return
            invited = await db.get_student(target["id"], int(sid)) if sid.isascii() and sid.isdigit() else None
            if invited and not invited["active"]:
                invited = None
            await state.update_data(join_teacher=target["id"], join_student=invited["id"] if invited else None)
            who = f"\nУченик: <b>{escape(invited['name'])}</b>" if invited else ""
            await message.answer(
                f"Вас пригласил преподаватель <b>{escape(target['name'])}</b>.{who}\n\nКто вы?",
                reply_markup=kb.inline([[("Я ученик", "join:student"), ("Я родитель", "join:parent")]]),
            )
            return
    await message.answer(WELCOME, reply_markup=kb.start_roles())


# ---------------------------------------------------------------- регистрация преподавателя

@router.callback_query(F.data == "reg:teacher")
async def reg_teacher(call: CallbackQuery, state: FSMContext, account: dict | None):
    if account:
        await call.answer("Вы уже зарегистрированы. Чтобы начать заново — «Рестарт профиля».", show_alert=True)
        return
    await state.set_state(Reg.teacher_name)
    await show(call, "Как вас зовут? Это имя увидят ученики и родители.\nНапример: <i>Александра Сергеевна</i>")


@router.message(Reg.teacher_name, TEXT_INPUT)
async def reg_teacher_name(message: Message, state: FSMContext, db: Database, bot: Bot, config: Config):
    name = message.text.strip()[:64]
    pending_promo = (await state.get_data()).get("pending_promo")
    teacher = await db.create_teacher(name, config.default_tz)
    await db.link_account(message.from_user.id, "teacher", teacher["id"],
                          full_name=message.from_user.full_name, username=message.from_user.username or "")
    if not config.admin_ids:
        # ADMIN_IDS не задан: владельцем становится первый зарегистрированный преподаватель
        config.admin_ids.add(message.from_user.id)
        kb.ADMIN_IDS.add(message.from_user.id)
        await db.set_setting("owner_id", str(message.from_user.id))
    if message.from_user.id in config.admin_ids:
        await db.update_teacher(teacher["id"], free_forever=1)  # владелец бота не платит
    if await db.has_perk(message.from_user.id, "trial"):
        await db.update_teacher(teacher["id"], trial_used=1)  # пробный период даётся один раз
    teacher = await db.get_teacher(teacher["id"])
    await state.clear()
    account = await db.get_account(message.from_user.id)
    await send_menu(message, account, TEACHER_HELP)
    await message.answer(await secrets_text(bot, teacher))
    if pending_promo:
        ok, text = await apply_promo(db, teacher, pending_promo, message.from_user.id)
        await message.answer(text)
    teacher = await db.get_teacher(teacher["id"])
    if not subscription.status(teacher).active:
        await message.answer(need_subscription_text(),
                             reply_markup=kb.subscription(teacher, False, config.price_rub, config.trial_days))


TEACHER_BUSY = "Вы уже вошли как преподаватель. Чтобы сменить роль, нажмите «🔄 Рестарт профиля»."


def _is_teacher(account: dict | None) -> bool:
    return bool(account and account["role"] == "teacher")


@router.callback_query(F.data == "reg:login")
async def reg_login(call: CallbackQuery, state: FSMContext, account: dict | None):
    if _is_teacher(account):
        await call.answer(TEACHER_BUSY, show_alert=True)
        return
    await state.set_state(Reg.teacher_secret)
    await show(call, "Введите секрет «Вход учителя с других аккаунтов».\n"
                     "Его можно найти в разделе «Мои секреты» на основном аккаунте.")


@router.message(Reg.teacher_secret, TEXT_INPUT)
async def reg_login_secret(message: Message, state: FSMContext, db: Database, config: Config):
    teacher = await db.teacher_by_teacher_secret(message.text)
    if not teacher:
        await message.answer("Такой секрет не найден. Проверьте и отправьте ещё раз или нажмите /start.")
        return
    await db.link_account(message.from_user.id, "teacher", teacher["id"],
                          full_name=message.from_user.full_name, username=message.from_user.username or "")
    if message.from_user.id in config.admin_ids:
        await db.update_teacher(teacher["id"], free_forever=1)
    await state.clear()
    account = await db.get_account(message.from_user.id)
    await send_menu(message, account, f"Вы вошли в профиль <b>{escape(teacher['name'])}</b>.")


# ---------------------------------------------------------------- регистрация ученика и родителя

@router.callback_query(F.data == "reg:student")
async def reg_student(call: CallbackQuery, state: FSMContext, account: dict | None):
    if _is_teacher(account):
        await call.answer(TEACHER_BUSY, show_alert=True)
        return
    await state.set_state(Reg.student_secret)
    await show(call, "Отправьте «Секрет для регистрации учеников», который дал преподаватель.\n"
                     "Или просто откройте ссылку-приглашение от преподавателя.")


@router.message(Reg.student_secret, TEXT_INPUT)
async def reg_student_secret(message: Message, state: FSMContext, db: Database):
    teacher = await db.teacher_by_student_secret(message.text.strip().partition("_")[0])
    if not teacher:
        await message.answer("Такой секрет не найден. Проверьте и отправьте ещё раз.")
        return
    await state.set_state(None)
    await state.update_data(join_teacher=teacher["id"], join_student=None)
    await message.answer(
        f"Преподаватель: <b>{escape(teacher['name'])}</b>.\nКто вы?",
        reply_markup=kb.inline([[("Я ученик", "join:student"), ("Я родитель", "join:parent")]]),
    )


@router.callback_query(F.data.in_({"join:student", "join:parent"}))
async def join_role(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot, account: dict | None):
    if _is_teacher(account):
        await call.answer(TEACHER_BUSY, show_alert=True)
        return
    data = await state.get_data()
    teacher = await db.get_teacher(data.get("join_teacher") or 0)
    if not teacher:
        await show(call, "Приглашение устарело. Откройте ссылку от преподавателя ещё раз.")
        return
    role = call.data.split(":")[1]
    await state.update_data(join_role=role)
    invited = data.get("join_student") and await db.get_student(teacher["id"], data["join_student"])
    if invited and invited["active"]:
        await call.answer()
        await call.message.edit_reply_markup(reply_markup=None)
        await _finish_join(call.message, state, db, bot, teacher, invited["id"], role, call.from_user)
        return
    await state.set_state(Reg.child_name)
    text = "Напишите ваше имя и фамилию:" if role == "student" else "Напишите имя и фамилию ребёнка:"
    await show(call, text)


@router.message(Reg.child_name, TEXT_INPUT)
async def join_new_name(message: Message, state: FSMContext, db: Database, bot: Bot):
    data = await state.get_data()
    teacher = await db.get_teacher(data.get("join_teacher") or 0)
    if not teacher:
        await state.clear()
        await message.answer("Приглашение устарело. Откройте ссылку от преподавателя ещё раз.")
        return
    name = " ".join(message.text.split())[:64]
    student = await db.match_student(teacher["id"], name)
    student_id = student["id"] if student else await db.add_student(teacher["id"], name)
    await _finish_join(message, state, db, bot, teacher, student_id, data.get("join_role", "student"), message.from_user)


async def _finish_join(message: Message, state: FSMContext, db: Database, bot: Bot,
                       teacher: dict, student_id: int, role: str, user) -> None:
    await db.link_account(user.id, role, teacher["id"], student_id,
                          full_name=user.full_name, username=user.username or "")
    await state.clear()
    student = await db.get_student(teacher["id"], student_id)
    account = await db.get_account(user.id)
    await send_menu(message, account, family_help(role, teacher))
    who = "Ученик" if role == "student" else "Родитель ученика"
    contact = f" (@{user.username})" if user.username else ""
    markup = kb.inline([[("Это другой ученик", f"rel:{user.id}:{student_id}")]])
    for tg_id in await db.teacher_accounts(teacher["id"]):
        try:
            await bot.send_message(
                tg_id, f"🔔 {who} <b>{escape(student['name'])}</b> подключился к боту: "
                       f"{escape(user.full_name)}{contact}.", reply_markup=markup)
        except Exception:
            pass


# ---------------------------------------------------------------- отмена и рестарт

@router.message(F.text == B.CANCEL)
@router.message(StateFilter("*"), F.text == "/cancel")
async def cancel(message: Message, state: FSMContext, account: dict | None):
    await state.clear()
    if account:
        await send_menu(message, account, "Действие отменено. Главное меню:")
    else:
        await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
        await message.answer(WELCOME, reply_markup=kb.start_roles())


@router.message(F.text == B.RESTART)
async def restart(message: Message, state: FSMContext, db: Database, account: dict | None):
    await state.clear()
    if not account:
        await message.answer(WELCOME, reply_markup=kb.start_roles())
        return
    if account["role"] == "teacher":
        others = [t for t in await db.teacher_accounts(account["teacher_id"]) if t != message.from_user.id]
        if others:
            text = ("Этот аккаунт будет отключён от профиля преподавателя. "
                    "Данные останутся доступны на других ваших аккаунтах. Продолжить?")
        else:
            text = ("⚠️ Профиль будет удалён полностью: расписание, ученики, доход и подписка. "
                    "Ученики и родители тоже будут отключены. Это нельзя отменить.\n\nУдалить профиль?")
    else:
        text = "Вы будете отключены от преподавателя. Подключиться снова можно по ссылке-приглашению. Продолжить?"
    await message.answer(text, reply_markup=kb.confirm("restart:yes", "restart:no", "Да", "Нет"))


@router.callback_query(F.data == "restart:no")
async def restart_no(call: CallbackQuery):
    await show(call, "Хорошо, ничего не меняю.")


@router.callback_query(F.data == "restart:yes")
async def restart_yes(call: CallbackQuery, state: FSMContext, db: Database, account: dict | None):
    await state.clear()
    if account:
        if account["role"] == "teacher":
            others = [t for t in await db.teacher_accounts(account["teacher_id"]) if t != call.from_user.id]
            if others:
                await db.unlink_account(call.from_user.id)
            else:
                await db.delete_teacher(account["teacher_id"])
        else:
            await db.unlink_account(call.from_user.id)
    await show(call, "Профиль сброшен.")
    await call.message.answer("Начнём заново.", reply_markup=ReplyKeyboardRemove())
    await call.message.answer(WELCOME, reply_markup=kb.start_roles())


@router.callback_query(F.data == "menu:close")
async def menu_close(call: CallbackQuery):
    await call.answer()
    try:
        await call.message.delete()
    except Exception:
        await call.message.edit_reply_markup(reply_markup=None)


@router.message(NoAccount())
async def not_registered(message: Message):
    await message.answer(WELCOME, reply_markup=kb.start_roles())


@router.callback_query(NoAccount(), ~F.data.startswith("reg:"), ~F.data.startswith("join:"))
async def not_registered_cb(call: CallbackQuery):
    await call.answer("Сначала нажмите /start", show_alert=True)
