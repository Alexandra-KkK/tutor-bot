"""Работа бота в облаке (Vercel).

Здесь нет постоянно работающей программы: Telegram присылает каждое сообщение
запросом на /api/telegram, а напоминания раз в минуту запускает внешний будильник,
дёргая /api/tick. Поэтому состояние диалогов и данные лежат в облачной базе.
"""
import asyncio
import logging
import os

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, Update

from . import keyboards
from .broadcast import tick
from .config import load_config
from .db import Database
from .main import build_dispatcher
from .storage_db import DbStorage

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/api/telegram"


def secret(name: str) -> str:
    return os.getenv(name, "").strip()


async def _open():
    """Открывает базу и собирает бота. Вызывается на каждый запрос: процесс живёт недолго."""
    config = load_config()
    if not config.bot_token:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения проекта")
    db = Database(config.db_path)
    await db.connect()
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    owner = await db.get_setting("owner_id")
    if not config.admin_ids and owner:
        config.admin_ids.add(int(owner))
    dp = build_dispatcher(db, config, storage=DbStorage(db))
    keyboards.ADMIN_IDS.update(config.admin_ids)
    return bot, dp, db, config


async def _close(bot: Bot, db: Database) -> None:
    await db.close()
    await bot.session.close()


async def handle_update(payload: dict) -> None:
    bot, dp, db, config = await _open()
    try:
        await dp.feed_webhook_update(bot, Update.model_validate(payload, context={"bot": bot}))
    finally:
        await _close(bot, db)


async def run_tick() -> None:
    """Одна проверка: кому пора напомнить об уроке, отправить план дня или расписание недели."""
    bot, dp, db, config = await _open()
    try:
        await tick(bot, db, config)
    finally:
        await _close(bot, db)


async def setup_webhook(base_url: str) -> dict:
    """Говорит Telegram слать сообщения на наш адрес и ставит описание бота."""
    bot, dp, db, config = await _open()
    try:
        await bot.set_webhook(
            f"{base_url.rstrip('/')}{WEBHOOK_PATH}",
            secret_token=secret("WEBHOOK_SECRET") or None,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True)
        await bot.set_my_commands([
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="cancel", description="Отменить текущее действие"),
        ])
        for admin_id in config.admin_ids:  # владелец бота пользуется им бесплатно
            acc = await db.get_account(admin_id)
            if acc and acc["role"] == "teacher":
                await db.update_teacher(acc["teacher_id"], free_forever=1)
        me = await bot.me()
        info = await bot.get_webhook_info()
        return {"ok": True, "bot": me.username, "webhook": info.url}
    finally:
        await _close(bot, db)


def run(coro):
    """Запускает асинхронную работу из обычного (синхронного) обработчика запроса."""
    return asyncio.run(coro)
