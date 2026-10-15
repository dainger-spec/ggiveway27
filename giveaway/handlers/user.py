import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
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
from ..captcha import CaptchaRateLimited, ChatCaptchaStore, ChatChallenge
from ..checker import find_missing
from ..commands import set_admin_commands
from ..config import Config
from ..database import Database
from ..keyboards import CHECK_CB, CaptchaCb, captcha_kb, sponsors_kb, webapp_kb
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
    cb: CallbackQuery, bot: Bot, db: Database, config: Config, chat_captcha: ChatCaptchaStore
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

    # Подписки проверяли в чате — капча тоже в чате: нажать на число с картинки.
    if await db.get_flag(captcha.SETTING) and not chat_captcha.recently_passed(user.id):
        if issued := await _new_captcha(cb, chat_captcha):
            await show(cb, texts.captcha_needed())
            await _send_captcha(cb, issued, edit=False)
            await cb.answer()
        return

    participant, _ = await db.register_participant(user.id, user.username, user.full_name)
    await show(cb, texts.success(participant.number, config.giveaway_name))
    await cb.answer("🎉 Номер получен!")


# ---------- Капча в чате: «какое число на картинке» ----------


async def _new_captcha(cb: CallbackQuery, store: ChatCaptchaStore) -> tuple[ChatChallenge, bytes] | None:
    """Новая капча; если попыток слишком много — говорит об этом и возвращает None."""
    try:
        return await store.issue(cb.from_user.id)
    except CaptchaRateLimited:
        await cb.answer(TOO_MANY_CAPTCHAS, show_alert=True)
        return None


async def _send_captcha(cb: CallbackQuery, issued: tuple[ChatChallenge, bytes], note: str = "", *, edit: bool) -> None:
    """Картинка с числами: в том же сообщении (edit) или новым."""
    challenge, image = issued
    photo = BufferedInputFile(image, filename="captcha.jpg")
    caption, keyboard = texts.captcha_caption(note), captcha_kb(challenge.id, challenge.options)
    if edit and isinstance(cb.message, Message):
        try:
            await cb.message.edit_media(InputMediaPhoto(media=photo, caption=caption), reply_markup=keyboard)
            return
        except TelegramBadRequest:
            pass
    await cb.bot.send_photo(cb.from_user.id, photo, caption=caption, reply_markup=keyboard)


@router.callback_query(CaptchaCb.filter())
async def captcha_pressed(
    cb: CallbackQuery,
    callback_data: CaptchaCb,
    bot: Bot,
    db: Database,
    config: Config,
    chat_captcha: ChatCaptchaStore,
) -> None:
    user = cb.from_user
    if await db.get_participant(user.id) is not None:  # номер уже выдан, например в приложении
        await cb.answer("Ты уже участвуешь — номер выдан ✅", show_alert=True)
        return
    challenge = chat_captcha.get(user.id, callback_data.id)
    if challenge is None:
        await cb.answer(OUTDATED_CAPTCHA, show_alert=True)
        return
    if callback_data.action == "new" or challenge.expired:
        if issued := await _new_captcha(cb, chat_captcha):
            note = "" if callback_data.action == "new" else "⏱ Время вышло — вот новая картинка."
            await _send_captcha(cb, issued, note, edit=True)
            await cb.answer()
        return
    if not chat_captcha.check(user.id, challenge.id, callback_data.option):
        if issued := await _new_captcha(cb, chat_captcha):
            await _send_captcha(cb, issued, "❌ Не то число — вот новая картинка.", edit=True)
            await cb.answer("Не то число, попробуй ещё раз")
        return

    if isinstance(cb.message, Message):
        try:
            await cb.message.edit_caption(caption=texts.captcha_passed(), reply_markup=None)
        except TelegramBadRequest:
            pass
    # Пока отвечал на капчу, мог отписаться — проверяем условия ещё раз.
    sponsors = await db.get_sponsors()
    if not sponsors:
        await bot.send_message(user.id, texts.no_sponsors(config.giveaway_name))
        await cb.answer()
        return
    missing = await find_missing(bot, db, sponsors, user.id)
    if missing:
        await bot.send_message(user.id, texts.missing(missing), reply_markup=sponsors_kb(missing, config.webapp_url))
        await cb.answer("Ещё не все условия выполнены")
        return
    participant, _ = await db.register_participant(user.id, user.username, user.full_name)
    await bot.send_message(user.id, texts.success(participant.number, config.giveaway_name))
    await cb.answer("🎉 Номер получен!")


@router.callback_query(F.data.startswith("pz:"))
async def old_puzzle_pressed(cb: CallbackQuery) -> None:
    """Кнопки «собери картинку» из прошлой версии бота."""
    await cb.answer(OUTDATED_CAPTCHA, show_alert=True)


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Нажми /start, чтобы участвовать в розыгрыше 🎁")
