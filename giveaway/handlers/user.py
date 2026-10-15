import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InputMediaPhoto,
    Message,
    ReplyKeyboardRemove,
)

from .. import captcha, texts
from ..captcha import CaptchaRateLimited, CaptchaStore, Challenge
from ..checker import find_missing
from ..commands import set_admin_commands
from ..config import Config
from ..database import Database
from ..keyboards import CHECK_CB, CaptchaCb, captcha_kb, check_kb, sponsors_kb, webapp_kb
from ..utils import show

router = Router(name="user")
router.message.filter(F.chat.type == ChatType.PRIVATE)

CHECK_COOLDOWN = 2.0  # секунд между нажатиями «Проверить подписку»
_last_check: dict[int, float] = {}

TOO_MANY_CAPTCHAS = "Слишком много попыток. Подожди пару минут и нажми «✅ Проверить подписку» снова."
OUTDATED_CAPTCHA = "Эта картинка устарела. Нажми «✅ Проверить подписку», чтобы получить новую."


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    if await state.get_state() is not None:
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())

    await db.touch_user(message.from_user.id)
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
async def check_subscriptions(
    cb: CallbackQuery, bot: Bot, db: Database, config: Config, chat_captcha: CaptchaStore
) -> None:
    user = cb.from_user
    await db.touch_user(user.id)

    now = time.monotonic()
    if now - _last_check.get(user.id, 0.0) < CHECK_COOLDOWN:
        await cb.answer("Подожди пару секунд ⏳")
        return
    _last_check[user.id] = now

    participant = await db.get_participant(user.id)
    sponsors = await db.get_sponsors()
    if participant is not None:
        # Номер уже есть; если после рассылки добавились спонсоры — подсказываем, на кого подписаться.
        missing = await find_missing(bot, db, sponsors, user.id)
        if missing:
            await show(cb, texts.missing(missing, participant.number), sponsors_kb(missing, config.webapp_url))
            await cb.answer("Есть новые условия")
        else:
            await show(cb, texts.already_participant(participant.number, config.giveaway_name))
            await cb.answer("✅ Всё выполнено")
        return

    if not sponsors:
        await show(cb, texts.no_sponsors(config.giveaway_name))
        await cb.answer()
        return

    missing = await find_missing(bot, db, sponsors, user.id)
    if missing:
        await show(cb, texts.missing(missing), sponsors_kb(missing, config.webapp_url))
        await cb.answer("Ещё не все условия выполнены")
        return

    # Подписки проверяли в чате — капча тоже в чате: написать слово с картинки.
    if await db.get_flag(captcha.SETTING) and not chat_captcha.recently_passed(user.id):
        old = chat_captcha.current(user.id)
        try:
            issued = await chat_captcha.issue(user.id)
        except CaptchaRateLimited:
            await cb.answer(TOO_MANY_CAPTCHAS, show_alert=True)
            return
        await _retire(bot, user.id, old)
        await show(cb, texts.captcha_needed())
        await _send_captcha(bot, user.id, issued)
        await cb.answer()
        return

    participant, _ = await db.register_participant(user.id, user.username, user.full_name)
    await show(cb, texts.success(participant.number, config.giveaway_name))
    await cb.answer("🎉 Номер получен!")


# ---------- Капча в чате: слово с картинки ----------


async def _send_captcha(bot: Bot, chat_id: int, issued: tuple[Challenge, bytes], note: str = "") -> None:
    challenge, image = issued
    sent = await bot.send_photo(
        chat_id,
        BufferedInputFile(image, filename="captcha.jpg"),
        caption=texts.captcha_caption(note),
        reply_markup=captcha_kb(challenge.id),
    )
    challenge.message_id = sent.message_id


async def _retire(bot: Bot, chat_id: int, challenge: Challenge | None) -> None:
    """Убирает кнопку «Другое слово» под картинкой, которая больше не действует."""
    if challenge is not None and challenge.message_id is not None:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=challenge.message_id, reply_markup=None)
        except TelegramAPIError:
            pass


@router.callback_query(CaptchaCb.filter())
async def captcha_new_word(
    cb: CallbackQuery, callback_data: CaptchaCb, db: Database, chat_captcha: CaptchaStore
) -> None:
    """«🔄 Другое слово» — новая картинка в том же сообщении."""
    user = cb.from_user
    if await db.get_participant(user.id) is not None:  # номер уже выдан, например в приложении
        await cb.answer("Ты уже участвуешь — номер выдан ✅", show_alert=True)
        return
    if callback_data.action != "new" or chat_captcha.get(user.id, callback_data.id) is None:
        await cb.answer(OUTDATED_CAPTCHA, show_alert=True)
        return
    try:
        challenge, image = await chat_captcha.issue(user.id)
    except CaptchaRateLimited:
        await cb.answer(TOO_MANY_CAPTCHAS, show_alert=True)
        return
    media = InputMediaPhoto(media=BufferedInputFile(image, filename="captcha.jpg"), caption=texts.captcha_caption())
    if isinstance(cb.message, Message):
        try:
            await cb.message.edit_media(media, reply_markup=captcha_kb(challenge.id))
            challenge.message_id = cb.message.message_id
            await cb.answer()
            return
        except TelegramBadRequest:
            pass
    await _send_captcha(cb.bot, user.id, (challenge, image))
    await cb.answer()


def _awaiting_captcha(message: Message, chat_captcha: CaptchaStore) -> bool:
    """Текстовое сообщение от того, кому сейчас показана капча (команды — мимо)."""
    return (
        message.from_user is not None
        and chat_captcha.current(message.from_user.id) is not None
        and not (message.text or "").startswith("/")
    )


@router.message(F.text, _awaiting_captcha)
async def captcha_answer(message: Message, bot: Bot, db: Database, config: Config, chat_captcha: CaptchaStore) -> None:
    user = message.from_user
    challenge = chat_captcha.current(user.id)
    await _retire(bot, message.chat.id, challenge)

    participant = await db.get_participant(user.id)
    if participant is not None:  # номер уже выдан, например в приложении
        chat_captcha.discard(user.id)
        await message.answer(texts.already_participant(participant.number, config.giveaway_name))
        return

    expired = challenge.expired
    if not chat_captcha.verify(user.id, message.text):
        try:
            issued = await chat_captcha.issue(user.id)
        except CaptchaRateLimited:
            await message.answer(TOO_MANY_CAPTCHAS, reply_markup=check_kb())
            return
        note = "⏱ Время вышло — вот новое слово." if expired else "❌ Неверно — вот новое слово."
        await _send_captcha(bot, message.chat.id, issued, note)
        return

    await message.answer(texts.captcha_passed())
    # Пока отвечал на капчу, мог отписаться — проверяем условия ещё раз.
    sponsors = await db.get_sponsors()
    if not sponsors:
        await message.answer(texts.no_sponsors(config.giveaway_name))
        return
    missing = await find_missing(bot, db, sponsors, user.id)
    if missing:
        await message.answer(texts.missing(missing), reply_markup=sponsors_kb(missing, config.webapp_url))
        return
    participant, _ = await db.register_participant(user.id, user.username, user.full_name)
    await message.answer(texts.success(participant.number, config.giveaway_name))


@router.callback_query(F.data.startswith("pz:"))
async def old_puzzle_pressed(cb: CallbackQuery) -> None:
    """Кнопки «собери картинку» из прошлых версий бота."""
    await cb.answer(OUTDATED_CAPTCHA, show_alert=True)


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Нажми /start, чтобы участвовать в розыгрыше 🎁")
