"""Общие помощники для отправки сообщений."""
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from . import keyboards as kb


async def show(target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Из callback — редактирует сообщение, из обычного сообщения — отправляет новое."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                await target.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
        try:
            await target.answer()
        except TelegramBadRequest:
            pass  # на этот запрос уже ответили
    else:
        await target.answer(text, reply_markup=markup, disable_web_page_preview=True)


def main_menu(account: dict):
    if account["role"] == "teacher":
        return kb.teacher_menu(account["tg_id"] in kb.ADMIN_IDS)
    return kb.student_menu(account["role"])


async def send_menu(message: Message, account: dict, text: str = "Главное меню:") -> None:
    await message.answer(text, reply_markup=main_menu(account))
