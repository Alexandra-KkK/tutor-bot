"""Состояние диалогов (FSM) в базе данных.

В облаке программа просыпается на каждое сообщение и тут же засыпает, поэтому помнить,
что человек сейчас вводит, в памяти нельзя: следующее сообщение попадёт в другой процесс.
"""
import json
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from .db import Database, now_iso


def row_key(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id or 0}:{key.destiny}"


class DbStorage(BaseStorage):
    def __init__(self, db: Database):
        self.db = db

    async def _row(self, key: StorageKey) -> dict | None:
        return await self.db._one("SELECT state, data FROM fsm WHERE key = ?", row_key(key))

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await self.db._exec(
            "INSERT INTO fsm (key, state, data, updated_at) VALUES (?, ?, '{}', ?) "
            "ON CONFLICT(key) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
            row_key(key), value, now_iso())

    async def get_state(self, key: StorageKey) -> str | None:
        row = await self._row(key)
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self.db._exec(
            "INSERT INTO fsm (key, state, data, updated_at) VALUES (?, NULL, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            row_key(key), json.dumps(dict(data), ensure_ascii=False), now_iso())

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await self._row(key)
        return json.loads(row["data"]) if row and row["data"] else {}

    async def get_value(self, storage_key: StorageKey, dict_key: str, default: Any | None = None) -> Any | None:
        return (await self.get_data(storage_key)).get(dict_key, default)

    async def update_data(self, key: StorageKey, data: Mapping[str, Any]) -> dict[str, Any]:
        current = await self.get_data(key)
        current.update(data)
        await self.set_data(key, current)
        return current

    async def close(self) -> None:
        pass
