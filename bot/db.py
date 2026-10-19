"""Хранилище на SQLite."""
import os
import secrets
import string
from datetime import datetime, timezone

import aiosqlite

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS teachers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    teacher_secret  TEXT NOT NULL UNIQUE,
    student_secret  TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL,
    trial_until     TEXT,
    paid_until      TEXT,
    trial_used      INTEGER NOT NULL DEFAULT 0,
    auto_renew      INTEGER NOT NULL DEFAULT 0,
    tz_offset       INTEGER NOT NULL DEFAULT 3,
    daily_plan      INTEGER NOT NULL DEFAULT 1,
    remind_before   INTEGER NOT NULL DEFAULT 60,
    memes_on        INTEGER NOT NULL DEFAULT 1,
    free_forever    INTEGER NOT NULL DEFAULT 0,
    ask_report      INTEGER NOT NULL DEFAULT 1,
    schedule_link   TEXT NOT NULL DEFAULT '',
    weekly_send     INTEGER NOT NULL DEFAULT 1,
    weekly_day      INTEGER NOT NULL DEFAULT 6,
    weekly_time     TEXT NOT NULL DEFAULT '18:00',
    parent_reports  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    price       INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    tg_id       INTEGER PRIMARY KEY,
    role        TEXT NOT NULL CHECK (role IN ('teacher', 'student', 'parent')),
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    student_id  INTEGER REFERENCES students(id) ON DELETE CASCADE,
    full_name   TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

-- Какие ученики привязаны к аккаунту: у ученика один, у родителя может быть несколько детей.
CREATE TABLE IF NOT EXISTS account_students (
    tg_id       INTEGER NOT NULL REFERENCES accounts(tg_id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    PRIMARY KEY (tg_id, student_id)
);

CREATE TABLE IF NOT EXISTS weekly_lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    weekday     INTEGER NOT NULL,
    time        TEXT NOT NULL,
    duration    INTEGER NOT NULL DEFAULT 60,
    price       INTEGER,
    valid_from  TEXT NOT NULL,
    valid_to    TEXT
);

CREATE TABLE IF NOT EXISTS single_lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    time        TEXT NOT NULL,
    duration    INTEGER NOT NULL DEFAULT 60,
    price       INTEGER
);

CREATE TABLE IF NOT EXISTS cancellations (
    lesson_id   INTEGER NOT NULL REFERENCES weekly_lessons(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    moved_to    INTEGER,                -- id разового занятия, если урок перенесли
    PRIMARY KEY (lesson_id, date)
);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    student_id  INTEGER REFERENCES students(id) ON DELETE SET NULL,
    amount      INTEGER NOT NULL,
    date        TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sub_payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id  INTEGER REFERENCES teachers(id) ON DELETE SET NULL,
    amount      INTEGER NOT NULL,
    days        INTEGER NOT NULL,
    source      TEXT NOT NULL,
    charge_id   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

-- Отчёт об уроке для родителей: оценки 1–5 (0 — не выставлена) и комментарий
CREATE TABLE IF NOT EXISTS lesson_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    time        TEXT NOT NULL,
    effort      INTEGER NOT NULL DEFAULT 0,
    productivity INTEGER NOT NULL DEFAULT 0,
    understanding INTEGER NOT NULL DEFAULT 0,
    comment     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

-- Промокоды для репетиторов: days = 0 означает «бесплатно навсегда»
CREATE TABLE IF NOT EXISTS promo_codes (
    code        TEXT PRIMARY KEY,
    days        INTEGER NOT NULL,
    max_uses    INTEGER,
    used        INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promo_uses (
    code        TEXT NOT NULL,
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (code, teacher_id)
);

-- Состояние диалогов: что человек сейчас вводит. В облаке процесс живёт недолго,
-- поэтому состояние хранится в базе, а не в памяти.
CREATE TABLE IF NOT EXISTS fsm (
    key         TEXT PRIMARY KEY,
    state       TEXT,
    data        TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

-- что человек уже получил (пробный период, промокоды); не удаляется при «Рестарте профиля»
CREATE TABLE IF NOT EXISTS perks (
    tg_id       INTEGER NOT NULL,
    perk        TEXT NOT NULL,
    PRIMARY KEY (tg_id, perk)
);

CREATE TABLE IF NOT EXISTS support_links (
    admin_chat_id   INTEGER NOT NULL,
    admin_msg_id    INTEGER NOT NULL,
    user_tg_id      INTEGER NOT NULL,
    PRIMARY KEY (admin_chat_id, admin_msg_id)
);

CREATE TABLE IF NOT EXISTS memes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    file_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Домашние задания: что отправил преподаватель и что прислал ученик
CREATE TABLE IF NOT EXISTS homework (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    due         TEXT NOT NULL DEFAULT '',      -- срок: дата и время следующего урока
    status      TEXT NOT NULL DEFAULT 'sent',  -- sent | submitted | done | redo
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sent_notifications (
    key         TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_students_teacher ON students(teacher_id);
CREATE INDEX IF NOT EXISTS idx_weekly_teacher ON weekly_lessons(teacher_id);
CREATE INDEX IF NOT EXISTS idx_single_teacher ON single_lessons(teacher_id, date);
CREATE INDEX IF NOT EXISTS idx_accounts_teacher ON accounts(teacher_id);
CREATE INDEX IF NOT EXISTS idx_account_students ON account_students(student_id);
CREATE INDEX IF NOT EXISTS idx_homework_student ON homework(student_id, id);
"""

TEACHER_COLUMNS = {
    "schedule_link": "TEXT NOT NULL DEFAULT ''",
    "weekly_send": "INTEGER NOT NULL DEFAULT 1",
    "weekly_day": "INTEGER NOT NULL DEFAULT 6",
    "weekly_time": "TEXT NOT NULL DEFAULT '18:00'",
    "parent_reports": "INTEGER NOT NULL DEFAULT 1",
    "memes_on": "INTEGER NOT NULL DEFAULT 1",
    "free_forever": "INTEGER NOT NULL DEFAULT 0",
    "ask_report": "INTEGER NOT NULL DEFAULT 1",
}

# без похожих символов: O/0, I/1/L
SECRET_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "O0I1L")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_secret(length: int = 10) -> str:
    return "".join(secrets.choice(SECRET_ALPHABET) for _ in range(length))


class _Sqlite:
    """База в файле на этом компьютере."""

    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row

    async def query(self, sql: str, args: tuple) -> list[dict]:
        async with self.conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def write(self, sql: str, args: tuple) -> tuple[int, int]:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid, cur.rowcount

    async def script(self, sql: str) -> None:
        await self.conn.executescript(sql)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()


class _Libsql:
    """Облачная база Turso: тот же SQL, что у SQLite, но по сети.

    Нужна там, где файлы не сохраняются между запусками, например на Vercel."""

    def __init__(self, url: str, token: str):
        self.url, self.token = url, token
        self.client = None

    async def connect(self) -> None:
        import libsql_client  # ставится только на сервере, локально не нужен
        self.client = libsql_client.create_client(self.url, auth_token=self.token or None)

    async def query(self, sql: str, args: tuple) -> list[dict]:
        rs = await self.client.execute(sql, list(args))
        return [dict(zip(rs.columns, row)) for row in rs.rows]

    async def write(self, sql: str, args: tuple) -> tuple[int, int]:
        rs = await self.client.execute(sql, list(args))
        return rs.last_insert_rowid or 0, rs.rows_affected

    async def script(self, sql: str) -> None:
        # Комментарии выкидываем до разбиения: в них бывает «;», иначе запрос рвётся пополам
        clean = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        for statement in (s.strip() for s in clean.split(";")):
            if not statement:
                continue
            try:
                await self.client.execute(statement)
            except Exception:
                if not statement.upper().startswith("PRAGMA"):  # PRAGMA по сети не поддерживается
                    raise

    async def close(self) -> None:
        if self.client:
            await self.client.close()


CLOUD_SCHEMES = ("libsql://", "wss://", "ws://", "https://", "http://")


class Database:
    def __init__(self, path: str):
        self.path = path
        if path.startswith(CLOUD_SCHEMES):
            self.be = _Libsql(path, os.getenv("TURSO_AUTH_TOKEN", ""))
        else:
            self.be = _Sqlite(path)

    async def connect(self) -> None:
        await self.be.connect()
        await self.be.script(SCHEMA)
        await self._migrate()

    async def _migrate(self) -> None:
        """Добавляет колонки, появившиеся в новых версиях, в уже существующую базу."""
        existing = {row["name"] for row in await self._all("PRAGMA table_info(teachers)")}
        for column, ddl in TEACHER_COLUMNS.items():
            if column not in existing:
                await self._exec(f"ALTER TABLE teachers ADD COLUMN {column} {ddl}")
        cancellations = {row["name"] for row in await self._all("PRAGMA table_info(cancellations)")}
        if "moved_to" not in cancellations:
            await self._exec("ALTER TABLE cancellations ADD COLUMN moved_to INTEGER")

    async def close(self) -> None:
        await self.be.close()

    # ------------------------------------------------------------ helpers

    async def _one(self, sql: str, *args) -> dict | None:
        rows = await self.be.query(sql, args)
        return rows[0] if rows else None

    async def _all(self, sql: str, *args) -> list[dict]:
        return await self.be.query(sql, args)

    async def _exec(self, sql: str, *args) -> int:
        row_id, _ = await self.be.write(sql, args)
        return row_id

    async def _changed(self, sql: str, *args) -> int:
        """Сколько строк изменил запрос."""
        _, changed = await self.be.write(sql, args)
        return changed

    # ------------------------------------------------------------ аккаунты

    async def get_account(self, tg_id: int) -> dict | None:
        return await self._one("SELECT * FROM accounts WHERE tg_id = ?", tg_id)

    async def link_account(self, tg_id: int, role: str, teacher_id: int,
                           student_id: int | None = None, full_name: str = "", username: str = "") -> None:
        """Привязать аккаунт. Родитель того же преподавателя добавляет ещё одного ребёнка,
        в остальных случаях прежние привязки заменяются."""
        existing = await self.get_account(tg_id)
        keep_children = (existing and role == "parent" and existing["role"] == "parent"
                         and existing["teacher_id"] == teacher_id)
        await self._exec(
            "INSERT INTO accounts (tg_id, role, teacher_id, student_id, full_name, username, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(tg_id) DO UPDATE SET role = excluded.role, "
            "teacher_id = excluded.teacher_id, student_id = excluded.student_id, "
            "full_name = excluded.full_name, username = excluded.username",
            tg_id, role, teacher_id, student_id, full_name, username or "", now_iso())
        if not keep_children:
            await self._exec("DELETE FROM account_students WHERE tg_id = ?", tg_id)
        if student_id is not None:
            await self._exec(
                "INSERT OR IGNORE INTO account_students (tg_id, student_id) VALUES (?, ?)", tg_id, student_id)

    async def relink_child(self, tg_id: int, old_student_id: int | None, new_student_id: int) -> None:
        if old_student_id is not None:
            await self._exec("DELETE FROM account_students WHERE tg_id = ? AND student_id = ?", tg_id, old_student_id)
        await self._exec("INSERT OR IGNORE INTO account_students (tg_id, student_id) VALUES (?, ?)",
                         tg_id, new_student_id)
        await self._exec("UPDATE accounts SET student_id = ? WHERE tg_id = ?", new_student_id, tg_id)

    async def children(self, tg_id: int) -> list[int]:
        rows = await self._all("SELECT student_id FROM account_students WHERE tg_id = ? ORDER BY student_id", tg_id)
        return [r["student_id"] for r in rows]

    async def unlink_account(self, tg_id: int) -> None:
        await self._exec("DELETE FROM accounts WHERE tg_id = ?", tg_id)

    async def teacher_accounts(self, teacher_id: int) -> list[int]:
        rows = await self._all(
            "SELECT tg_id FROM accounts WHERE teacher_id = ? AND role = 'teacher'", teacher_id)
        return [r["tg_id"] for r in rows]

    _FAMILY_SQL = (
        "SELECT a.tg_id, a.role, a.teacher_id, x.student_id, a.full_name, a.username "
        "FROM account_students x JOIN accounts a ON a.tg_id = x.tg_id "
    )

    async def student_accounts(self, student_id: int, role: str | None = None) -> list[dict]:
        """Аккаунты ученика и его родителей."""
        sql, args = self._FAMILY_SQL + "WHERE x.student_id = ?", [student_id]
        if role:
            sql += " AND a.role = ?"
            args.append(role)
        return await self._all(sql, *args)

    async def family_accounts(self, teacher_id: int) -> list[dict]:
        """Пары (аккаунт, ученик): родитель двоих детей встречается дважды."""
        return await self._all(
            self._FAMILY_SQL + "WHERE a.teacher_id = ? AND a.role IN ('student', 'parent')", teacher_id)

    # ------------------------------------------------------------ преподаватели

    async def create_teacher(self, name: str, tz_offset: int) -> dict:
        while True:
            t_secret, s_secret = new_secret(), new_secret()
            exists = await self._one(
                "SELECT id FROM teachers WHERE teacher_secret IN (?, ?) OR student_secret IN (?, ?)",
                t_secret, s_secret, t_secret, s_secret)
            if not exists:
                break
        teacher_id = await self._exec(
            "INSERT INTO teachers (name, teacher_secret, student_secret, created_at, tz_offset) "
            "VALUES (?, ?, ?, ?, ?)",
            name, t_secret, s_secret, now_iso(), tz_offset)
        return await self.get_teacher(teacher_id)

    async def get_teacher(self, teacher_id: int) -> dict | None:
        return await self._one("SELECT * FROM teachers WHERE id = ?", teacher_id)

    async def teacher_by_teacher_secret(self, secret: str) -> dict | None:
        return await self._by_code("teacher_secret", secret)

    async def teacher_by_student_secret(self, secret: str) -> dict | None:
        return await self._by_code("student_secret", secret)

    async def _by_code(self, column: str, code: str) -> dict | None:
        """Найти по коду, не различая похожие символы (O и 0, I/L и 1) и регистр."""
        def norm(sql_or_value: str) -> str:
            return f"REPLACE(REPLACE(REPLACE({sql_or_value}, 'O', '0'), 'I', '1'), 'L', '1')"
        return await self._one(f"SELECT * FROM teachers WHERE {norm(column)} = {norm('?')}", code.strip().upper())

    async def all_teachers(self) -> list[dict]:
        return await self._all("SELECT * FROM teachers")

    async def update_teacher(self, teacher_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self._exec(f"UPDATE teachers SET {cols} WHERE id = ?", *fields.values(), teacher_id)

    async def regenerate_secret(self, teacher_id: int, kind: str) -> str:
        assert kind in ("teacher_secret", "student_secret")
        while True:
            value = new_secret()
            exists = await self._one(
                "SELECT id FROM teachers WHERE teacher_secret = ? OR student_secret = ?", value, value)
            if not exists:
                break
        await self.update_teacher(teacher_id, **{kind: value})
        return value

    async def delete_teacher(self, teacher_id: int) -> None:
        await self._exec("DELETE FROM teachers WHERE id = ?", teacher_id)

    # ------------------------------------------------------------ ученики

    async def add_student(self, teacher_id: int, name: str, price: int = 0) -> int:
        return await self._exec(
            "INSERT INTO students (teacher_id, name, price, created_at) VALUES (?, ?, ?, ?)",
            teacher_id, name.strip(), price, now_iso())

    async def get_student(self, teacher_id: int, student_id: int) -> dict | None:
        return await self._one(
            "SELECT * FROM students WHERE id = ? AND teacher_id = ?", student_id, teacher_id)

    async def students(self, teacher_id: int, include_inactive: bool = False) -> list[dict]:
        sql = "SELECT * FROM students WHERE teacher_id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        return await self._all(sql + " ORDER BY name COLLATE NOCASE", teacher_id)

    async def students_map(self, teacher_id: int) -> dict[int, dict]:
        return {s["id"]: s for s in await self.students(teacher_id, include_inactive=True)}

    async def find_student_by_name(self, teacher_id: int, name: str) -> dict | None:
        name = name.strip().lower()
        for s in await self.students(teacher_id):
            if s["name"].lower() == name:
                return s
        return None

    async def match_student(self, teacher_id: int, name: str) -> dict | None:
        """Найти ученика по имени, которое написал сам ученик или родитель.

        Сначала точное совпадение, затем — единственный ученик, в имени которого есть все написанные слова
        («Маша» → «Маша Иванова», «Иванова Маша» → «Маша Иванова»). «Маша Петрова» к «Маше» не привяжется:
        это может быть другой ребёнок.
        """
        exact = await self.find_student_by_name(teacher_id, name)
        if exact:
            return exact
        words = {w for w in name.lower().replace("ё", "е").split() if len(w) > 1}
        if not words:
            return None
        candidates = []
        for s in await self.students(teacher_id):
            their = {w for w in s["name"].lower().replace("ё", "е").split()}
            if words <= their:
                candidates.append(s)
        return candidates[0] if len(candidates) == 1 else None

    async def student_has_history(self, student_id: int) -> bool:
        for sql in ("SELECT 1 FROM weekly_lessons WHERE student_id = ?",
                    "SELECT 1 FROM single_lessons WHERE student_id = ?",
                    "SELECT 1 FROM payments WHERE student_id = ?",
                    "SELECT 1 FROM account_students WHERE student_id = ?"):
            if await self._one(sql, student_id):
                return True
        return False

    async def update_student(self, teacher_id: int, student_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self._exec(
            f"UPDATE students SET {cols} WHERE id = ? AND teacher_id = ?",
            *fields.values(), student_id, teacher_id)

    async def delete_student(self, teacher_id: int, student_id: int) -> None:
        await self._detach_student(teacher_id, student_id)
        await self._exec("DELETE FROM students WHERE id = ? AND teacher_id = ?", student_id, teacher_id)

    async def archive_student(self, teacher_id: int, student_id: int, ends: dict[int, str],
                              today: str, now_time: str) -> None:
        """Ученик скрывается, будущие занятия снимаются, история и доход сохраняются.

        ends — для каждого регулярного занятия ученика последний день, когда оно ещё было.
        """
        await self.update_student(teacher_id, student_id, active=0)
        for lesson_id, last_day in ends.items():
            await self.end_weekly(teacher_id, lesson_id, last_day)
        await self._exec(
            "DELETE FROM single_lessons WHERE student_id = ? AND teacher_id = ? "
            "AND (date > ? OR (date = ? AND time > ?))",
            student_id, teacher_id, today, today, now_time)
        await self._detach_student(teacher_id, student_id)

    async def _detach_student(self, teacher_id: int, student_id: int) -> None:
        """Отвязать ученика от всех аккаунтов; аккаунты без детей удаляются."""
        if not await self._one("SELECT 1 FROM students WHERE id = ? AND teacher_id = ?", student_id, teacher_id):
            return  # чужой ученик
        await self._exec("DELETE FROM account_students WHERE student_id = ?", student_id)
        await self._exec(
            "DELETE FROM accounts WHERE teacher_id = ? AND role IN ('student', 'parent') "
            "AND tg_id NOT IN (SELECT tg_id FROM account_students)", teacher_id)
        await self._exec(
            "UPDATE accounts SET student_id = (SELECT MIN(student_id) FROM account_students x "
            "WHERE x.tg_id = accounts.tg_id) WHERE student_id = ?", student_id)

    # ------------------------------------------------------------ занятия

    async def add_weekly(self, teacher_id: int, student_id: int, weekday: int, time: str,
                         duration: int, price: int | None, valid_from: str) -> int:
        return await self._exec(
            "INSERT INTO weekly_lessons (teacher_id, student_id, weekday, time, duration, price, valid_from) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            teacher_id, student_id, weekday, time, duration, price, valid_from)

    async def weekly(self, teacher_id: int) -> list[dict]:
        return await self._all("SELECT * FROM weekly_lessons WHERE teacher_id = ?", teacher_id)

    async def current_weekly(self, teacher_id: int, today: str) -> list[dict]:
        """Регулярные занятия, которые действуют сейчас или начнутся позже."""
        return await self._all(
            "SELECT * FROM weekly_lessons WHERE teacher_id = ? AND (valid_to IS NULL OR valid_to >= ?) "
            "AND (valid_to IS NULL OR valid_to >= valid_from) ORDER BY weekday, time",
            teacher_id, today)

    async def get_weekly(self, teacher_id: int, lesson_id: int) -> dict | None:
        return await self._one(
            "SELECT * FROM weekly_lessons WHERE id = ? AND teacher_id = ?", lesson_id, teacher_id)

    async def end_weekly(self, teacher_id: int, lesson_id: int, last_day: str) -> None:
        """Завершить регулярное занятие после last_day: прошлые остаются в истории дохода."""
        lesson = await self.get_weekly(teacher_id, lesson_id)
        if not lesson:
            return
        if lesson["valid_from"] > last_day:
            await self._exec("DELETE FROM weekly_lessons WHERE id = ?", lesson_id)
        elif not lesson["valid_to"] or lesson["valid_to"] > last_day:
            await self._exec("UPDATE weekly_lessons SET valid_to = ? WHERE id = ?", last_day, lesson_id)

    async def add_single(self, teacher_id: int, student_id: int, day: str, time: str,
                         duration: int, price: int | None) -> int:
        return await self._exec(
            "INSERT INTO single_lessons (teacher_id, student_id, date, time, duration, price) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            teacher_id, student_id, day, time, duration, price)

    async def singles(self, teacher_id: int, start: str | None = None, end: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM single_lessons WHERE teacher_id = ?", [teacher_id]
        if start:
            sql += " AND date >= ?"
            args.append(start)
        if end:
            sql += " AND date <= ?"
            args.append(end)
        return await self._all(sql + " ORDER BY date, time", *args)

    async def delete_single(self, teacher_id: int, lesson_id: int) -> None:
        await self._exec("DELETE FROM single_lessons WHERE id = ? AND teacher_id = ?", lesson_id, teacher_id)

    async def delete_future_singles(self, teacher_id: int, today: str, now_time: str) -> int:
        """Удалить разовые занятия, которые ещё не начались."""
        cond = "teacher_id = ? AND (date > ? OR (date = ? AND time > ?))"
        rows = await self._all(f"SELECT id FROM single_lessons WHERE {cond}", teacher_id, today, today, now_time)
        await self._exec(f"DELETE FROM single_lessons WHERE {cond}", teacher_id, today, today, now_time)
        return len(rows)

    async def cancel_on(self, lesson_id: int, day: str, moved_to: int | None = None) -> None:
        await self._exec("INSERT OR IGNORE INTO cancellations (lesson_id, date, moved_to) VALUES (?, ?, ?)",
                         lesson_id, day, moved_to)

    async def moved_to(self, lesson_id: int, day: str) -> int | None:
        row = await self._one("SELECT moved_to FROM cancellations WHERE lesson_id = ? AND date = ?", lesson_id, day)
        return row["moved_to"] if row else None

    async def future_cancellations(self, teacher_id: int, from_day: str) -> list[dict]:
        return await self._all(
            "SELECT c.lesson_id, c.date, c.moved_to FROM cancellations c "
            "JOIN weekly_lessons w ON w.id = c.lesson_id WHERE w.teacher_id = ? AND c.date >= ?",
            teacher_id, from_day)

    async def uncancel_on(self, lesson_id: int, day: str) -> None:
        await self._exec("DELETE FROM cancellations WHERE lesson_id = ? AND date = ?", lesson_id, day)

    async def cancelled(self, teacher_id: int) -> set[tuple[int, str]]:
        rows = await self._all(
            "SELECT c.lesson_id, c.date FROM cancellations c "
            "JOIN weekly_lessons w ON w.id = c.lesson_id WHERE w.teacher_id = ?", teacher_id)
        return {(r["lesson_id"], r["date"]) for r in rows}

    async def schedule_data(self, teacher_id: int):
        """Всё, что нужно для расчёта занятий на любые даты."""
        return (
            await self.weekly(teacher_id),
            await self.singles(teacher_id),
            await self.cancelled(teacher_id),
            await self.students_map(teacher_id),
        )

    # ------------------------------------------------------------ оплаты учеников

    async def add_payment(self, teacher_id: int, student_id: int | None, amount: int, day: str, note: str = "") -> int:
        return await self._exec(
            "INSERT INTO payments (teacher_id, student_id, amount, date, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            teacher_id, student_id, amount, day, note, now_iso())

    async def payments(self, teacher_id: int, start: str | None = None, end: str | None = None,
                       student_id: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM payments WHERE teacher_id = ?", [teacher_id]
        if start:
            sql += " AND date >= ?"
            args.append(start)
        if end:
            sql += " AND date <= ?"
            args.append(end)
        if student_id is not None:
            sql += " AND student_id = ?"
            args.append(student_id)
        return await self._all(sql + " ORDER BY date DESC, id DESC", *args)

    async def delete_payment(self, teacher_id: int, payment_id: int) -> None:
        await self._exec("DELETE FROM payments WHERE id = ? AND teacher_id = ?", payment_id, teacher_id)

    # ------------------------------------------------------------ подписка

    async def log_sub_payment(self, teacher_id: int, amount: int, days: int, source: str, charge_id: str = "") -> None:
        await self._exec(
            "INSERT INTO sub_payments (teacher_id, amount, days, source, charge_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            teacher_id, amount, days, source, charge_id, now_iso())

    # ------------------------------------------------------------ мемы

    async def add_meme(self, teacher_id: int, kind: str, file_id: str) -> int:
        return await self._exec(
            "INSERT INTO memes (teacher_id, kind, file_id, created_at) VALUES (?, ?, ?, ?)",
            teacher_id, kind, file_id, now_iso())

    async def memes(self, teacher_id: int) -> list[dict]:
        return await self._all("SELECT * FROM memes WHERE teacher_id = ? ORDER BY id", teacher_id)

    async def random_meme(self, teacher_id: int) -> dict | None:
        return await self._one(
            "SELECT * FROM memes WHERE teacher_id = ? ORDER BY RANDOM() LIMIT 1", teacher_id)

    async def delete_meme(self, teacher_id: int, meme_id: int) -> None:
        await self._exec("DELETE FROM memes WHERE id = ? AND teacher_id = ?", meme_id, teacher_id)

    async def delete_all_memes(self, teacher_id: int) -> None:
        await self._exec("DELETE FROM memes WHERE teacher_id = ?", teacher_id)

    # ------------------------------------------------------------ отчёты об уроках

    async def add_report(self, teacher_id: int, student_id: int, day: str, time: str,
                         ratings: dict[str, int], comment: str) -> int:
        return await self._exec(
            "INSERT INTO lesson_reports (teacher_id, student_id, date, time, effort, productivity, "
            "understanding, comment, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            teacher_id, student_id, day, time, ratings.get("effort", 0), ratings.get("productivity", 0),
            ratings.get("understanding", 0), comment, now_iso())

    async def reports(self, teacher_id: int, student_id: int, limit: int = 5) -> list[dict]:
        return await self._all(
            "SELECT * FROM lesson_reports WHERE teacher_id = ? AND student_id = ? "
            "ORDER BY date DESC, time DESC LIMIT ?", teacher_id, student_id, limit)

    # ------------------------------------------------------------ домашние задания

    async def add_homework(self, teacher_id: int, student_id: int, due: str = "") -> int:
        return await self._exec(
            "INSERT INTO homework (teacher_id, student_id, due, created_at) VALUES (?, ?, ?, ?)",
            teacher_id, student_id, due, now_iso())

    async def get_homework(self, homework_id: int) -> dict | None:
        return await self._one("SELECT * FROM homework WHERE id = ?", homework_id)

    async def open_homework(self, student_id: int) -> dict | None:
        """Последнее задание, которое ученик ещё не сдал (или которое просили переделать)."""
        return await self._one(
            "SELECT * FROM homework WHERE student_id = ? AND status IN ('sent', 'redo') "
            "ORDER BY id DESC LIMIT 1", student_id)

    async def last_homework(self, student_id: int) -> dict | None:
        return await self._one("SELECT * FROM homework WHERE student_id = ? ORDER BY id DESC LIMIT 1", student_id)

    async def set_homework_status(self, homework_id: int, status: str) -> None:
        await self._exec("UPDATE homework SET status = ? WHERE id = ?", status, homework_id)

    # ------------------------------------------------------------ промокоды

    async def create_promo(self, code: str, days: int, max_uses: int | None) -> bool:
        if await self.get_promo(code):
            return False
        await self._exec("INSERT INTO promo_codes (code, days, max_uses, created_at) VALUES (?, ?, ?, ?)",
                         code.upper(), days, max_uses, now_iso())
        return True

    async def get_promo(self, code: str) -> dict | None:
        return await self._one("SELECT * FROM promo_codes WHERE code = ?", code.strip().upper())

    async def promos(self) -> list[dict]:
        return await self._all("SELECT * FROM promo_codes ORDER BY active DESC, created_at DESC")

    async def set_promo_active(self, code: str, active: bool) -> None:
        await self._exec("UPDATE promo_codes SET active = ? WHERE code = ?", 1 if active else 0, code.upper())

    async def promo_used_by(self, code: str, teacher_id: int) -> bool:
        return bool(await self._one("SELECT 1 FROM promo_uses WHERE code = ? AND teacher_id = ?",
                                    code.upper(), teacher_id))

    async def reserve_promo(self, code: str, teacher_id: int) -> bool:
        """Засчитать использование промокода, если лимит ещё не исчерпан (одним запросом, без гонок)."""
        changed = await self._changed(
            "UPDATE promo_codes SET used = used + 1 WHERE code = ? AND active = 1 "
            "AND (max_uses IS NULL OR used < max_uses)", code.upper())
        if changed:
            await self._exec(
                "INSERT OR IGNORE INTO promo_uses (code, teacher_id, created_at) VALUES (?, ?, ?)",
                code.upper(), teacher_id, now_iso())
        return bool(changed)

    async def get_setting(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM settings WHERE key = ?", key)
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._exec("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", key, value)

    async def add_perk(self, tg_id: int, perk: str) -> bool:
        """True, если человек получает это впервые."""
        return bool(await self._changed("INSERT OR IGNORE INTO perks (tg_id, perk) VALUES (?, ?)", tg_id, perk))

    async def has_perk(self, tg_id: int, perk: str) -> bool:
        return bool(await self._one("SELECT 1 FROM perks WHERE tg_id = ? AND perk = ?", tg_id, perk))

    # ------------------------------------------------------------ поддержка и уведомления

    async def save_support_link(self, admin_chat_id: int, admin_msg_id: int, user_tg_id: int) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO support_links (admin_chat_id, admin_msg_id, user_tg_id) VALUES (?, ?, ?)",
            admin_chat_id, admin_msg_id, user_tg_id)

    async def support_user(self, admin_chat_id: int, admin_msg_id: int) -> int | None:
        row = await self._one(
            "SELECT user_tg_id FROM support_links WHERE admin_chat_id = ? AND admin_msg_id = ?",
            admin_chat_id, admin_msg_id)
        return row["user_tg_id"] if row else None

    async def mark_sent(self, key: str) -> bool:
        """True, если уведомление ещё не отправлялось (и теперь помечено)."""
        return await self._changed(
            "INSERT OR IGNORE INTO sent_notifications (key, created_at) VALUES (?, ?)", key, now_iso()) > 0

    async def stats(self) -> dict:
        return {
            "teachers": (await self._one("SELECT COUNT(*) AS n FROM teachers"))["n"],
            "students": (await self._one("SELECT COUNT(*) AS n FROM students WHERE active = 1"))["n"],
            "accounts": (await self._one("SELECT COUNT(*) AS n FROM accounts"))["n"],
            "sub_payments": (await self._one("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS s FROM sub_payments")),
        }
