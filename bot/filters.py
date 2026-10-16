from aiogram import F
from aiogram.filters import BaseFilter

from . import subscription
from .keyboards import ALL_MENU_BUTTONS

# Текстовый ввод, который не является нажатием кнопки главного меню.
TEXT_INPUT = F.text & ~F.text.in_(ALL_MENU_BUTTONS) & ~F.text.startswith("/")


class Role(BaseFilter):
    def __init__(self, *roles: str):
        self.roles = roles

    async def __call__(self, event, account: dict | None = None) -> bool:
        return account is not None and account["role"] in self.roles


class NoAccount(BaseFilter):
    async def __call__(self, event, account: dict | None = None) -> bool:
        return account is None


class ActiveSub(BaseFilter):
    async def __call__(self, event, teacher: dict | None = None) -> bool:
        return teacher is not None and subscription.status(teacher).active


class IsAdmin(BaseFilter):
    async def __call__(self, event, config=None, event_from_user=None) -> bool:
        return bool(config and event_from_user and event_from_user.id in config.admin_ids)
