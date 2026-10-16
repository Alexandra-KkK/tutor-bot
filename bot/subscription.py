"""Статус подписки преподавателя: пробный период и оплаченные дни."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import logic
from .logic import local_now, tz_name


# Выключается настройкой SUBSCRIPTION=off: тогда бот бесплатный и все функции открыты.
ENABLED = True


@dataclass
class SubStatus:
    active: bool
    kind: str  # "trial" | "paid" | "none"
    until: datetime | None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def status(teacher: dict, now: datetime | None = None) -> SubStatus:
    if not ENABLED or teacher.get("free_forever"):
        return SubStatus(True, "free", None)
    now = now or logic.utcnow()
    trial = _parse(teacher["trial_until"])
    paid = _parse(teacher["paid_until"])
    ends = [d for d in (trial, paid) if d]
    until = max(ends) if ends else None
    if until and until > now:
        return SubStatus(True, "trial" if trial and trial > now else "paid", until)
    return SubStatus(False, "none", until)


def extend_paid(teacher: dict, days: int, now: datetime | None = None) -> str:
    """Новая дата окончания оплаченного периода: продлевается от конца текущего доступа."""
    now = now or logic.utcnow()
    base = max(d for d in (now, _parse(teacher["paid_until"]), _parse(teacher["trial_until"])) if d)
    return (base + timedelta(days=days)).isoformat(timespec="seconds")


def trial_until(days: int, now: datetime | None = None) -> str:
    now = now or logic.utcnow()
    return (now + timedelta(days=days)).isoformat(timespec="seconds")


def fmt_dt(value: datetime, offset: int) -> str:
    return local_now(offset, value).strftime("%Y-%m-%d %H:%M") + " " + tz_name(offset)


def describe(teacher: dict, price: int, trial_days: int, now: datetime | None = None) -> str:
    st = status(teacher, now)
    if st.kind == "free":
        return "Статус: <b>бесплатный доступ навсегда</b> 🎉\nВсе функции бота открыты, платить не нужно."
    off = teacher["tz_offset"]
    paid = _parse(teacher["paid_until"])
    trial = _parse(teacher["trial_until"])
    now = now or logic.utcnow()
    if st.active:
        lines = ["Статус: <b>Подписка активна</b>", ""]
        if st.kind == "trial":
            lines.append(f"Сейчас действует пробный период ({trial_days} дней).")
            lines.append(f"Пробный период до: {fmt_dt(trial, off)}")
            if paid and paid > now:
                lines.append(f"Оплачено до: {fmt_dt(paid, off)}")
            elif teacher["auto_renew"]:
                lines.append(f"Далее: {price}₽/мес с автопродлением.")
            else:
                lines.append(f"Далее: {price}₽/мес. Можно заранее оплатить подписку — "
                             "оплаченные дни добавятся после пробного периода.")
        else:
            lines.append(f"Оплачено до: {fmt_dt(paid, off)}")
            lines.append("Автопродление: " + ("включено" if teacher["auto_renew"] else "выключено"))
        return "\n".join(lines)
    lines = ["Статус: <b>Подписка не активна</b>", ""]
    if st.until:
        lines.append(f"Доступ закончился: {fmt_dt(st.until, off)}")
    if not teacher["trial_used"]:
        lines.append(f"Вы можете взять бесплатные {trial_days} дней — без привязки карты.")
    lines.append(f"Стоимость подписки: {price}₽/мес.")
    return "\n".join(lines)
