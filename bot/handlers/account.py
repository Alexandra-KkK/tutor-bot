"""Подписка, секреты, настройки и мемы преподавателя."""
import logging
from datetime import datetime, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from .. import keyboards as kb
from .. import subscription
from ..config import Config
from ..db import Database
from ..filters import TEXT_INPUT
from ..keyboards import B
from ..logic import SHORT, WEEKDAYS, local_now, parse_time, tz_name
from ..notify import send_meme
from ..texts import secrets_text
from ..ui import show
from ..broadcast import send_weekly
from ..promo import apply_promo

router = Router(name="account")


class Acc(StatesGroup):
    promo = State()
    weekly_time = State()
    link = State()
    memes = State()


# ---------------------------------------------------------------- подписка

async def show_subscription(target, teacher: dict, config: Config):
    st = subscription.status(teacher)
    text = subscription.describe(teacher, config.price_rub, config.trial_days)
    markup = None if st.kind == "free" else kb.subscription(teacher, st.active, config.price_rub, config.trial_days)
    await show(target, text, markup)


@router.message(F.text == B.MY_SUB)
async def my_subscription(message: Message, state: FSMContext, teacher: dict, config: Config):
    await state.clear()
    await show_subscription(message, teacher, config)


@router.callback_query(F.data == "sub:promo")
async def promo_ask(call: CallbackQuery, state: FSMContext):
    await state.set_state(Acc.promo)
    await call.answer()
    await call.message.answer("Напишите промокод:")


@router.message(Acc.promo, TEXT_INPUT)
async def promo_enter(message: Message, state: FSMContext, db: Database, teacher: dict, config: Config):
    ok, text = await apply_promo(db, teacher, message.text, message.from_user.id)
    await message.answer(text)
    if ok:
        await state.clear()
        await show_subscription(message, await db.get_teacher(teacher["id"]), config)


@router.callback_query(F.data == "sub:trial")
async def take_trial(call: CallbackQuery, db: Database, teacher: dict, config: Config):
    if teacher["trial_used"] or not await db.add_perk(call.from_user.id, "trial"):
        await db.update_teacher(teacher["id"], trial_used=1)
        await call.answer("Пробный период уже был использован.", show_alert=True)
        return
    await db.update_teacher(teacher["id"], trial_until=subscription.trial_until(config.trial_days),
                            trial_used=1, auto_renew=1)
    teacher = await db.get_teacher(teacher["id"])
    await call.answer("Пробный период активирован 🎉")
    await show_subscription(call, teacher, config)
    await call.message.answer("Всё готово! Начните с раздела «📅 Расписание» → «Редактирование расписания».")


async def send_payment(bot: Bot, chat_id: int, teacher: dict, config: Config) -> None:
    if not config.payment_provider_token:
        await bot.send_message(
            chat_id,
            "💳 Онлайн-оплата скоро появится.\n"
            "Пока продлить подписку можно через поддержку: напишите, и мы активируем доступ вручную.",
            reply_markup=kb.inline([[("💬 Написать в поддержку", "sup:start")]]),
        )
        return
    await bot.send_invoice(
        chat_id=chat_id,
        title="Подписка на бота для репетитора",
        description=f"Доступ ко всем функциям на {config.sub_days} дней.",
        payload=f"sub:{teacher['id']}",
        provider_token=config.payment_provider_token,
        currency="RUB",
        prices=[LabeledPrice(label=f"Подписка на {config.sub_days} дней", amount=config.price_rub * 100)],
    )


@router.callback_query(F.data == "sub:pay")
async def pay(call: CallbackQuery, bot: Bot, teacher: dict, config: Config):
    await call.answer()
    await send_payment(bot, call.message.chat.id, teacher, config)


# pre_checkout и paid регистрируются в main.py прямо на диспетчере: оплата не должна
# теряться из-за роли или незаконченного действия (например, открытой поддержки).
async def pre_checkout(query: PreCheckoutQuery, db: Database):
    ok = query.invoice_payload.startswith("sub:") and await db.get_teacher(int(query.invoice_payload[4:]))
    await query.answer(ok=bool(ok), error_message=None if ok else "Профиль не найден. Нажмите /start.")


async def paid(message: Message, db: Database, config: Config):
    sp = message.successful_payment
    teacher = await db.get_teacher(int(sp.invoice_payload[4:]))
    if not teacher:
        logging.error("Оплата %s без профиля: %s", sp.telegram_payment_charge_id, sp.invoice_payload)
        await message.answer("Оплата получена, но профиль преподавателя не найден. "
                             "Напишите в поддержку, доступ продлят вручную.")
        return
    until = subscription.extend_paid(teacher, config.sub_days)
    await db.update_teacher(teacher["id"], paid_until=until, auto_renew=1)
    await db.log_sub_payment(teacher["id"], sp.total_amount // 100, config.sub_days, "telegram",
                             sp.telegram_payment_charge_id)
    teacher = await db.get_teacher(teacher["id"])
    await message.answer("✅ Оплата прошла, спасибо!\n\n" + subscription.describe(teacher, config.price_rub, config.trial_days))


@router.message(F.text == B.CANCEL_SUB)
@router.callback_query(F.data == "sub:cancel")
async def cancel_sub(event, state: FSMContext, teacher: dict):
    await state.clear()
    st = subscription.status(teacher)
    if st.kind == "free":
        await show(event, subscription.describe(teacher, 0, 0))
        return
    if not st.active:
        await show(event, "У вас нет активной подписки.")
        return
    if not teacher["auto_renew"]:
        await show(event, "Автопродление уже отключено. Доступ сохранится до "
                          f"{subscription.fmt_dt(st.until, teacher['tz_offset'])}.")
        return
    await show(event, "Отменить подписку? Автопродление отключится, а доступ сохранится до "
                      f"{subscription.fmt_dt(st.until, teacher['tz_offset'])}.",
               kb.confirm("sub:cancel:yes", "sub:cancel:no", "Да, отменить", "Нет"))


@router.callback_query(F.data == "sub:cancel:yes")
async def cancel_sub_yes(call: CallbackQuery, db: Database, teacher: dict):
    await db.update_teacher(teacher["id"], auto_renew=0)
    st = subscription.status(teacher)
    await show(call, "Подписка отменена. Доступ сохранится до "
                     f"{subscription.fmt_dt(st.until, teacher['tz_offset'])}. "
                     "Вернуть её можно в «Моя подписка».")


@router.callback_query(F.data == "sub:cancel:no")
async def cancel_sub_no(call: CallbackQuery):
    await show(call, "Хорошо, подписка остаётся 👍")


# ---------------------------------------------------------------- секреты

@router.message(F.text == B.SECRETS)
async def my_secrets(message: Message, state: FSMContext, bot: Bot, teacher: dict):
    await state.clear()
    await message.answer(await secrets_text(bot, teacher), disable_web_page_preview=True,
                         reply_markup=kb.inline([
                             [("🔄 Новый секрет для учеников", "sec:new:student")],
                             [("🔄 Новый секрет для входа учителя", "sec:new:teacher")],
                         ]))


@router.callback_query(F.data.startswith("sec:new:"))
async def new_secret_ask(call: CallbackQuery):
    kind = call.data.split(":")[2]
    note = ("Старая ссылка-приглашение перестанет работать. Уже подключённые ученики останутся."
            if kind == "student" else "Старый секрет входа перестанет работать. Уже вошедшие аккаунты останутся.")
    await show(call, f"Сгенерировать новый секрет? {note}", kb.confirm(f"sec:yes:{kind}", "sec:no", "Да", "Нет"))


@router.callback_query(F.data.startswith("sec:yes:"))
async def new_secret(call: CallbackQuery, db: Database, bot: Bot, teacher: dict):
    kind = call.data.split(":")[2]
    await db.regenerate_secret(teacher["id"], f"{kind}_secret")
    teacher = await db.get_teacher(teacher["id"])
    await show(call, await secrets_text(bot, teacher))


@router.callback_query(F.data == "sec:no")
async def new_secret_no(call: CallbackQuery, bot: Bot, teacher: dict):
    await show(call, await secrets_text(bot, teacher))


# ---------------------------------------------------------------- настройки

def _onoff(value) -> str:
    return "вкл" if value else "выкл"


async def _settings(target, db: Database, teacher: dict):
    t = teacher
    now = local_now(t["tz_offset"])
    memes = await db.memes(t["id"])
    remind = f"за {t['remind_before']} мин" if t["remind_before"] else "выкл"
    weekly = f"{SHORT[t['weekly_day']]} в {t['weekly_time']}" if t["weekly_send"] else "выкл"
    link = "задана" if t["schedule_link"] else "не задана"
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"🕒 Часовой пояс: {tz_name(t['tz_offset'])} (сейчас {now.strftime('%H:%M')})\n"
        f"🌅 План на день в 8:00: {_onoff(t['daily_plan'])}\n"
        f"⏰ Напоминание ученикам: {remind}\n"
        f"📨 Рассылка расписания на неделю: {weekly}\n"
        f"🔗 Ссылка на ваше расписание (Google Диск): {link}\n"
        f"✅ Сообщать родителям, что урок проведён: {_onoff(t['parent_reports'])}\n"
        f"📋 Предлагать отчёт об уроке для родителей: {_onoff(t['ask_report'])}\n"
        f"🖼 Мемы в напоминаниях: {_onoff(t['memes_on'])} ({len(memes)} шт.)"
    )
    await show(target, text, kb.inline([
        [("🕒 Часовой пояс", "set:tz"), (f"🌅 План: {_onoff(t['daily_plan'])}", "set:daily")],
        [("⏰ Напоминание", "set:remind"), (f"✅ Родителям: {_onoff(t['parent_reports'])}", "set:reports")],
        [(f"📋 Отчёт после урока: {_onoff(t['ask_report'])}", "set:askrep")],
        [("📨 Рассылка расписания", "set:weekly")],
        [("🔗 Ссылка на Google Диск", "set:link")],
        [("🖼 Мемы для учеников", "set:memes")],
        kb.back("menu:close"),
    ]))


@router.message(F.text == B.SETTINGS)
async def settings(message: Message, state: FSMContext, db: Database, teacher: dict):
    await state.clear()
    await _settings(message, db, teacher)


@router.callback_query(F.data == "set:menu")
async def settings_cb(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await state.clear()
    await _settings(call, db, teacher)


async def _update_and_show(call: CallbackQuery, db: Database, teacher: dict, **fields):
    await db.update_teacher(teacher["id"], **fields)
    await _settings(call, db, await db.get_teacher(teacher["id"]))


@router.callback_query(F.data == "set:daily")
async def toggle_daily(call: CallbackQuery, db: Database, teacher: dict):
    await _update_and_show(call, db, teacher, daily_plan=0 if teacher["daily_plan"] else 1)


@router.callback_query(F.data == "set:reports")
async def toggle_reports(call: CallbackQuery, db: Database, teacher: dict):
    await _update_and_show(call, db, teacher, parent_reports=0 if teacher["parent_reports"] else 1)


@router.callback_query(F.data == "set:askrep")
async def toggle_ask_report(call: CallbackQuery, db: Database, teacher: dict):
    await _update_and_show(call, db, teacher, ask_report=0 if teacher["ask_report"] else 1)


@router.callback_query(F.data == "set:tz")
async def tz_menu(call: CallbackQuery):
    offsets = list(range(2, 13))
    rows, row = [], []
    for off in offsets:
        row.append((tz_name(off), f"set:tz:{off}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(kb.back("set:menu"))
    await show(call, "Выберите часовой пояс. Москва — UTC+3, Екатеринбург — UTC+5, "
                     "Новосибирск — UTC+7, Владивосток — UTC+10.", kb.inline(rows))


@router.callback_query(F.data.startswith("set:tz:"))
async def tz_set(call: CallbackQuery, db: Database, teacher: dict):
    await _update_and_show(call, db, teacher, tz_offset=int(call.data.split(":")[2]))


@router.callback_query(F.data == "set:remind")
async def remind_menu(call: CallbackQuery):
    await show(call, "За сколько до урока напоминать ученикам?", kb.inline([
        [("15 мин", "set:rm:15"), ("30 мин", "set:rm:30"), ("1 час", "set:rm:60")],
        [("2 часа", "set:rm:120"), ("3 часа", "set:rm:180"), ("Не напоминать", "set:rm:0")],
        kb.back("set:menu"),
    ]))


@router.callback_query(F.data.startswith("set:rm:"))
async def remind_set(call: CallbackQuery, db: Database, teacher: dict):
    await _update_and_show(call, db, teacher, remind_before=int(call.data.split(":")[2]))


@router.callback_query(F.data == "set:weekly")
async def weekly_menu(call: CallbackQuery, teacher: dict):
    status = (f"Сейчас: каждый {WEEKDAYS[teacher['weekly_day']].lower()} в {teacher['weekly_time']}."
              if teacher["weekly_send"] else "Сейчас рассылка выключена.")
    await show(call,
               "📨 Раз в неделю бот присылает каждому ученику и родителям расписание их занятий "
               "на 7 дней вперёд и вашу ссылку на Google Диск.\n\n" + status, kb.inline([
                   [("Выбрать день и время", "set:wd")],
                   [("Выключить рассылку" if teacher["weekly_send"] else "Включить рассылку", "set:wtoggle")],
                   [("📤 Разослать сейчас", "set:wnow")],
                   kb.back("set:menu"),
               ]))


@router.callback_query(F.data == "set:wtoggle")
async def weekly_toggle(call: CallbackQuery, db: Database, teacher: dict):
    await db.update_teacher(teacher["id"], weekly_send=0 if teacher["weekly_send"] else 1)
    await weekly_menu(call, await db.get_teacher(teacher["id"]))


@router.callback_query(F.data == "set:wd")
async def weekly_day_menu(call: CallbackQuery):
    await show(call, "В какой день присылать расписание?", kb.weekdays_picker("set:wd:", "set:weekly"))


@router.callback_query(F.data.startswith("set:wd:"))
async def weekly_day_set(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await db.update_teacher(teacher["id"], weekly_day=int(call.data.split(":")[2]), weekly_send=1)
    await state.set_state(Acc.weekly_time)
    await show(call, "Во сколько? Например <code>18:00</code>.",
               kb.inline([[("18:00", "set:wt:18:00"), ("19:00", "set:wt:19:00"), ("20:00", "set:wt:20:00")]]))


async def _weekly_time_saved(target, state: FSMContext, db: Database, teacher: dict, time: str):
    await state.clear()
    await db.update_teacher(teacher["id"], weekly_time=time)
    t = await db.get_teacher(teacher["id"])
    await show(target, f"✅ Рассылка: каждый {WEEKDAYS[t['weekly_day']].lower()} в {time}.",
               kb.inline([kb.back("set:menu", "К настройкам")]))


@router.callback_query(F.data.startswith("set:wt:"))
async def weekly_time_button(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await _weekly_time_saved(call, state, db, teacher, call.data[7:])


@router.message(Acc.weekly_time, TEXT_INPUT)
async def weekly_time_typed(message: Message, state: FSMContext, db: Database, teacher: dict):
    time = parse_time(message.text)
    if not time:
        await message.answer("Не понял время. Пример: <code>18:00</code>")
        return
    await _weekly_time_saved(message, state, db, teacher, time)


@router.callback_query(F.data == "set:wnow")
async def weekly_now(call: CallbackQuery, db: Database, bot: Bot, teacher: dict):
    if not subscription.status(teacher).active:
        await call.answer("Рассылка работает при активной подписке.", show_alert=True)
        return
    await call.answer("Отправляю…")
    today = local_now(teacher["tz_offset"]).date()
    if today.weekday() == teacher["weekly_day"]:
        await db.mark_sent(f"week:{teacher['id']}:{today}")  # чтобы сегодня не пришло второй раз
    sent, total = await send_weekly(bot, db, teacher)
    if total == 0:
        text = ("Пока некому отправлять: ни ученики, ни родители не подключились к боту.\n"
                "Отправьте им ссылку-приглашение из раздела «🔑 Мои секреты».")
    else:
        text = f"✅ Расписание отправлено: {sent} из {total} получателей."
    await call.message.answer(text)


@router.callback_query(F.data == "set:link")
async def link_ask(call: CallbackQuery, state: FSMContext, teacher: dict):
    await state.set_state(Acc.link)
    current = f"\n\nСейчас: {escape(teacher['schedule_link'])}" if teacher["schedule_link"] else ""
    await show(call, "Отправьте ссылку на ваше расписание в Google Диске (или любую другую). "
                     "Она будет приходить ученикам и родителям вместе с еженедельной рассылкой.\n"
                     "Отправьте «-», чтобы убрать ссылку." + current,
               kb.inline([kb.back("set:menu")]))


@router.message(Acc.link, TEXT_INPUT)
async def link_set(message: Message, state: FSMContext, db: Database, teacher: dict):
    text = message.text.strip()
    if text != "-" and not text.startswith(("http://", "https://")):
        await message.answer("Это не похоже на ссылку. Она должна начинаться с https://")
        return
    await state.clear()
    await db.update_teacher(teacher["id"], schedule_link="" if text == "-" else text[:500])
    await _settings(message, db, await db.get_teacher(teacher["id"]))


# ---------------------------------------------------------------- мемы

async def _memes_menu(target, db: Database, teacher: dict, state: FSMContext):
    memes = await db.memes(teacher["id"])
    await state.set_state(Acc.memes)
    await show(target,
               "🖼 <b>Мемы для учеников</b>\n\n"
               "Пришлите сюда картинки, GIF или стикеры — прямо сейчас, сколько хотите. "
               "Перед уроком ученик получит напоминание со случайным мемом из вашей коллекции.\n\n"
               f"В коллекции: {len(memes)} шт. Мемы: {_onoff(teacher['memes_on'])}.",
               kb.inline([
                   [("👀 Показать случайный", "meme:random")],
                   [("Выключить мемы" if teacher["memes_on"] else "Включить мемы", "meme:toggle")],
                   [("🗑 Удалить все", "meme:clear")],
                   [("Готово", "set:menu")],
               ]))


@router.callback_query(F.data == "set:memes")
async def memes_menu(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await _memes_menu(call, db, teacher, state)


@router.message(Acc.memes, F.photo | F.animation | F.sticker | F.video)
async def meme_add(message: Message, db: Database, teacher: dict):
    if message.photo:
        kind, file_id = "photo", message.photo[-1].file_id
    elif message.animation:
        kind, file_id = "animation", message.animation.file_id
    elif message.sticker:
        kind, file_id = "sticker", message.sticker.file_id
    else:
        kind, file_id = "video", message.video.file_id
    await db.add_meme(teacher["id"], kind, file_id)
    count = len(await db.memes(teacher["id"]))
    await message.reply(f"Добавлено 👍 В коллекции: {count}. Можно присылать ещё или нажать «Готово».",
                        reply_markup=kb.inline([[("Готово", "set:menu")]]))


@router.callback_query(F.data == "meme:random")
async def meme_random(call: CallbackQuery, db: Database, bot: Bot, teacher: dict):
    meme = await db.random_meme(teacher["id"])
    if not meme:
        await call.answer("Коллекция пуста — пришлите картинку.", show_alert=True)
        return
    await call.answer()
    await send_meme(bot, call.message.chat.id, meme)
    await call.message.answer("Так его увидит ученик.", reply_markup=kb.inline([
        [("🗑 Удалить этот мем", f"meme:del:{meme['id']}")], [("Ещё один", "meme:random")]]))


@router.callback_query(F.data.startswith("meme:del:"))
async def meme_delete(call: CallbackQuery, db: Database, teacher: dict):
    await db.delete_meme(teacher["id"], int(call.data.split(":")[2]))
    await show(call, "Мем удалён.", kb.inline([[("Ещё один", "meme:random")], [("Готово", "set:menu")]]))


@router.callback_query(F.data == "meme:toggle")
async def meme_toggle(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await db.update_teacher(teacher["id"], memes_on=0 if teacher["memes_on"] else 1)
    await _memes_menu(call, db, await db.get_teacher(teacher["id"]), state)


@router.callback_query(F.data == "meme:clear")
async def meme_clear(call: CallbackQuery):
    await show(call, "Удалить все мемы из коллекции?", kb.confirm("meme:clear:yes", "set:memes"))


@router.callback_query(F.data == "meme:clear:yes")
async def meme_clear_yes(call: CallbackQuery, state: FSMContext, db: Database, teacher: dict):
    await db.delete_all_memes(teacher["id"])
    await _memes_menu(call, db, teacher, state)
