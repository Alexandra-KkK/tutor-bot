"""Чистая логика без Telegram: разбор ввода, расчёт расписания и дохода."""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
NEAREST = [
    "ближайший понедельник", "ближайший вторник", "ближайшую среду", "ближайший четверг",
    "ближайшую пятницу", "ближайшую субботу", "ближайшее воскресенье",
]
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
MONTHS_NOM = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

_DAY_ALIASES = {
    0: ["пн", "пон", "понедельник", "понедельникам", "mon", "monday"],
    1: ["вт", "вто", "вторник", "вторникам", "tue", "tuesday"],
    2: ["ср", "сре", "среда", "среду", "средам", "wed", "wednesday"],
    3: ["чт", "чет", "четверг", "четвергам", "thu", "thursday"],
    4: ["пт", "пят", "пятница", "пятницу", "пятницам", "fri", "friday"],
    5: ["сб", "суб", "суббота", "субботу", "субботам", "sat", "saturday"],
    6: ["вс", "вос", "воскресенье", "воскресеньям", "sun", "sunday"],
}
DAY_LOOKUP = {alias: day for day, aliases in _DAY_ALIASES.items() for alias in aliases}


def parse_day(text: str) -> int | None:
    return DAY_LOOKUP.get(text.strip().lower().rstrip(".,:"))


_TIME_RE = re.compile(r"^(\d{1,2})(?:[:.ч ](\d{2}))?$")


def parse_time(text: str) -> str | None:
    """'15', '15:00', '9.30' -> 'HH:MM'. Дефис означает промежуток: '15-16' — это 15:00–16:00."""
    m = _TIME_RE.match(text.strip().lower())
    if not m:
        return None
    h, mnt = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mnt > 59:
        return None
    return f"{h:02d}:{mnt:02d}"


def parse_time_range(text: str) -> tuple[str, int | None] | None:
    """'15:00' -> ('15:00', None); '15:00-16:30' -> ('15:00', 90)."""
    text = text.strip()
    m = re.match(r"^(\d{1,2}(?:[:.]\d{2})?)\s*[-–—]\s*(\d{1,2}(?:[:.]\d{2})?)$", text)
    if m:
        start, end = parse_time(m.group(1)), parse_time(m.group(2))
        if not start or not end:
            return None
        duration = minutes(end) - minutes(start)
        if duration <= 0:
            return None
        return start, duration
    t = parse_time(text)
    return (t, None) if t else None


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def add_minutes(hhmm: str, delta: int) -> str:
    total = (minutes(hhmm) + delta) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def parse_date(text: str, today: date) -> date | None:
    """'2026-10-19', '19.10.2026', '19.10', '19/10/26', 'сегодня', 'завтра', 'пн'."""
    s = text.strip().lower()
    words = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
    if s in words:
        return today + timedelta(days=words[s])
    day = parse_day(s)
    if day is not None:
        return nearest_date(today, day)
    try:
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"^(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?$", s)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            if m.group(3):
                y = int(m.group(3))
                if y < 100:
                    y += 2000
                return date(y, mo, d)
            candidate = date(today.year, mo, d)
            # Дата без года: если она уже давно прошла, считаем, что имеется в виду следующий год.
            if candidate < today - timedelta(days=60):
                candidate = date(today.year + 1, mo, d)
            return candidate
    except ValueError:
        return None
    return None


def parse_amount(text: str) -> int | None:
    s = text.replace(" ", "").replace(" ", "").lower().replace("₽", "").replace("руб", "").replace("р", "")
    s = s.rstrip(".")
    if not (s.isascii() and s.isdigit()):
        return None
    value = int(s)
    return value if value <= 10_000_000 else None


def nearest_date(today: date, weekday: int) -> date:
    """Ближайшая дата с этим днём недели, считая сегодня."""
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def week_bounds(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


def prev_month_bounds(day: date) -> tuple[date, date]:
    return month_bounds(day.replace(day=1) - timedelta(days=1))


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------- расписание

@dataclass
class Entry:
    weekday: int
    time: str
    name: str
    price: int | None = None
    duration: int | None = None


def parse_schedule_text(text: str) -> tuple[list[Entry], list[str]]:
    """Разбор расписания целиком, по одному занятию в строке:

        Пн 15:00 Маша 1500
        Ср 17:30-19:00 Петя Иванов 2000
        Пт 10 Аня 1200 90
    """
    entries: list[Entry] = []
    errors: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().strip("-•*").strip()
        if not line:
            continue
        # «15:00 - 16:00» → «15:00-16:00», чтобы промежуток не попал в имя
        line = re.sub(r"(\d{1,2}(?:[:.]\d{2})?)\s*[-–—]\s*(?=\d{1,2}(?:[:.]\d{2})?(?:\s|$))", r"\1-", line, count=1)
        parts = line.replace(",", " ").split()
        if len(parts) < 3:
            errors.append(f"«{raw.strip()}» — нужно: день, время, имя")
            continue
        day = parse_day(parts[0])
        if day is None:
            errors.append(f"«{raw.strip()}» — не понял день недели «{parts[0]}»")
            continue
        tr = parse_time_range(parts[1])
        if tr is None:
            errors.append(f"«{raw.strip()}» — не понял время «{parts[1]}»")
            continue
        time, duration = tr
        rest = parts[2:]
        numbers: list[int] = []
        while rest and len(numbers) < 2 and parse_amount(rest[-1]) is not None and len(rest) > 1:
            numbers.insert(0, parse_amount(rest.pop()))
        name = " ".join(rest).strip()
        if not name:
            errors.append(f"«{raw.strip()}» — не указано имя ученика")
            continue
        price = numbers[0] if numbers else None
        if len(numbers) == 2:
            duration = numbers[1]
        entries.append(Entry(day, time, name, price, duration))
    return entries, errors


@dataclass
class Lesson:
    kind: str  # "w" — регулярное, "s" — разовое
    id: int
    student_id: int
    student_name: str
    day: date
    time: str
    duration: int
    price: int

    @property
    def end(self) -> str:
        return add_minutes(self.time, self.duration)


def lessons_on(day: date, weekly: list[dict], singles: list[dict],
               cancelled: set[tuple[int, str]], students: dict[int, dict]) -> list[Lesson]:
    iso = day.isoformat()
    result: list[Lesson] = []
    for w in weekly:
        if w["weekday"] != day.weekday():
            continue
        if w["valid_from"] > iso or (w["valid_to"] and w["valid_to"] < iso):
            continue
        if (w["id"], iso) in cancelled:
            continue
        st = students.get(w["student_id"])
        if not st:
            continue
        price = w["price"] if w["price"] is not None else st["price"]
        result.append(Lesson("w", w["id"], st["id"], st["name"], day, w["time"], w["duration"], price))
    for s in singles:
        if s["date"] != iso:
            continue
        st = students.get(s["student_id"])
        if not st:
            continue
        price = s["price"] if s["price"] is not None else st["price"]
        result.append(Lesson("s", s["id"], st["id"], st["name"], day, s["time"], s["duration"], price))
    result.sort(key=lambda l: (l.time, l.student_name))
    return result


def lesson_started(lesson: Lesson, now_local: datetime) -> bool:
    start = datetime.combine(lesson.day, datetime.strptime(lesson.time, "%H:%M").time())
    return start <= now_local.replace(tzinfo=None)


@dataclass
class Income:
    total: int
    count: int
    by_student: dict[int, tuple[str, int, int]]  # id -> (имя, занятий, сумма)
    planned_total: int = 0
    planned_count: int = 0


def income(start: date, end: date, now_local: datetime, weekly, singles, cancelled, students) -> Income:
    """Проведённые занятия (уже начавшиеся) и запланированные на остаток периода."""
    inc = Income(0, 0, {})
    for d in daterange(start, end):
        for l in lessons_on(d, weekly, singles, cancelled, students):
            if lesson_started(l, now_local):
                inc.total += l.price
                inc.count += 1
                name, cnt, amount = inc.by_student.get(l.student_id, (l.student_name, 0, 0))
                inc.by_student[l.student_id] = (name, cnt + 1, amount + l.price)
            else:
                inc.planned_total += l.price
                inc.planned_count += 1
    return inc


# ---------------------------------------------------------------- форматирование

def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₽"


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def human_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]}, {WEEKDAYS[d.weekday()].lower()}"


def tz_name(offset: int) -> str:
    return f"UTC{offset:+d}"


def utcnow() -> datetime:
    """Текущее время (в тестах подменяется)."""
    return datetime.now(timezone.utc)


def local_now(offset: int, now_utc: datetime | None = None) -> datetime:
    now_utc = now_utc or utcnow()
    return now_utc.astimezone(timezone(timedelta(hours=offset)))
