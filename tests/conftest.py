"""Фейковый Telegram: бот работает целиком, но запросы к API перехватываются."""
import itertools
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, GetMe, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import Config
from bot.db import Database
from bot.main import build_dispatcher

_ids = itertools.count(1000)
_DP = None


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="Bot", username="test_tutor_bot")
        if isinstance(method, AnswerCallbackQuery):
            return True
        name = type(method).__name__
        if name.startswith(("Send", "Edit", "Copy")):
            chat_id = getattr(method, "chat_id", None) or 0
            if name == "CopyMessage":
                from aiogram.types import MessageId
                return MessageId(message_id=next(_ids))
            return Message(message_id=next(_ids), date=datetime.now(timezone.utc),
                           chat=Chat(id=chat_id, type="private"), text=getattr(method, "text", None))
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):
        yield b""


class Harness:
    def __init__(self, bot, dp, session, db, config):
        self.bot, self.dp, self.session, self.db, self.config = bot, dp, session, db, config
        self._upd = itertools.count(1)

    def user(self, uid: int, name: str = "User"):
        return User(id=uid, is_bot=False, first_name=name, username=f"user{uid}")

    async def send(self, uid: int, text: str, reply_to: int | None = None, **extra):
        user = self.user(uid)
        msg = dict(message_id=next(_ids), date=datetime.now(timezone.utc),
                   chat=Chat(id=uid, type="private"), from_user=user, text=text, **extra)
        if reply_to:
            msg["reply_to_message"] = Message(message_id=reply_to, date=datetime.now(timezone.utc),
                                              chat=Chat(id=uid, type="private"), text="x")
        start = len(self.session.calls)
        await self.dp.feed_update(self.bot, Update(update_id=next(self._upd), message=Message(**msg)))
        return self.session.calls[start:]

    async def click(self, uid: int, data: str):
        user = self.user(uid)
        msg = Message(message_id=next(_ids), date=datetime.now(timezone.utc),
                      chat=Chat(id=uid, type="private"), from_user=User(id=1, is_bot=True, first_name="Bot"), text="menu")
        cq = CallbackQuery(id=str(next(_ids)), from_user=user, chat_instance="ci", message=msg, data=data)
        start = len(self.session.calls)
        await self.dp.feed_update(self.bot, Update(update_id=next(self._upd), callback_query=cq))
        return self.session.calls[start:]

    @staticmethod
    def texts(calls) -> str:
        out = []
        for c in calls:
            t = getattr(c, "text", None) or getattr(c, "caption", None)
            if t:
                out.append(t)
        return "\n---\n".join(out)

    @staticmethod
    def buttons(calls) -> list[str]:
        result = []
        for c in calls:
            markup = getattr(c, "reply_markup", None)
            if markup is not None and hasattr(markup, "inline_keyboard"):
                for row in markup.inline_keyboard:
                    for b in row:
                        result.append(f"{b.text}|{b.callback_data}")
        return result


@pytest_asyncio.fixture
async def h():
    db = Database(":memory:")
    await db.connect()
    config = Config(bot_token="42:TEST", admin_ids={999})
    session = FakeSession()
    bot = Bot("42:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    global _DP
    if _DP is None:
        _DP = build_dispatcher(db, config)
    dp = _DP
    dp.workflow_data.update(db=db, config=config)
    dp.fsm.storage = MemoryStorage()
    yield Harness(bot, dp, session, db, config)
    await db.close()
