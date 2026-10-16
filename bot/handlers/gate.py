"""Если подписка не активна — разделы расписания, учеников и дохода закрыты."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import subscription
from ..config import Config
from ..filters import ActiveSub
from ..keyboards import B
from ..texts import need_subscription_text

router = Router(name="gate")

PAID_PREFIXES = ("sch:", "stu:", "inc:", "rel:", "rln:", "hw:", "rep:")


# Для активной подписки сюда доходят только устаревшие кнопки — их обрабатывает fallback.
@router.message(F.text.in_({B.SCHEDULE, B.STUDENTS, B.INCOME}), ~ActiveSub())
async def locked(message: Message, state: FSMContext, teacher: dict, config: Config):
    await state.clear()
    st = subscription.status(teacher)
    await message.answer(need_subscription_text(),
                         reply_markup=kb.subscription(teacher, st.active, config.price_rub, config.trial_days))


@router.callback_query(F.data.startswith(PAID_PREFIXES), ~ActiveSub())
async def locked_cb(call: CallbackQuery, teacher: dict, config: Config):
    await call.answer("Подписка не активна", show_alert=False)
    st = subscription.status(teacher)
    await call.message.answer(need_subscription_text(),
                              reply_markup=kb.subscription(teacher, st.active, config.price_rub, config.trial_days))
