from datetime import date, datetime, timedelta, timezone

from bot import subscription
from bot.logic import (income, lessons_on, money, nearest_date, parse_amount, parse_date,
                       parse_schedule_text, parse_time, parse_time_range, plural)


def test_parse_time():
    assert parse_time("15") == "15:00"
    assert parse_time("9.30") == "09:30"
    assert parse_time_range("15-16") == ("15:00", 60)
    assert parse_time_range("9.30 - 10.15") == ("09:30", 45)
    assert parse_time("25:00") is None
    assert parse_time_range("15:00-16:30") == ("15:00", 90)
    assert parse_time_range("15:00") == ("15:00", None)
    assert parse_time_range("16:00-15:00") is None


def test_parse_date():
    today = date(2026, 10, 15)  # четверг
    assert parse_date("завтра", today) == date(2026, 10, 16)
    assert parse_date("19.10", today) == date(2026, 10, 19)
    assert parse_date("2026-10-20", today) == date(2026, 10, 20)
    assert parse_date("пн", today) == date(2026, 10, 19)
    assert parse_date("05.01", today) == date(2027, 1, 5)
    assert parse_date("31.02", today) is None
    assert parse_date("абв", today) is None


def test_parse_amount():
    assert parse_amount("1 500") == 1500
    assert parse_amount("1500₽") == 1500
    assert parse_amount("1500 руб") == 1500
    assert parse_amount("abc") is None


def test_parse_schedule():
    entries, errors = parse_schedule_text(
        "Пн 15:00 Маша Иванова 1500\nср 17:30-19:00 Петя 2000\nПт 10 Аня 1200 45\nВс 12:00 Коля\nплохая строка")
    assert len(entries) == 4
    assert entries[0].name == "Маша Иванова" and entries[0].price == 1500 and entries[0].duration is None
    assert entries[1].duration == 90 and entries[1].weekday == 2
    assert entries[2].duration == 45 and entries[2].time == "10:00"
    assert entries[3].price is None
    assert len(errors) == 1
    entries, _ = parse_schedule_text("Пн 15:00 - 16:00 Маша 1500\nВт 15 – 16 Петя")
    assert [(e.name, e.duration) for e in entries] == [("Маша", 60), ("Петя", 60)]


def _data():
    students = {1: {"id": 1, "name": "Маша", "price": 1000}, 2: {"id": 2, "name": "Петя", "price": 2000}}
    weekly = [
        {"id": 10, "student_id": 1, "weekday": 0, "time": "15:00", "duration": 60, "price": None,
         "valid_from": "2026-10-01", "valid_to": None},
        {"id": 11, "student_id": 2, "weekday": 2, "time": "17:00", "duration": 90, "price": 2500,
         "valid_from": "2026-10-01", "valid_to": "2026-10-20"},
    ]
    singles = [{"id": 20, "student_id": 1, "date": "2026-10-17", "time": "12:00", "duration": 60, "price": None}]
    return weekly, singles, students


def test_lessons_on_and_cancel():
    weekly, singles, students = _data()
    assert [l.student_name for l in lessons_on(date(2026, 10, 19), weekly, singles, set(), students)] == ["Маша"]
    assert lessons_on(date(2026, 10, 19), weekly, singles, {(10, "2026-10-19")}, students) == []
    assert lessons_on(date(2026, 10, 14), weekly, singles, set(), students)[0].price == 2500
    assert lessons_on(date(2026, 10, 21), weekly, singles, set(), students) == []  # valid_to прошёл
    assert lessons_on(date(2026, 10, 17), weekly, singles, set(), students)[0].kind == "s"


def test_income():
    weekly, singles, students = _data()
    now = datetime(2026, 10, 19, 15, 30)
    inc = income(date(2026, 10, 1), date(2026, 10, 31), now, weekly, singles, set(), students)
    # Маша: пн 5, 12, 19 (начался) по 1000 + разовое 17-го = 4000; Петя: ср 7, 14 по 2500 = 5000
    assert inc.total == 9000
    assert inc.count == 6
    assert inc.planned_count == 1  # пн 26-го


def test_nearest_and_format():
    assert nearest_date(date(2026, 10, 15), 3) == date(2026, 10, 15)
    assert nearest_date(date(2026, 10, 15), 0) == date(2026, 10, 19)
    assert money(15000) == "15 000 ₽"
    assert plural(1, "a", "b", "c") == "a" and plural(3, "a", "b", "c") == "b" and plural(11, "a", "b", "c") == "c"


def test_subscription():
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    t = {"trial_until": None, "paid_until": None, "trial_used": 0, "auto_renew": 0, "tz_offset": 3}
    assert not subscription.status(t, now).active
    t["trial_until"] = (now + timedelta(days=15)).isoformat()
    st = subscription.status(t, now)
    assert st.active and st.kind == "trial"
    # Оплата во время пробного периода продлевает от его конца
    t["paid_until"] = subscription.extend_paid(t, 30, now)
    assert datetime.fromisoformat(t["paid_until"]) == now + timedelta(days=45)
    assert subscription.status(t, now + timedelta(days=20)).kind == "paid"
    assert not subscription.status(t, now + timedelta(days=46)).active
