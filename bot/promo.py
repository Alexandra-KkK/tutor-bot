"""Промокоды для репетиторов."""
import re

from aiogram import Bot

from . import subscription
from .db import Database

CODE_RE = re.compile(r"^[A-Za-z0-9]{3,24}$")


def days_text(days: int) -> str:
    if days == 0:
        return "бесплатно навсегда"
    from .logic import plural
    return f"{days} {plural(days, 'день', 'дня', 'дней')} бесплатно"


async def promo_link(bot: Bot, code: str) -> str:
    me = await bot.me()
    return f"https://t.me/{me.username}?start=promo_{code}"


async def apply_promo(db: Database, teacher: dict, code: str, tg_id: int) -> tuple[bool, str]:
    """Применить промокод. Возвращает (успех, текст для пользователя)."""
    promo = await db.get_promo(code)
    if not promo or not promo["active"]:
        return False, "Такого промокода нет или он больше не действует."
    if promo["max_uses"] is not None and promo["used"] >= promo["max_uses"]:
        return False, "Этот промокод уже использован максимальное число раз."
    perk = f"promo:{promo['code']}"
    if await db.promo_used_by(promo["code"], teacher["id"]) or await db.has_perk(tg_id, perk):
        return False, "Вы уже активировали этот промокод."
    if not await db.reserve_promo(promo["code"], teacher["id"]):
        return False, "Этот промокод уже использован максимальное число раз."
    await db.add_perk(tg_id, perk)
    if promo["days"] == 0:
        await db.update_teacher(teacher["id"], free_forever=1)
    else:
        await db.update_teacher(teacher["id"], paid_until=subscription.extend_paid(teacher, promo["days"]))
    await db.log_sub_payment(teacher["id"], 0, promo["days"], f"promo:{promo['code']}")
    return True, f"🎁 Промокод <b>{promo['code']}</b> активирован: {days_text(promo['days'])}!"
