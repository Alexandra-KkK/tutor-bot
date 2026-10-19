import asyncio
from datetime import datetime, timedelta, timezone

from bot import broadcast
from bot.keyboards import B
from bot.logic import local_now
from bot.storage_db import DbStorage

T, S, P, ADMIN = 100, 200, 300, 999


async def register_teacher(h, trial=True):
    await h.send(T, "/start")
    await h.click(T, "reg:teacher")
    calls = await h.send(T, "Александра Сергеевна")
    if trial:
        await h.click(T, "sub:trial")
    return calls


async def test_teacher_registration_and_gate(h):
    calls = await register_teacher(h, trial=False)
    text = h.texts(calls)
    assert "секрет" not in text.lower() and "Как подключить учеников" in text
    assert "https://t.me/test_tutor_bot?start=" in text
    assert "активируйте подписку" in text
    # Без подписки расписание закрыто
    calls = await h.send(T, B.SCHEDULE)
    assert "активируйте подписку" in h.texts(calls)
    calls = await h.click(T, "sub:trial")
    assert "Подписка активна" in h.texts(calls)
    calls = await h.send(T, B.SCHEDULE)
    assert "Раздел расписания" in h.texts(calls)
    # Пробный период второй раз не дают
    calls = await h.click(T, "sub:trial")
    assert any(getattr(c, "show_alert", False) for c in calls)


async def test_bulk_schedule_and_view(h):
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    calls = await h.send(T, "Пн 15:00 Маша 1500\nСр 17:30-19:00 Петя 2000\nПт 10 Аня")
    assert "Сохранить?" in h.texts(calls)
    calls = await h.click(T, "sch:bulk:save")
    assert "3 занятия" in h.texts(calls)
    students = await h.db.students(1)
    assert [s["name"] for s in students] == ["Аня", "Маша", "Петя"]
    calls = await h.click(T, "sch:day:0")
    assert "Маша" in h.texts(calls) and "15:00–16:00" in h.texts(calls)
    calls = await h.click(T, "sch:week")
    assert "Петя" in h.texts(calls)
    # Повторная замена расписания не дублирует занятия
    await h.click(T, "sch:bulk")
    await h.send(T, "Пн 16:00 Маша")
    await h.click(T, "sch:bulk:save")
    calls = await h.click(T, "sch:week")
    text = h.texts(calls)
    assert "16:00" in text and "Петя" not in text


async def test_add_lesson_step_by_step(h):
    await register_teacher(h)
    await h.click(T, "sch:add")
    await h.click(T, "sch:st:new")
    await h.send(T, "Вова")
    await h.click(T, "sch:wd:1")
    calls = await h.send(T, "18:00")
    assert "Сколько длится" in h.texts(calls)
    calls = await h.click(T, "sch:dur:90")
    assert "Добавлено" in h.texts(calls)
    weekly = await h.db.weekly(1)
    assert weekly[0]["duration"] == 90 and weekly[0]["weekday"] == 1
    # Разовое занятие
    await h.click(T, "sch:single")
    calls = await h.click(T, f"sch:st:{weekly[0]['student_id']}")
    assert "На какую дату" in h.texts(calls)
    await h.send(T, "завтра")
    calls = await h.send(T, "12:00-13:00")
    assert "Разовое занятие" in h.texts(calls)
    assert len(await h.db.singles(1)) == 1


async def join_family(h):
    teacher = await h.db.get_teacher(1)
    student = await h.db.find_student_by_name(1, "Маша")
    # Ученик — по личной ссылке, ничего выбирать не нужно
    calls = await h.send(S, f"/start {teacher['student_secret']}_{student['id']}")
    assert "Ученик: <b>Маша</b>" in h.texts(calls)
    calls = await h.click(S, "join:student")
    assert "теперь в боте" in h.texts(calls)  # уведомление учителю
    # Родитель — по общей ссылке: списка чужих учеников не видит, пишет имя
    await h.send(P, f"/start {teacher['student_secret']}")
    calls = await h.click(P, "join:parent")
    assert not any("Петя" in b for b in h.buttons(calls))
    await h.send(P, "маша")
    assert (await h.db.get_account(P))["student_id"] == student["id"]
    return student


async def test_family_join_and_notifications(h, monkeypatch):
    # Понедельник 19.10.2026, 9:00 по Москве: урок в 15:00 ещё впереди в любое время запуска тестов
    _freeze(monkeypatch, "2026-10-19T09:00:00")
    await register_teacher(h)
    teacher = await h.db.get_teacher(1)
    today = local_now(teacher["tz_offset"]).date()
    day = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][today.weekday()]
    await h.click(T, "sch:bulk")
    await h.send(T, f"{day} 15:00 Маша 1500")
    await h.click(T, "sch:bulk:save")
    student = await join_family(h)
    assert len(await h.db.student_accounts(student["id"])) == 2

    calls = await h.send(S, B.MY_SCHEDULE)
    assert "15:00–16:00" in h.texts(calls)
    calls = await h.send(P, B.NEXT_LESSON)
    assert "15:00" in h.texts(calls)

    # Напоминание за час: ставим «сейчас» на 14:10 по времени преподавателя
    tz = timezone(timedelta(hours=teacher["tz_offset"]))
    at = datetime.combine(today, datetime.strptime("14:10", "%H:%M").time(), tzinfo=tz)
    start = len(h.session.calls)
    await broadcast.tick(h.bot, h.db, h.config, at.astimezone(timezone.utc))
    sent = h.session.calls[start:]
    reminder = next(c for c in sent if getattr(c, "chat_id", None) == S and "Урок сегодня" in (c.text or ""))
    no_button = [b for b in h.buttons([reminder]) if "rem:no:" in b][0].split("|")[1]
    # Повторно не шлёт
    start = len(h.session.calls)
    await broadcast.tick(h.bot, h.db, h.config, at.astimezone(timezone.utc))
    assert not any(getattr(c, "chat_id", None) == S for c in h.session.calls[start:])

    # Ученик отвечает «Не смогу» — преподаватель получает кнопки отмены и переноса
    calls = await h.click(S, no_button)
    to_teacher = [c for c in calls if getattr(c, "chat_id", None) == T]
    assert to_teacher and "не сможет прийти" in to_teacher[0].text
    assert any(b.endswith(no_button.replace("rem:no:", "sch:cx:")) for b in h.buttons(to_teacher))

    # После урока — отчёт родителю
    at = at.replace(hour=16, minute=5)
    start = len(h.session.calls)
    await broadcast.tick(h.bot, h.db, h.config, at.astimezone(timezone.utc))
    sent = h.session.calls[start:]
    assert any(getattr(c, "chat_id", None) == P and "Урок проведён" in (c.text or "") for c in sent)
    assert not any(getattr(c, "chat_id", None) == S and "Урок проведён" in (getattr(c, "text", "") or "") for c in sent)


async def test_weekly_broadcast_and_link(h):
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    await h.send(T, "Пн 15:00 Маша\nВт 15:00 Маша\nСр 10:00 Петя")
    await h.click(T, "sch:bulk:save")
    await h.click(T, "set:link")
    await h.send(T, "https://docs.google.com/spreadsheets/d/abc")
    await join_family(h)
    calls = await h.click(T, "set:wnow")
    to_student = [c for c in calls if getattr(c, "chat_id", None) == S]
    assert to_student and "docs.google.com" in to_student[0].text and "Петя" not in to_student[0].text
    assert "2 из 2" in h.texts(calls)


async def test_cancel_and_move_notifies_family(h):
    await register_teacher(h)
    teacher = await h.db.get_teacher(1)
    today = local_now(teacher["tz_offset"]).date()
    tomorrow = today + timedelta(days=1)
    day = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][tomorrow.weekday()]
    await h.click(T, "sch:bulk")
    await h.send(T, f"{day} 15:00 Маша")
    await h.click(T, "sch:bulk:save")
    await join_family(h)
    lesson = (await h.db.weekly(1))[0]
    calls = await h.click(T, f"sch:cd:{tomorrow.isoformat()}")
    assert f"sch:cl:{tomorrow.isoformat()}:w:{lesson['id']}" in "".join(h.buttons(calls))
    await h.click(T, f"sch:mv:{tomorrow.isoformat()}:w:{lesson['id']}")
    await h.send(T, (tomorrow + timedelta(days=1)).strftime("%d.%m.%Y"))
    calls = await h.send(T, "17:00")
    assert any(getattr(c, "chat_id", None) == P and "перенесено" in c.text for c in calls)
    assert (lesson["id"], tomorrow.isoformat()) in await h.db.cancelled(1)
    assert len(await h.db.singles(1)) == 1


async def test_income_and_payments(h):
    await register_teacher(h)
    await h.click(T, "stu:add")
    await h.send(T, "Маша")
    calls = await h.send(T, "1500")
    assert "добавлен" in h.texts(calls)
    s = await h.db.find_student_by_name(1, "Маша")
    calls = await h.click(T, f"inc:pay:{s['id']}")
    assert f"inc:amt:{s['id']}:6000" in "".join(h.buttons(calls))
    calls = await h.click(T, f"inc:amt:{s['id']}:6000")
    assert "Оплата записана" in h.texts(calls)
    calls = await h.send(T, B.INCOME)
    assert "6 000 ₽" in h.texts(calls)
    calls = await h.click(T, "inc:debts")
    assert "предоплата 6 000 ₽" in h.texts(calls)
    calls = await h.click(T, f"stu:card:{s['id']}")
    assert "Предоплата" in h.texts(calls)


async def test_support_roundtrip(h):
    await register_teacher(h)
    await h.send(T, B.SUPPORT)
    calls = await h.send(T, "Не работает оплата")
    to_admin = [c for c in calls if getattr(c, "chat_id", None) == ADMIN]
    assert to_admin
    admin_msg = next(c for c in calls if type(c).__name__ == "SendMessage" and c.chat_id == ADMIN)
    # находим id сообщения админу по ссылке в базе
    link = await h.db._one("SELECT admin_msg_id FROM support_links WHERE admin_chat_id = ?", ADMIN)
    calls = await h.send(ADMIN, "Сейчас поправим", reply_to=link["admin_msg_id"])
    assert any(getattr(c, "chat_id", None) == T and "Поддержка" in (getattr(c, "text", "") or "") for c in calls)


async def test_student_writes_teacher(h):
    await register_teacher(h)
    await h.click(T, "stu:add")
    await h.send(T, "Маша")
    await h.click(T, "stu:add:skip")
    await join_family(h)
    await h.send(S, B.SUPPORT)
    calls = await h.send(S, "Можно перенести урок?")
    assert any(getattr(c, "chat_id", None) == T and "Маша (ученик)" in (getattr(c, "text", "") or "") for c in calls)


async def test_cancel_and_restart(h):
    await register_teacher(h)
    await h.click(T, "stu:add")
    calls = await h.send(T, B.CANCEL)
    assert "Действие отменено" in h.texts(calls)
    calls = await h.send(T, B.RESTART)
    assert "удалён полностью" in h.texts(calls)
    await h.click(T, "restart:yes")
    assert await h.db.get_account(T) is None
    assert await h.db.get_teacher(1) is None


async def test_second_teacher_account_login(h):
    await register_teacher(h)
    teacher = await h.db.get_teacher(1)
    calls = await h.click(T, "inv:login")
    assert f"start=login_{teacher['teacher_secret']}" in h.texts(calls)
    calls = await h.send(555, f"/start login_{teacher['teacher_secret']}")
    assert "Вход выполнен" in h.texts(calls)
    calls = await h.send(555, B.SCHEDULE)
    assert "Раздел расписания" in h.texts(calls)


async def test_memes(h):
    from aiogram.types import PhotoSize
    await register_teacher(h)
    await h.click(T, "set:memes")
    calls = await h.send(T, None, photo=[PhotoSize(file_id="PHOTO1", file_unique_id="u", width=1, height=1)])
    assert "В коллекции: 1" in h.texts(calls)
    assert (await h.db.random_meme(1))["file_id"] == "PHOTO1"


async def test_admin_grant(h):
    await register_teacher(h, trial=False)
    teacher = await h.db.get_teacher(1)
    calls = await h.send(ADMIN, f"/grant {teacher['teacher_secret']} 30")
    assert "доступ до" in h.texts(calls)
    calls = await h.send(T, B.SCHEDULE)
    assert "Раздел расписания" in h.texts(calls)


async def test_relink_wrong_student(h):
    await register_teacher(h)
    await h.click(T, "stu:add")
    await h.send(T, "Маша Иванова")
    await h.click(T, "stu:add:skip")
    teacher = await h.db.get_teacher(1)
    await h.send(P, f"/start {teacher['student_secret']}")
    await h.click(P, "join:parent")
    calls = await h.send(P, "Мария Иванова")  # не совпало — создан новый ученик
    wrong = (await h.db.get_account(P))["student_id"]
    assert f"rel:{P}:{wrong}" in "".join(h.buttons(calls))
    right = await h.db.find_student_by_name(1, "Маша Иванова")
    assert wrong != right["id"]
    calls = await h.click(T, f"rel:{P}:{wrong}")
    assert f"rln:{P}:{wrong}:{right['id']}" in "".join(h.buttons(calls))
    await h.click(T, f"rln:{P}:{wrong}:{right['id']}")
    assert (await h.db.get_account(P))["student_id"] == right["id"]
    assert await h.db.get_student(1, wrong) is None


async def test_homework(h):
    await register_teacher(h)
    await h.click(T, "stu:add")
    await h.send(T, "Маша")
    await h.click(T, "stu:add:skip")
    student = await join_family(h)
    await h.click(T, f"hw:{student['id']}")
    calls = await h.send(T, "№ 125, 126 из учебника")
    copies = [c for c in calls if type(c).__name__ == "CopyMessage"]
    assert {c.chat_id for c in copies} == {S, P}
    assert "2 из 2" in h.texts(calls)




async def test_homework_submit_and_check(h, monkeypatch):
    # Понедельник 19.10.2026: следующий урок Маши — в четверг
    _freeze(monkeypatch, "2026-10-19T09:00:00")
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    await h.send(T, "Чт 15:00 Маша 1500")
    await h.click(T, "sch:bulk:save")
    student = await join_family(h)

    # Преподаватель отправляет задание: родителю — со сроком, ученику — тоже
    await h.click(T, f"hw:{student['id']}")
    calls = await h.send(T, "№ 125, 126 из учебника")
    to_parent = [c for c in calls if getattr(c, "chat_id", None) == P
                 and "Домашнее задание" in (getattr(c, "text", None) or "")]
    assert "четверг, 22.10 в 15:00" in to_parent[0].text
    assert "выполнить до следующего урока" in to_parent[0].text
    to_student = [c for c in calls if getattr(c, "chat_id", None) == S
                  and "Домашнее задание" in (getattr(c, "text", None) or "")]
    assert "Сделать до следующего урока: четверг, 22.10 в 15:00" in to_student[0].text

    # «Готово» — ученику приходит напоминание прислать фото
    calls = await h.click(T, f"hw:done:{student['id']}")
    ask = [c for c in calls if getattr(c, "chat_id", None) == S][0]
    submit_button = [b for b in h.buttons([ask]) if b.startswith("📸")][0].split("|")[1]
    hw = await h.db.last_homework(student["id"])
    assert submit_button == f"hws:{hw['id']}" and hw["status"] == "sent"

    # Ученик сдаёт работу: преподаватель получает её и кнопки отметки
    await h.click(S, submit_button)
    calls = await h.send(S, "вот решение")
    assert [c for c in calls if type(c).__name__ == "CopyMessage"][0].chat_id == T
    assert "Домашнее задание на проверку" in h.texts(calls)
    assert (await h.db.get_homework(hw["id"]))["status"] == "submitted"
    mark = [b for b in h.buttons(calls) if b.startswith("✅")][0].split("|")[1]

    # «Зачтено» видят и ученик, и родитель
    calls = await h.click(T, mark)
    assert "зачтено" in next(c.text for c in calls if getattr(c, "chat_id", None) == S and getattr(c, "text", None))
    assert "зачтено" in next(c.text for c in calls if getattr(c, "chat_id", None) == P and getattr(c, "text", None))
    assert (await h.db.get_homework(hw["id"]))["status"] == "done"
    calls = await h.click(T, f"stu:card:{student['id']}")
    assert "Домашнее задание зачтено" in h.texts(calls)


async def test_homework_redo_and_student_menu_button(h):
    await register_teacher(h)
    await h.click(T, "stu:add")
    await h.send(T, "Маша")
    await h.click(T, "stu:add:skip")
    student = await join_family(h)
    await h.click(T, f"hw:{student['id']}")
    await h.send(T, "№ 125")
    await h.click(T, f"hw:done:{student['id']}")
    hw = await h.db.last_homework(student["id"])

    # Ученик сдаёт через кнопку меню, преподаватель отправляет на доработку
    calls = await h.send(S, B.SEND_HW)
    assert "выполненное задание" in h.texts(calls)
    calls = await h.send(S, "готово")
    redo = [b for b in h.buttons(calls) if b.startswith("🔁")][0].split("|")[1]
    calls = await h.click(T, redo)
    assert "доделать" in next(c.text for c in calls if getattr(c, "chat_id", None) == S and getattr(c, "text", None))
    assert "доделать" in next(c.text for c in calls if getattr(c, "chat_id", None) == P and getattr(c, "text", None))
    assert (await h.db.get_homework(hw["id"]))["status"] == "redo"
    assert (await h.db.open_homework(student["id"]))["id"] == hw["id"]

    # У родителя кнопки «Сдать домашку» нет
    calls = await h.send(P, B.SEND_HW)
    assert "для ученика" in h.texts(calls)

    # Фото без нажатия кнопки тоже уходит преподавателю как домашнее задание
    from aiogram.types import PhotoSize
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=10, height=10)]
    calls = await h.send(S, None, photo=photo, caption="исправил")
    assert [c for c in calls if type(c).__name__ == "CopyMessage"][0].chat_id == T
    assert (await h.db.get_homework(hw["id"]))["status"] == "submitted"

async def test_homework_album_and_several_messages(h):
    from aiogram.types import PhotoSize
    await register_teacher(h)
    await h.click(T, "stu:add")
    await h.send(T, "Маша")
    await h.click(T, "stu:add:skip")
    student = await join_family(h)
    await h.click(T, f"hw:{student['id']}")
    start = len(h.session.calls)
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=10, height=10)]
    # Фото из альбома приходят почти одновременно
    await asyncio.gather(h.send(T, None, photo=photo, caption="№ 125", media_group_id="A1"),
                         h.send(T, None, photo=photo, media_group_id="A1"))
    await h.send(T, "И ещё № 126")
    calls = h.session.calls[start:]
    copies = [c for c in calls if type(c).__name__ == "CopyMessage"]
    assert len(copies) == 6  # три сообщения ученику и родителю
    heads = [c for c in calls if "Домашнее задание" in (getattr(c, "text", None) or "")]
    assert sorted(c.chat_id for c in heads) == sorted([S, P])  # заголовок один раз
    confirms = [c for c in calls if "Отправлено ученику" in (getattr(c, "text", None) or "")]
    assert len(confirms) == 2  # на альбом и на текст
    await h.click(T, f"hw:done:{student['id']}")
    calls = await h.send(T, "просто текст")
    assert not [c for c in calls if type(c).__name__ == "CopyMessage"]

async def test_parent_with_two_children(h):
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    await h.send(T, "Пн 15:00 Маша\nВт 16:00 Петя")
    await h.click(T, "sch:bulk:save")
    teacher = await h.db.get_teacher(1)
    masha = await h.db.find_student_by_name(1, "Маша")
    petya = await h.db.find_student_by_name(1, "Петя")
    for s in (masha, petya):
        await h.send(P, f"/start {teacher['student_secret']}_{s['id']}")
        await h.click(P, "join:parent")
    assert set(await h.db.children(P)) == {masha["id"], petya["id"]}
    calls = await h.send(P, "📅 Моё расписание")
    text = h.texts(calls)
    assert "Маша" in text and "Петя" in text
    calls = await h.click(T, "set:wnow")
    to_parent = [c for c in calls if getattr(c, "chat_id", None) == P]
    assert len(to_parent) == 2
    # Удаление одного ребёнка не отключает родителя
    await h.click(T, f"stu:del:yes:{masha['id']}")
    assert await h.db.children(P) == [petya["id"]]
    assert (await h.db.get_account(P))["student_id"] == petya["id"]


async def test_free_mode(h):
    from bot import subscription
    subscription.ENABLED = False
    try:
        calls = await register_teacher(h, trial=False)
        assert "активируйте подписку" not in h.texts(calls)
        calls = await h.send(T, B.SCHEDULE)
        assert "Раздел расписания" in h.texts(calls)
        calls = await h.send(T, B.CANCEL_SUB)
        assert "бесплатный доступ" in h.texts(calls)
        await broadcast.tick(h.bot, h.db, h.config)
    finally:
        subscription.ENABLED = True


async def test_payment_flow(h):
    from aiogram.types import SuccessfulPayment
    await register_teacher(h, trial=False)
    calls = await h.click(T, "sub:pay")
    assert "Онлайн-оплата скоро появится" in h.texts(calls)
    h.config.payment_provider_token = "PROVIDER"
    try:
        calls = await h.click(T, "sub:pay")
        invoice = [c for c in calls if type(c).__name__ == "SendInvoice"][0]
        assert invoice.prices[0].amount == 29900 and invoice.currency == "RUB"
        sp = SuccessfulPayment(currency="RUB", total_amount=29900, invoice_payload=invoice.payload,
                               telegram_payment_charge_id="tg1", provider_payment_charge_id="p1")
        await h.send(T, B.SUPPORT)  # незаконченное действие не должно «съесть» оплату
        calls = await h.send(T, None, successful_payment=sp)
        assert "Оплата прошла" in h.texts(calls)
        calls = await h.send(T, B.SCHEDULE)
        assert "Раздел расписания" in h.texts(calls)
    finally:
        h.config.payment_provider_token = ""


async def test_owner_free_and_promo(h):
    # Владелец (админ) регистрируется как преподаватель и не платит
    await h.send(ADMIN, "/start")
    await h.click(ADMIN, "reg:teacher")
    calls = await h.send(ADMIN, "Александра")
    assert "активируйте подписку" not in h.texts(calls)
    calls = await h.send(ADMIN, B.SCHEDULE)
    assert "Раздел расписания" in h.texts(calls)
    # Создаёт промокод через панель
    calls = await h.send(ADMIN, "🛠 Админ-панель")
    assert "Админ-панель" in h.texts(calls)
    await h.click(ADMIN, "adm:new")
    await h.click(ADMIN, "adm:nd:90")
    await h.click(ADMIN, "adm:nu:90:1")
    calls = await h.send(ADMIN, "olga2026")
    assert "start=promo_OLGA2026" in h.texts(calls)
    # Подруга приходит по ссылке с промокодом
    calls = await h.send(T, "/start promo_OLGA2026")
    assert "OLGA2026" in h.texts(calls)
    await h.click(T, "reg:teacher")
    calls = await h.send(T, "Ольга")
    assert "активирован" in h.texts(calls) and "активируйте подписку" not in h.texts(calls)
    calls = await h.send(T, B.SCHEDULE)
    assert "Раздел расписания" in h.texts(calls)
    # Второй раз этот промокод не сработает (лимит 1)
    await h.send(555, "/start")
    await h.click(555, "reg:teacher")
    await h.send(555, "Ира")
    await h.click(555, "sub:promo")
    calls = await h.send(555, "OLGA2026")
    assert "максимальное число" in h.texts(calls)
    # Промокод навсегда
    await h.click(ADMIN, "adm:new")
    await h.click(ADMIN, "adm:nd:0")
    await h.click(ADMIN, "adm:nu:0:0")
    await h.click(ADMIN, "adm:gen")
    code = next(p["code"] for p in await h.db.promos() if p["days"] == 0)
    calls = await h.send(555, f"/start promo_{code}")
    assert "навсегда" in h.texts(calls)


def _freeze(monkeypatch, local_iso: str, offset: int = 3):
    from bot import logic
    moment = datetime.fromisoformat(local_iso).replace(tzinfo=timezone(timedelta(hours=offset)))
    monkeypatch.setattr(logic, "utcnow", lambda: moment.astimezone(timezone.utc))
    return moment


async def test_schedule_change_keeps_today_history(h, monkeypatch):
    # Пятница 16.10.2026, 15:00 по Москве
    _freeze(monkeypatch, "2026-10-16T09:00:00")
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    await h.send(T, "Пт 12:00 Петя 2000")
    await h.click(T, "sch:bulk:save")
    _freeze(monkeypatch, "2026-10-16T15:00:00")
    # Урок Пети в 12:00 уже прошёл — он должен остаться в доходе после замены расписания
    await h.click(T, "sch:bulk")
    await h.send(T, "Пт 10:00 Маша 1000")
    await h.click(T, "sch:bulk:save")
    calls = await h.click(T, "sch:today")
    text = h.texts(calls)
    assert "Петя" in text and "Маша" not in text  # Маша начнётся только со следующей пятницы
    calls = await h.send(T, B.INCOME)
    assert "Эта неделя: <b>2 000 ₽</b>" in h.texts(calls)
    calls = await h.click(T, "sch:day:4")  # ближайшая пятница — сегодня
    _freeze(monkeypatch, "2026-10-17T10:00:00")
    calls = await h.click(T, "sch:week")
    text = h.texts(calls)
    assert "Маша" in text and "Петя" not in text


async def test_delete_student_keeps_past_income(h, monkeypatch):
    _freeze(monkeypatch, "2026-10-12T09:00:00")  # понедельник
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    await h.send(T, "Пн 10:00 Маша 1000\nСр 10:00 Маша 1000")
    await h.click(T, "sch:bulk:save")
    _freeze(monkeypatch, "2026-10-14T12:00:00")  # среда, оба урока прошли
    masha = await h.db.find_student_by_name(1, "Маша")
    await h.click(T, f"stu:del:yes:{masha['id']}")
    calls = await h.send(T, B.INCOME)
    assert "Эта неделя: <b>2 000 ₽</b>" in h.texts(calls)


async def test_lesson_report(h, monkeypatch):
    _freeze(monkeypatch, "2026-10-16T09:00:00")  # пятница
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    await h.send(T, "Пт 16:00 Маша 1500")
    await h.click(T, "sch:bulk:save")
    await join_family(h)
    moment = _freeze(monkeypatch, "2026-10-16T17:05:00")
    start = len(h.session.calls)
    await broadcast.tick(h.bot, h.db, h.config, moment.astimezone(timezone.utc))
    prompt = [c for c in h.session.calls[start:] if getattr(c, "chat_id", None) == T]
    data = [b.split("|")[1] for b in h.buttons(prompt) if "Заполнить" in b][0]
    calls = await h.click(T, data)
    assert "Старательность|rep:noop" in h.buttons(calls)
    n = [b for b in h.buttons(calls) if "rep:next:" in b][0].rsplit(":", 1)[1]
    await h.click(T, f"rep:r:{n}:effort:5")
    await h.click(T, f"rep:r:{n}:understanding:4")
    await h.click(T, f"rep:next:{n}")
    calls = await h.send(T, "Квадратные уравнения, всё поняла")
    to_parent = [c for c in calls if getattr(c, "chat_id", None) == P]
    assert to_parent and "★★★★★" in to_parent[0].text and "★★★★☆" in to_parent[0].text
    assert "Квадратные уравнения" in to_parent[0].text and "Продуктивность" not in to_parent[0].text
    assert not any(getattr(c, "chat_id", None) == S for c in calls)  # ученику оценки не приходят
    assert len(await h.db.reports(1, (await h.db.find_student_by_name(1, "Маша"))["id"])) == 1
    # Из карточки ученика — отчёт о последнем проведённом уроке
    masha = await h.db.find_student_by_name(1, "Маша")
    calls = await h.click(T, f"rep:last:{masha['id']}")
    assert "16:00–17:00" in h.texts(calls)
    calls2 = await h.click(T, f"rep:last:{masha['id']}")
    n2 = [b for b in h.buttons(calls2) if "rep:next:" in b][0].rsplit(":", 1)[1]
    # Кнопки предыдущего отчёта больше не действуют
    old = [b for b in h.buttons(calls) if "rep:next:" in b][0].split("|")[1]
    calls = await h.click(T, old)
    assert "уже закрыт" in str(calls)
    # Пустой отчёт не отправляется
    await h.click(T, f"rep:next:{n2}")
    calls = await h.click(T, f"rep:send:{n2}")
    assert "Отчёт пустой" in h.texts(calls)


async def test_resave_keeps_cancellations_and_moves(h, monkeypatch):
    _freeze(monkeypatch, "2026-10-16T09:00:00")  # пятница
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    await h.send(T, "Пн 15:00 Маша\nСр 15:00 Петя")
    await h.click(T, "sch:bulk:save")
    masha, petya = sorted(await h.db.weekly(1), key=lambda w: w["weekday"])
    await h.click(T, f"sch:cx:2026-10-19:w:{masha['id']}")
    await h.click(T, f"sch:mv:2026-10-21:w:{petya['id']}")
    await h.send(T, "22.10")
    await h.send(T, "15-16")
    # Пересохраняем расписание, чтобы добавить Аню: отмена и перенос должны остаться
    await h.click(T, "sch:bulk")
    await h.send(T, "Пн 15:00 Маша\nСр 15:00 Петя\nЧт 12:00 Аня")
    await h.click(T, "sch:bulk:save")
    calls = await h.click(T, "sch:week")
    text = h.texts(calls)
    assert "Маша" not in text and text.count("Петя") == 1 and "Аня" in text
    # Вернуть перенесённый урок: разовое занятие удаляется, остаётся исходное
    new_petya = [w for w in await h.db.weekly(1) if w["weekday"] == 2 and not w["valid_to"]][0]
    calls = await h.click(T, "sch:cd:2026-10-21")
    assert any("Отменить перенос" in b for b in h.buttons(calls))
    await h.click(T, f"sch:rs:2026-10-21:{new_petya['id']}")
    assert await h.db.singles(1) == []
    calls = await h.click(T, "sch:week")
    assert h.texts(calls).count("Петя") == 1


async def test_restart_does_not_renew_trial_or_promo(h):
    await h.db.create_promo("FREE30", 30, None)
    await register_teacher(h)
    await h.send(T, "/start promo_FREE30")
    await h.send(T, B.RESTART)
    await h.click(T, "restart:yes")
    await register_teacher(h, trial=False)
    calls = await h.click(T, "sub:trial")
    assert "уже был использован" in str(calls)
    calls = await h.send(T, "/start promo_FREE30")
    assert "уже активировали" in h.texts(calls)
    teacher = (await h.db.all_teachers())[-1]
    assert not teacher["trial_until"] and not teacher["paid_until"]


async def test_lesson_ending_after_midnight(h, monkeypatch):
    _freeze(monkeypatch, "2026-10-16T09:00:00")  # пятница
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    await h.send(T, "Пт 23:30 Маша 1500 60")
    await h.click(T, "sch:bulk:save")
    await join_family(h)
    moment = _freeze(monkeypatch, "2026-10-17T00:31:00")  # 00:31 субботы по Москве
    start = len(h.session.calls)
    await broadcast.tick(h.bot, h.db, h.config, moment.astimezone(timezone.utc))
    sent = h.session.calls[start:]
    assert any(getattr(c, "chat_id", None) == P and "Урок проведён" in c.text for c in sent)
    assert any("rep:2026-10-16:w:" in b for b in h.buttons([c for c in sent if getattr(c, "chat_id", None) == T]))


async def test_first_teacher_becomes_owner_without_admin_ids(h):
    from bot import keyboards
    saved = set(h.config.admin_ids)
    h.config.admin_ids.clear()
    keyboards.ADMIN_IDS.clear()
    try:
        calls = await register_teacher(h, trial=False)
        menus = [c.reply_markup for c in calls if hasattr(getattr(c, "reply_markup", None), "keyboard")]
        assert any(b.text == B.ADMIN for m in menus for row in m.keyboard for b in row)
        assert (await h.db.get_teacher(1))["free_forever"]
        assert await h.db.get_setting("owner_id") == str(T)
    finally:
        h.config.admin_ids.clear()
        h.config.admin_ids.update(saved)
        keyboards.ADMIN_IDS.clear()
        keyboards.ADMIN_IDS.update(saved)



async def test_invite_screen_preview_and_family_buttons(h):
    await register_teacher(h)
    await h.click(T, "sch:bulk")
    await h.send(T, "Пн 15:00 Маша 1500\nСр 15:00 Петя 3000")
    await h.click(T, "sch:bulk:save")
    calls = await h.send(T, B.SECRETS)
    assert "Как подключить учеников" in h.texts(calls)
    calls = await h.click(T, "inv:preview")
    text = h.texts(calls)
    assert "Твоё расписание" in text and "₽" not in text and "1500" not in text
    # Старая кнопка «Мои секреты» тоже работает
    calls = await h.send(T, B.OLD_SECRETS)
    assert "Как подключить учеников" in h.texts(calls)
    # Родитель нажал «Я родитель» и переслал ссылку целиком; O и 0 не путаются
    teacher = await h.db.get_teacher(1)
    await h.send(P, "/start")
    await h.click(P, "reg:parent")
    code = teacher["student_secret"]
    calls = await h.send(P, f"https://t.me/test_tutor_bot?start={code.lower()}")
    assert "имя и фамилию ребёнка" in h.texts(calls)
    await h.send(P, "Маша")
    acc = await h.db.get_account(P)
    assert acc["role"] == "parent" and acc["student_id"] == (await h.db.find_student_by_name(1, "Маша"))["id"]
    # Неверная ссылка — понятный ответ без слова «секрет»
    await h.send(S, "/start")
    await h.click(S, "reg:student")
    calls = await h.send(S, "XXXXXXXX")
    assert "Не получилось найти приглашение" in h.texts(calls)
    calls = await h.send(S, "/start WRONGCODE")
    assert "Не получилось найти приглашение" in h.texts(calls)


async def test_codes_ignore_lookalike_symbols(h):
    await register_teacher(h)
    await h.db.update_teacher(1, student_secret="PN65N60ZMO")
    assert await h.db.teacher_by_student_secret("pn65n6ozm0")


async def test_dialog_state_lives_in_database(h):
    """В облаке процесс не живёт между сообщениями, поэтому состояние диалога берётся из базы."""
    h.dp.fsm.storage = DbStorage(h.db)
    await register_teacher(h)
    await h.click(T, "sch:add")
    await h.click(T, "sch:st:new")
    await h.send(T, "Вова")
    await h.click(T, "sch:wd:1")
    # многошаговый диалог дошёл до конца, хотя память между шагами не использовалась
    assert await h.db._one("SELECT 1 FROM fsm WHERE state IS NOT NULL")
    await h.send(T, "18:00")
    calls = await h.click(T, "sch:dur:90")
    assert "Добавлено" in h.texts(calls)
    weekly = await h.db.weekly(1)
    assert weekly[0]["duration"] == 90 and weekly[0]["weekday"] == 1
