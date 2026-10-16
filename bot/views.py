"""Тексты расписания, общие для преподавателя, учеников и уведомлений."""
from datetime import date, timedelta
from html import escape

from .db import Database
from .logic import NEAREST, SHORT, WEEKDAYS, Lesson, human_date, lessons_on, local_now, minutes, money, nearest_date


def lesson_line(l: Lesson, show_price: bool = True, show_name: bool = True) -> str:
    parts = [f"<b>{l.time}–{l.end}</b>"]
    if show_name:
        parts.append(escape(l.student_name))
    if l.kind == "s":
        parts.append("<i>разовое</i>")
    if show_price and l.price:
        parts.append(money(l.price))
    return " · ".join(parts)


def day_block(lessons: list[Lesson], show_price: bool = True, show_name: bool = True) -> str:
    if not lessons:
        return "На эту дату активных расписаний и занятий нет."
    lines = [lesson_line(l, show_price, show_name) for l in lessons]
    if show_price:
        total = sum(l.price for l in lessons)
        if total:
            lines.append(f"\nИтого: {len(lessons)} · {money(total)}")
    return "\n".join(lines)


def plan_title(day: date, today: date) -> str:
    if day == today:
        return f"План на сегодня ({day.isoformat()}):"
    if day == today + timedelta(days=1):
        return f"План на завтра ({day.isoformat()}):"
    if 0 < (day - today).days < 7:
        return f"План на {NEAREST[day.weekday()]} ({day.isoformat()}):"
    return f"План на {human_date(day)} ({day.isoformat()}):"


class TeacherSchedule:
    """Загружает расписание преподавателя один раз и отвечает на вопросы по датам."""

    def __init__(self, teacher: dict, weekly, singles, cancelled, students):
        self.teacher = teacher
        self.weekly, self.singles, self.cancelled, self.students = weekly, singles, cancelled, students

    @classmethod
    async def load(cls, db: Database, teacher: dict) -> "TeacherSchedule":
        return cls(teacher, *await db.schedule_data(teacher["id"]))

    @property
    def now(self):
        return local_now(self.teacher["tz_offset"])

    @property
    def today(self) -> date:
        return self.now.date()

    def on(self, day: date, student_id: int | None = None) -> list[Lesson]:
        lessons = lessons_on(day, self.weekly, self.singles, self.cancelled, self.students)
        if student_id is not None:
            lessons = [l for l in lessons if l.student_id == student_id]
        return lessons

    def cancelled_on(self, day: date) -> list[Lesson]:
        """Регулярные занятия этого дня, которые были отменены."""
        iso = day.isoformat()
        every = lessons_on(day, self.weekly, [], set(), self.students)
        return [l for l in every if (l.id, iso) in self.cancelled]

    def plan_text(self, day: date) -> str:
        return plan_title(day, self.today) + "\n\n" + day_block(self.on(day))

    def range_text(self, start: date, days: int, student_id: int | None = None,
                   show_price: bool = True, show_name: bool = True, empty_days: bool = True) -> str:
        blocks = []
        for i in range(days):
            d = start + timedelta(days=i)
            lessons = self.on(d, student_id)
            if not lessons and not empty_days:
                continue
            head = f"<b>{SHORT[d.weekday()]}, {d.strftime('%d.%m')}</b>"
            if d == self.today:
                head += " · сегодня"
            body = "\n".join(lesson_line(l, show_price, show_name) for l in lessons) if lessons else "—"
            blocks.append(f"{head}\n{body}")
        return "\n\n".join(blocks) if blocks else "Занятий нет."

    def next_lessons(self, student_id: int, limit: int = 1, horizon: int = 60) -> list[Lesson]:
        result: list[Lesson] = []
        now = self.now.replace(tzinfo=None)
        for i in range(horizon):
            d = self.today + timedelta(days=i)
            for l in self.on(d, student_id):
                if d > now.date() or l.time > now.strftime("%H:%M"):
                    result.append(l)
                    if len(result) >= limit:
                        return result
        return result

    def started_today(self, weekday: int, time: str) -> bool:
        """Сегодняшнее занятие в этот день недели и время уже началось."""
        return weekday == self.today.weekday() and time <= self.now.strftime("%H:%M")

    def last_day_for(self, lesson: dict) -> str:
        """До какого дня сохранить регулярное занятие, если его убирают сейчас."""
        keep_today = self.started_today(lesson["weekday"], lesson["time"])
        return (self.today if keep_today else self.today - timedelta(days=1)).isoformat()

    def first_day_for(self, weekday: int, time: str) -> str:
        """С какого дня действует новое регулярное занятие (прошедшее сегодня не считается)."""
        return (self.today + timedelta(days=1) if self.started_today(weekday, time) else self.today).isoformat()

    async def end_all(self, db: Database) -> int:
        lessons = self.weekly_template()
        for w in lessons:
            await db.end_weekly(self.teacher["id"], w["id"], self.last_day_for(w))
        return len(lessons)

    def conflicts(self, day: date, time: str, duration: int, skip: tuple[str, int] | None = None) -> list[Lesson]:
        """Занятия в этот день, которые пересекаются по времени с новым."""
        start = minutes(time)
        end = start + duration
        return [l for l in self.on(day) if (l.kind, l.id) != skip
                and minutes(l.time) < end and start < minutes(l.time) + l.duration]

    def weekly_conflicts(self, weekday: int, time: str, duration: int) -> list[Lesson]:
        first = date.fromisoformat(self.first_day_for(weekday, time))
        return self.conflicts(nearest_date(first, weekday), time, duration)

    def weekly_template(self) -> list[dict]:
        """Регулярные занятия, действующие с сегодняшнего дня."""
        iso = self.today.isoformat()
        rows = [w for w in self.weekly
                if (not w["valid_to"] or w["valid_to"] > iso) and (not w["valid_to"] or w["valid_to"] >= w["valid_from"])]
        return sorted(rows, key=lambda w: (w["weekday"], w["time"]))

    def bulk_text(self) -> str:
        lines = []
        for w in self.weekly_template():
            st = self.students.get(w["student_id"])
            if not st:
                continue
            line = f"{SHORT[w['weekday']]} {w['time']} {st['name']}"
            price = w["price"] if w["price"] is not None else st["price"]
            if price:
                line += f" {price}"
            if w["duration"] != 60:
                line += f" {w['duration']}" if price else f" 0 {w['duration']}"
            lines.append(line)
        return "\n".join(lines)

    def lesson_word(self, l: Lesson) -> str:
        return f"{WEEKDAYS[l.day.weekday()].lower()}, {l.day.strftime('%d.%m')} в {l.time}"


def conflict_note(lessons: list[Lesson]) -> str:
    if not lessons:
        return ""
    items = ", ".join(f"{l.time}–{l.end} {escape(l.student_name)}" for l in lessons)
    return f"\n\n⚠️ Внимание: в это время уже есть занятие ({items})."


def earned_by_student(sch: TeacherSchedule, start: date | None = None, end: date | None = None) -> dict[int, tuple[int, int]]:
    """student_id -> (проведено занятий, сумма) за период (по умолчанию — за всё время до сейчас)."""
    from .logic import income
    if start is None:
        dates = [w["valid_from"] for w in sch.weekly] + [s["date"] for s in sch.singles]
        if not dates:
            return {}
        start = date.fromisoformat(min(dates))
    end = end or sch.today
    inc = income(start, end, sch.now, sch.weekly, sch.singles, sch.cancelled, sch.students)
    return {sid: (cnt, amount) for sid, (_, cnt, amount) in inc.by_student.items()}
