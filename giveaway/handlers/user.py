import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    ReplyKeyboardRemove,
)

from .. import captcha, texts
from ..captcha import CaptchaRateLimited, ChatCaptchaStore, ChatPuzzle
from ..checker import find_missing
from ..commands import set_admin_commands
from ..config import Config
from ..database import Database
from ..keyboards import CHECK_CB, PuzzleCb, puzzle_kb, sponsors_kb, webapp_kb
from ..utils import show

router = Router(name="user")
router.message.filter(F.chat.type == ChatType.PRIVATE)

CHECK_COOLDOWN = 2.0  # секунд между нажатиями «Проверить подписку»
_last_check: dict[int, float] = {}

TOO_MANY_PUZZLES = "Слишком много попыток. Подожди пару минут и нажми «✅ Проверить подписку» снова."


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

    # Подписки проверяли в чате — капча тоже в чате: собрать картинку кнопками под фото.
    if await db.get_flag(captcha.SETTING) and not chat_captcha.recently_passed(user.id):
        try:
            puzzle = await chat_captcha.issue(user.id)
        except CaptchaRateLimited:
            await cb.answer(TOO_MANY_PUZZLES, show_alert=True)
            return
        await show(cb, texts.captcha_needed())
        await bot.send_photo(
            user.id, await _photo(puzzle), caption=texts.puzzle_caption(), reply_markup=puzzle_kb(puzzle.id, None)
        )
        await cb.answer("Остался последний шаг")
        return

    participant, _ = await db.register_participant(user.id, user.username, user.full_name)
    await show(cb, texts.success(participant.number, config.giveaway_name))
    await cb.answer("🎉 Номер получен!")


# ---------- Капча в чате: «собери картинку» ----------


async def _photo(puzzle: ChatPuzzle) -> BufferedInputFile:
    return BufferedInputFile(await puzzle.render(), filename="puzzle.jpg")


async def _edit_photo(
    cb: CallbackQuery, puzzle: ChatPuzzle, caption: str, keyboard: InlineKeyboardMarkup | None
) -> None:
    """Меняет картинку в том же сообщении; если не вышло — присылает новое."""
    photo = await _photo(puzzle)
    if isinstance(cb.message, Message):
        try:
            await cb.message.edit_media(InputMediaPhoto(media=photo, caption=caption), reply_markup=keyboard)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
    await cb.bot.send_photo(cb.from_user.id, photo, caption=caption, reply_markup=keyboard)


@router.callback_query(PuzzleCb.filter())
async def puzzle_pressed(
    cb: CallbackQuery,
    callback_data: PuzzleCb,
    bot: Bot,
    db: Database,
    config: Config,
    chat_captcha: ChatCaptchaStore,
) -> None:
    user = cb.from_user
    if await db.get_participant(user.id) is not None:  # номер уже выдан, например в приложении
        await cb.answer("Ты уже участвуешь — номер выдан ✅", show_alert=True)
        return
    puzzle = chat_captcha.get(user.id, callback_data.id)
    if puzzle is None:
        await cb.answer("Эта картинка устарела. Нажми «✅ Проверить подписку», чтобы получить новую.", show_alert=True)
        return

    if callback_data.action == "new" or puzzle.expired:
        try:
            puzzle = await chat_captcha.issue(user.id)
        except CaptchaRateLimited:
            await cb.answer(TOO_MANY_PUZZLES, show_alert=True)
            return
        note = "" if callback_data.action == "new" else "⏱ Время вышло — вот новая картинка."
        await _edit_photo(cb, puzzle, texts.puzzle_caption(note), puzzle_kb(puzzle.id, None))
        await cb.answer("Новая картинка")
        return

    async with puzzle.lock:  # быстрые нажатия обрабатываем по очереди
        if chat_captcha.get(user.id, puzzle.id) is not puzzle:  # пока ждали, картинку сменили или собрали
            await cb.answer()
            return
        if not puzzle.tap(callback_data.pos):  # выбрали первый кусочек или сняли выбор
            if isinstance(cb.message, Message):
                try:
                    await cb.message.edit_reply_markup(reply_markup=puzzle_kb(puzzle.id, puzzle.selected))
                except TelegramBadRequest:
                    pass
            await cb.answer("Теперь нажми, с каким кусочком поменять" if puzzle.selected is not None else None)
            return
        if not puzzle.solved:
            await _edit_photo(cb, puzzle, texts.puzzle_caption(), puzzle_kb(puzzle.id, None))
            await cb.answer()
            return
        chat_captcha.solve(user.id)
        await _edit_photo(cb, puzzle, texts.puzzle_solved(), None)

    # Пока собирал картинку, мог отписаться — проверяем условия ещё раз.
    sponsors = await db.get_sponsors()
    missing = await find_missing(bot, db, sponsors, user.id)
    if not sponsors:
        await bot.send_message(user.id, texts.no_sponsors(config.giveaway_name))
        await cb.answer()
        return
    if missing:
        await bot.send_message(user.id, texts.missing(missing), reply_markup=sponsors_kb(missing, config.webapp_url))
        await cb.answer("Ещё не все условия выполнены")
        return
    participant, _ = await db.register_participant(user.id, user.username, user.full_name)
    await bot.send_message(user.id, texts.success(participant.number, config.giveaway_name))
    await cb.answer("🎉 Номер получен!")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Нажми /start, чтобы участвовать в розыгрыше 🎁")
