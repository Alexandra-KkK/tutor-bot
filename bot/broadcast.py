"""Фоновые уведомления: рассылка расписания, напоминания, отчёты родителям, подписка."""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot

from . import keyboards as kb
from . import logic, subscription
from .config import Config
from .db import Database
from .logic import SHORT, WEEKDAYS
from .notify import notify_teacher, safe_send
from .views import TeacherSchedule, day_block

log = logging.getLogger(__name__)

REMINDER_PHRASES = [
    "Скоро урок! Захвати тетрадь и хорошее настроение 📐",
    "Через {left} — математика! Калькулятор не забудь 🧮",
    "Напоминаю про урок. Иксы сами себя не найдут 🔍",
    "Урок скоро! Время прокачать мозг 🧠",
    "Скоро занятие. Домашка сделана? 😉",
    "Через {left} встречаемся на уроке! Будет интересно ✨",
    "Пора готовиться к уроку: чай, ручка, тетрадь ✏️",
]


def _left(minutes: int) -> str:
    if minutes >= 60 and minutes % 60 == 0:
        h = minutes // 60
        return "час" if h == 1 else f"{h} часа" if h < 5 else f"{h} часов"
    return f"{minutes} мин"


# ---------------------------------------------------------------- еженедельная рассылка

async def send_weekly(bot: Bot, db: Database, teacher: dict) -> tuple[int, int]:
    """Каждому подключённому ученику и родителю — его занятия на 7 дней начиная с завтра."""
    sch = await TeacherSchedule.load(db, teacher)
    start = sch.today + timedelta(days=1)
    end = start + timedelta(days=6)
    period = f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}"
    link = teacher["schedule_link"]
    sent = total = 0
    for acc in await db.family_accounts(teacher["id"]):
        student = sch.students.get(acc["student_id"])
        if not student or not student["active"]:
            continue
        total += 1
        body = sch.range_text(start, 7, student_id=student["id"], show_price=False, show_name=False,
                              empty_days=False)
        if acc["role"] == "parent":
            head = f"📅 Расписание занятий: <b>{escape(student['name'])}</b>, {period}"
        else:
            head = f"📅 Твоё расписание на неделю ({period})"
        text = f"{head}\n\n{body}"
        if link:
            text += f"\n\n📎 Общее расписание: {escape(link)}"
        text += f"\n\nПреподаватель: {escape(teacher['name'])}"
        meme = None
        if acc["role"] == "student" and teacher["memes_on"]:
            meme = await db.random_meme(teacher["id"])
        if await safe_send(bot, acc["tg_id"], text, meme):
            sent += 1
    return sent, total


# ---------------------------------------------------------------- проверка раз в минуту

async def tick(bot: Bot, db: Database, config: Config, now_utc: datetime | None = None) -> None:
    now_utc = now_utc or logic.utcnow()
    for teacher in await db.all_teachers():
        try:
            await _teacher_tick(bot, db, config, teacher, now_utc)
        except Exception:
            log.exception("Ошибка уведомлений для преподавателя %s", teacher["id"])


async def _teacher_tick(bot: Bot, db: Database, config: Config, teacher: dict, now_utc: datetime) -> None:
    tid = teacher["id"]
    st = subscription.status(teacher, now_utc)
    await _subscription_notices(bot, db, config, teacher, st, now_utc)
    if not st.active:
        return

    sch = await TeacherSchedule.load(db, teacher)
    sch_now = now_utc.astimezone(timezone(timedelta(hours=teacher["tz_offset"])))
    now = sch_now.replace(tzinfo=None)
    today = now.date()
    lessons = sch.on(today)

    # План на день в 8 утра
    if teacher["daily_plan"] and now.hour == 8 and lessons and await db.mark_sent(f"plan:{tid}:{today}"):
        text = f"☀️ Доброе утро! План на сегодня, {WEEKDAYS[today.weekday()].lower()} {today.strftime('%d.%m')}:\n\n"
        await notify_teacher(bot, db, tid, text + day_block(lessons))

    # Напоминания ученикам (и родителям, если ученик сам не подключён)
    if teacher["remind_before"]:
        for day in (today, today + timedelta(days=1)):
            for l in (lessons if day == today else sch.on(day)):
                start = datetime.combine(day, datetime.strptime(l.time, "%H:%M").time())
                left = (start - now).total_seconds() / 60
                if 0 < left <= teacher["remind_before"] and await db.mark_sent(f"rem:{l.kind}:{l.id}:{day}"):
                    await _remind(bot, db, teacher, l, int(round(left)), day == today)

    # Уроки, закончившиеся за последние 3 часа (вчерашние тоже: урок мог закончиться после полуночи).
    # Старше 3 часов не берём, чтобы после перезапуска бота не слать старое.
    finished = []
    for day in (today - timedelta(days=1), today):
        for l in (lessons if day == today else sch.on(day)):
            end = datetime.combine(day, datetime.strptime(l.time, "%H:%M").time()) + timedelta(minutes=l.duration)
            if end <= now < end + timedelta(hours=3):
                finished.append(l)

    # Сообщение родителям «урок проведён»
    if teacher["parent_reports"]:
        for l in finished:
            if await db.mark_sent(f"done:{l.kind}:{l.id}:{l.day}"):
                for acc in await db.student_accounts(l.student_id, role="parent"):
                    await safe_send(bot, acc["tg_id"],
                                    f"✅ Урок проведён: <b>{escape(l.student_name)}</b>, "
                                    f"{l.day.strftime('%d.%m')} {l.time}–{l.end}.\n"
                                    f"Преподаватель: {escape(teacher['name'])}")

    # Предложить преподавателю отчёт для родителей, когда урок закончился
    if teacher["ask_report"]:
        for l in finished:
            if await db.student_accounts(l.student_id, role="parent") \
                    and await db.mark_sent(f"ask:{l.kind}:{l.id}:{l.day}"):
                await notify_teacher(
                    bot, db, tid,
                    f"Урок закончился: <b>{escape(l.student_name)}</b>, {l.time}–{l.end}.\n"
                    "Оставить родителям короткий отчёт: оценки и пару слов об уроке?",
                    reply_markup=kb.inline([[("📋 Заполнить отчёт", f"rep:{l.day.isoformat()}:{l.kind}:{l.id}"),
                                             ("Не нужно", "menu:close")]]))

    # Еженедельная рассылка расписания (в течение 2 часов после заданного времени)
    if teacher["weekly_send"] and now.weekday() == teacher["weekly_day"]:
        h, m = map(int, teacher["weekly_time"].split(":"))
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if at <= now < at + timedelta(hours=2) and await db.mark_sent(f"week:{tid}:{today}"):
            sent, total = await send_weekly(bot, db, teacher)
            if total:
                await notify_teacher(bot, db, tid, f"📨 Расписание на неделю разослано: {sent} из {total} получателей.")


async def _remind(bot: Bot, db: Database, teacher: dict, lesson, left: int, is_today: bool) -> None:
    accounts = await db.student_accounts(lesson.student_id)
    has_student = any(a["role"] == "student" for a in accounts)
    when = "сегодня" if is_today else "завтра"
    for acc in accounts:
        if acc["role"] == "student":
            phrase = random.choice(REMINDER_PHRASES).format(left=_left(left))
            text = f"⏰ {phrase}\n\nУрок {when} в {lesson.time}–{lesson.end}, преподаватель {escape(teacher['name'])}."
            meme = await db.random_meme(teacher["id"]) if teacher["memes_on"] else None
            key = f"{lesson.day.isoformat()}:{lesson.kind}:{lesson.id}"
            markup = kb.inline([[("👍 Буду", f"rem:ok:{key}"), ("😔 Не смогу", f"rem:no:{key}")]])
            if meme:
                await safe_send(bot, acc["tg_id"], "", meme)
            await safe_send(bot, acc["tg_id"], text, reply_markup=markup)
        elif not has_student:
            await safe_send(bot, acc["tg_id"], f"⏰ Напоминание: урок {when} в {lesson.time}–{lesson.end} · "
                                               f"<b>{escape(lesson.student_name)}</b>")


async def _subscription_notices(bot: Bot, db: Database, config: Config, teacher: dict, st, now_utc: datetime):
    tid = teacher["id"]
    markup = kb.inline([[(f"Оплатить ({config.price_rub}₽/мес)", "sub:pay")]])
    if st.active and st.until and st.until - now_utc < timedelta(days=2):
        if st.kind == "trial" and await db.mark_sent(f"trialend:{tid}:{teacher['trial_until']}"):
            await notify_teacher(bot, db, tid,
                                 "⏳ Пробный период заканчивается "
                                 f"{subscription.fmt_dt(st.until, teacher['tz_offset'])}. "
                                 "Оплатите подписку, чтобы расписание и уведомления продолжили работать.",
                                 reply_markup=markup)
        elif st.kind == "paid" and teacher["auto_renew"] and await db.mark_sent(f"renew:{tid}:{teacher['paid_until']}"):
            await notify_teacher(bot, db, tid,
                                 "⏳ Подписка заканчивается "
                                 f"{subscription.fmt_dt(st.until, teacher['tz_offset'])}. Продлить?",
                                 reply_markup=markup)
    if not st.active and st.until and now_utc - st.until < timedelta(days=3) \
            and await db.mark_sent(f"expired:{tid}:{st.until.isoformat()}"):
        await notify_teacher(bot, db, tid,
                             "Подписка закончилась. Уведомления ученикам и родителям приостановлены. "
                             "Продлить можно в разделе «⭐ Моя подписка».", reply_markup=markup)


async def scheduler_loop(bot: Bot, db: Database, config: Config, interval: int = 60) -> None:
    while True:
        try:
            await tick(bot, db, config)
        except Exception:
            log.exception("Ошибка в цикле уведомлений")
        await asyncio.sleep(interval)
