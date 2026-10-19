"""Переносит данные бота из файла на компьютере в облачную базу Turso.

Запуск:
    python deploy/to_turso.py data/bot.sqlite3 libsql://ИМЯ-БАЗЫ.turso.io ТОКЕН

Таблицы в облаке создаются сами, старые строки с такими же номерами заменяются,
поэтому скрипт можно запускать повторно.
"""
import asyncio
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.db import Database  # noqa: E402


async def main(path: str, url: str, token: str) -> None:
    os.environ["TURSO_AUTH_TOKEN"] = token
    cloud = Database(url)
    await cloud.connect()  # создаёт таблицы, если их ещё нет

    local = sqlite3.connect(path)
    local.row_factory = sqlite3.Row
    tables = [r[0] for r in local.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        rows = local.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"{table}: пусто")
            continue
        columns = rows[0].keys()
        places = ", ".join("?" for _ in columns)
        sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({places})"
        for row in rows:
            await cloud._exec(sql, *[row[c] for c in columns])
        print(f"{table}: перенесено {len(rows)}")
    local.close()
    await cloud.close()
    print("Готово. Данные в облаке.")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
