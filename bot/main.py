import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from . import keyboards, subscription
from .broadcast import scheduler_loop
from .config import Config, load_config
from .db import Database
from .filters import ActiveSub, Role
from .handlers import (account, admin, common, family, fallback, gate, homework, income, report, schedule,
                       students, support)
from .middleware import ContextMiddleware


_tasks: set[asyncio.Task] = set()


async def on_shutdown() -> None:
    for task in _tasks:
        task.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks.clear()


def build_dispatcher(db: Database, config: Config) -> Dispatcher:
    subscription.ENABLED = config.subscription_enabled
    keyboards.ADMIN_IDS.clear()
    keyboards.ADMIN_IDS.update(config.admin_ids)
    dp = Dispatcher(storage=MemoryStorage(), db=db, config=config)
    middleware = ContextMiddleware()
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.pre_checkout_query.outer_middleware(middleware)
    # оплата обрабатывается раньше всех роутеров, без фильтров по роли и состоянию
    dp.pre_checkout_query.register(account.pre_checkout)
    dp.message.register(account.paid, F.successful_payment)

    for r in (account.router, gate.router):
        r.message.filter(Role("teacher"))
        r.callback_query.filter(Role("teacher"))

    paid = Router(name="paid")
    paid.message.filter(Role("teacher"), ActiveSub())
    paid.callback_query.filter(Role("teacher"), ActiveSub())
    paid.include_routers(schedule.router, students.router, income.router, homework.router, report.router)

    family.router.message.filter(Role("student", "parent"))
    family.router.callback_query.filter(Role("student", "parent"))

    dp.include_routers(
        admin.router,
        support.router,
        common.router,
        account.router,
        paid,
        gate.router,
        family.router,
        fallback.router,
    )
    return dp


async def on_startup(bot: Bot, db: Database, config: Config) -> None:
    owner = await db.get_setting("owner_id")
    if not config.admin_ids and owner:
        config.admin_ids.add(int(owner))
        keyboards.ADMIN_IDS.add(int(owner))
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="cancel", description="Отменить текущее действие"),
        ])
        await bot.set_my_short_description(
            "Расписание, ученики и доход репетитора. Напоминания ученикам и родителям.")
        await bot.set_my_description(
            "Привет! Я бот для репетиторов и их учеников.\n\n"
            "👩‍🏫 Преподавателю: расписание, ученики, доход и оплаты в одном месте.\n"
            "🎒 Ученику: расписание на неделю и напоминания перед уроком.\n"
            "👪 Родителям: расписание ребёнка и сообщение, когда урок проведён.\n\n"
            "Нажмите «Начать»!")
    except Exception:
        logging.exception("Не удалось задать команды бота")
    for admin_id in config.admin_ids:  # владелец бота пользуется им бесплатно
        acc = await db.get_account(admin_id)
        if acc and acc["role"] == "teacher":
            await db.update_teacher(acc["teacher_id"], free_forever=1)
    _tasks.add(asyncio.create_task(scheduler_loop(bot, db, config)))
    me = await bot.me()
    logging.info("Бот @%s запущен", me.username)


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    if not config.bot_token:
        sys.exit("Не задан BOT_TOKEN. Скопируйте .env.example в .env и вставьте токен от @BotFather.")
    db = Database(config.db_path)
    await db.connect()
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(db, config)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
