from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from .db import Database


class ContextMiddleware(BaseMiddleware):
    """Подставляет в обработчики account, teacher и student текущего пользователя."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        db: Database = data["db"]
        account = teacher = student = None
        user = data.get("event_from_user")
        if user:
            account = await db.get_account(user.id)
            if account:
                teacher = await db.get_teacher(account["teacher_id"])
                if teacher is None:
                    await db.unlink_account(user.id)
                    account = None
                elif account["student_id"]:
                    student = await db.get_student(teacher["id"], account["student_id"])
        data.update(account=account, teacher=teacher, student=student)
        return await handler(event, data)
