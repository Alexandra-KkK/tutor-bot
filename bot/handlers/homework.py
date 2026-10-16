"""Домашнее задание: преподаватель отправляет текст, фото или файл ученику и родителям."""
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..db import Database
from ..keyboards import ALL_MENU_BUTTONS
from ..ui import show

router = Router(name="homework")


class Hw(StatesGroup):
    content = State()


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
                     "или файл. Я перешлю ученику и родителям.",
               kb.inline([kb.back(f"stu:card:{sid}", "Отмена")]))


@router.callback_query(F.data.regexp(r"^hw:done:\d+$"))
async def homework_done(call: CallbackQuery, state: FSMContext):
    await state.clear()
    sid = call.data.split(":")[2]
    await show(call, "Готово 👍", kb.inline([[("Карточка ученика", f"stu:card:{sid}")]]))


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
    await state.update_data(headed=True, album=message.media_group_id)
    sent = 0
    for acc in accounts:
        head = ("📝 Домашнее задание" if acc["role"] == "student"
                else f"📝 Домашнее задание · <b>{escape(s['name'])}</b>")
        try:
            if first:
                await bot.send_message(acc["tg_id"], f"{head} от {escape(teacher['name'])}:")
            await message.copy_to(acc["tg_id"])
            sent += 1
        except Exception:
            pass
    if same_album:
        return  # подтверждение уже было на первое фото альбома
    await message.answer(f"✅ Отправлено ученику и родителям ({sent} из {len(accounts)}).\n"
                         "Можно прислать ещё фото или текст, они дойдут к этому же заданию.",
                         reply_markup=kb.inline([[("Готово", f"hw:done:{s['id']}")]]))
