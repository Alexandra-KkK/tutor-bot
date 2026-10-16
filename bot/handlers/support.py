"""Переписка: преподаватель ↔ поддержка (админы), ученик/родитель ↔ преподаватель.

Каждое пересланное сообщение запоминается, и ответ на него (свайп → «Ответить»)
доставляется обратно отправителю. Так получается переписка без раскрытия контактов.
"""
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..config import Config
from ..db import Database
from ..keyboards import ALL_MENU_BUTTONS, B
from ..ui import show

router = Router(name="support")


class Sup(StatesGroup):
    text = State()


class IsLinkedReply(BaseFilter):
    async def __call__(self, message: Message, db: Database) -> bool | dict:
        if not message.reply_to_message:
            return False
        target = await db.support_user(message.chat.id, message.reply_to_message.message_id)
        return {"reply_target": target} if target else False


def sender_label(account: dict | None, teacher: dict | None, student: dict | None, user) -> str:
    if account is None:
        return f"{escape(user.full_name)}"
    if account["role"] == "teacher":
        return f"👩‍🏫 {escape(teacher['name'])}"
    who = "ученик" if account["role"] == "student" else "родитель"
    name = escape(student["name"]) if student else "?"
    return f"👤 {name} ({who})"


async def family_label(db: Database, account: dict, teacher: dict) -> str:
    names = []
    for sid in await db.children(account["tg_id"]):
        st = await db.get_student(teacher["id"], sid)
        if st:
            names.append(escape(st["name"]))
    who = "ученик" if account["role"] == "student" else "родитель"
    return f"👤 {', '.join(names) or '?'} ({who})"


async def deliver(bot: Bot, db: Database, message: Message, target: int, header: str) -> bool:
    """Отправить заголовок + копию сообщения и запомнить связь для ответа."""
    try:
        head = await bot.send_message(target, header)
        copy = await message.copy_to(target)
    except Exception:
        return False
    for msg_id in (head.message_id, copy.message_id):
        await db.save_support_link(target, msg_id, message.chat.id)
    return True


# ---------------------------------------------------------------- начать обращение

@router.message(F.text == B.SUPPORT)
async def support_start(message: Message, state: FSMContext, account: dict | None):
    await state.clear()
    await _ask(message, state, account)


@router.callback_query(F.data == "sup:start")
async def support_start_cb(call: CallbackQuery, state: FSMContext, account: dict | None):
    await _ask(call, state, account)


async def _ask(target, state: FSMContext, account: dict | None):
    await state.set_state(Sup.text)
    if account and account["role"] != "teacher":
        text = ("✍️ Напишите сообщение преподавателю — текст, фото или голосовое. "
                "Я передам его, а ответ придёт сюда.")
    else:
        text = ("✍️ Опишите вопрос или проблему одним сообщением — можно с фото или скриншотом. "
                "Ответ придёт сюда.")
    await show(target, text)


@router.message(Sup.text, ~F.successful_payment, ~F.text.in_(ALL_MENU_BUTTONS), ~F.text.startswith("/"))
async def support_message(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config,
                          account: dict | None, teacher: dict | None, student: dict | None):
    await state.clear()
    user = message.from_user
    if account and account["role"] != "teacher":
        targets = await db.teacher_accounts(account["teacher_id"])
        header = await family_label(db, account, teacher) + " пишет:"
        hint = "\n\n<i>Чтобы ответить, смахните это сообщение влево (или «Ответить»).</i>"
    else:
        targets = list(config.admin_ids)
        name = escape(teacher["name"]) if teacher else escape(user.full_name)
        contact = f" @{user.username}" if user.username else ""
        header = f"🆘 Обращение в поддержку\nОт: {name}{contact} (id {user.id})"
        hint = "\n\n<i>Ответьте на это сообщение, чтобы написать пользователю.</i>"
    if not targets:
        await message.answer("Поддержка пока не настроена. Попробуйте позже.")
        return
    ok = False
    for t in targets:
        ok = await deliver(bot, db, message, t, header + hint) or ok
    if ok:
        await message.answer("✅ Сообщение отправлено. Ответ придёт в этот чат.")
    else:
        await message.answer("Не получилось отправить сообщение. Попробуйте позже.")


# ---------------------------------------------------------------- ответы

@router.message(IsLinkedReply())
async def linked_reply(message: Message, bot: Bot, db: Database, config: Config, reply_target: int,
                       account: dict | None, teacher: dict | None, student: dict | None):
    target_acc = await db.get_account(reply_target)
    same_circle = bool(account and target_acc and account["teacher_id"] == target_acc["teacher_id"])
    if message.from_user.id in config.admin_ids and not same_circle:
        label = "💬 Поддержка"
    else:
        label = (await family_label(db, account, teacher) if account and account["role"] != "teacher"
                 else sender_label(account, teacher, student, message.from_user))
    header = f"{label} отвечает:\n<i>Чтобы ответить, смахните сообщение влево.</i>"
    if await deliver(bot, db, message, reply_target, header):
        await message.reply("✅ Доставлено")
    else:
        await message.reply("Не удалось доставить: пользователь, возможно, заблокировал бота.")
