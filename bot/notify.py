"""Отправка уведомлений ученикам и родителям (с мемами, если они есть)."""
import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

from .db import Database

log = logging.getLogger(__name__)


async def send_meme(bot: Bot, chat_id: int, meme: dict, caption: str | None = None) -> None:
    kind, file_id = meme["kind"], meme["file_id"]
    if kind == "photo":
        await bot.send_photo(chat_id, file_id, caption=caption)
    elif kind == "animation":
        await bot.send_animation(chat_id, file_id, caption=caption)
    elif kind == "video":
        await bot.send_video(chat_id, file_id, caption=caption)
    elif kind == "sticker":
        await bot.send_sticker(chat_id, file_id)
        if caption:
            await bot.send_message(chat_id, caption)


async def safe_send(bot: Bot, chat_id: int, text: str, meme: dict | None = None, **kwargs) -> bool:
    for attempt in range(3):
        try:
            return await _send(bot, chat_id, text, meme, **kwargs)
        except TelegramRetryAfter as e:  # Telegram просит подождать (много сообщений подряд)
            await asyncio.sleep(min(e.retry_after, 30) + 1)
        except TelegramNetworkError as e:
            log.warning("Сеть недоступна, повтор: %s", e)
            await asyncio.sleep(5 * (attempt + 1))
        except Exception as e:  # пользователь заблокировал бота и т.п.
            log.info("Не удалось отправить сообщение %s: %s", chat_id, e)
            return False
    log.warning("Не удалось отправить сообщение %s после повторов", chat_id)
    return False


async def _send(bot: Bot, chat_id: int, text: str, meme: dict | None, **kwargs) -> bool:
    if meme and not text:
        await send_meme(bot, chat_id, meme)
    elif meme and meme["kind"] != "sticker" and len(text) <= 1024 and not kwargs:
        await send_meme(bot, chat_id, meme, caption=text)
    else:
        await bot.send_message(chat_id, text, disable_web_page_preview=True, **kwargs)
        if meme:
            await send_meme(bot, chat_id, meme)
    return True


async def notify_family(bot: Bot, db: Database, teacher: dict, student_id: int, text: str,
                        roles: tuple[str, ...] = ("student", "parent"), with_meme: bool = False) -> int:
    sent = 0
    for acc in await db.student_accounts(student_id):
        if acc["role"] not in roles:
            continue
        meme = None
        if with_meme and teacher["memes_on"] and acc["role"] == "student":
            meme = await db.random_meme(teacher["id"])
        if await safe_send(bot, acc["tg_id"], text, meme):
            sent += 1
    return sent


async def notify_teacher(bot: Bot, db: Database, teacher_id: int, text: str, **kwargs) -> None:
    for tg_id in await db.teacher_accounts(teacher_id):
        await safe_send(bot, tg_id, text, **kwargs)
