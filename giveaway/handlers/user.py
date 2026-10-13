import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from .. import texts
from ..checker import find_missing
from ..commands import set_admin_commands
from ..config import Config
from ..database import Database
from ..keyboards import CHECK_CB, sponsors_kb, webapp_kb
from ..utils import show

router = Router(name="user")
router.message.filter(F.chat.type == ChatType.PRIVATE)

CHECK_COOLDOWN = 2.0  # секунд между нажатиями «Проверить подписку»
_last_check: dict[int, float] = {}


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    if await state.get_state() is not None:
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())

    participant = await db.get_participant(message.from_user.id)
    sponsors = await db.get_sponsors()
    if participant is not None:
        await message.answer(
            texts.already_participant(participant.number, config.giveaway_name),
            reply_markup=webapp_kb(config.webapp_url),
        )
    elif not sponsors:
        await message.answer(texts.no_sponsors(config.giveaway_name))
    else:
        await message.answer(
            texts.welcome(message.from_user.first_name, config.giveaway_name, sponsors),
            reply_markup=sponsors_kb(sponsors, config.webapp_url),
        )

    if message.from_user.id in config.admin_ids:
        await set_admin_commands(bot, message.from_user.id)
        await message.answer("⚙️ Вы администратор. Панель управления: /admin")


@router.callback_query(F.data == CHECK_CB)
async def check_subscriptions(cb: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    user = cb.from_user

    participant = await db.get_participant(user.id)
    if participant is not None:
        await show(cb, texts.already_participant(participant.number, config.giveaway_name))
        await cb.answer()
        return

    now = time.monotonic()
    if now - _last_check.get(user.id, 0.0) < CHECK_COOLDOWN:
        await cb.answer("Подожди пару секунд ⏳")
        return
    _last_check[user.id] = now

    sponsors = await db.get_sponsors()
    if not sponsors:
        await show(cb, texts.no_sponsors(config.giveaway_name))
        await cb.answer()
        return

    missing = await find_missing(bot, db, sponsors, user.id)
    if missing:
        await show(cb, texts.missing(missing), sponsors_kb(missing, config.webapp_url))
        await cb.answer("Ещё не все условия выполнены")
        return

    participant, _ = await db.register_participant(user.id, user.username, user.full_name)
    await show(cb, texts.success(participant.number, config.giveaway_name))
    await cb.answer("🎉 Номер получен!")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Нажми /start, чтобы участвовать в розыгрыше 🎁")
