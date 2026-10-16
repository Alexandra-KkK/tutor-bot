from aiogram import Router
from aiogram.types import CallbackQuery, Message

from ..ui import send_menu

router = Router(name="fallback")


@router.message()
async def unknown(message: Message, account: dict | None):
    if account:
        await send_menu(message, account, "Не совсем понял 🙂 Выберите действие кнопками меню ниже.")


@router.callback_query()
async def unknown_cb(call: CallbackQuery):
    await call.answer("Эта кнопка устарела. Откройте раздел заново из меню.", show_alert=True)
