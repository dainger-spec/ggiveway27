import csv
import io
import math
import re
from datetime import datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from .. import keyboards as kb
from .. import media, texts
from ..checker import bot_is_admin, bot_rights, find_missing
from ..config import Config
from ..database import KIND_BOT, KIND_CHANNEL, KIND_CHAT, Database, Sponsor
from ..links import USERNAME, SponsorRef, parse_message
from ..utils import IsAdmin, not_command, show

router = Router(name="admin")
router.message.filter(F.chat.type == ChatType.PRIVATE, IsAdmin())
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 20
AMOUNT_MAX_LEN = 32


class AddSponsor(StatesGroup):
    source = State()  # ждём ссылку / @username / пересланный пост / выбор кнопкой
    url = State()
    amount = State()


class EditAmount(StatesGroup):
    amount = State()


class FindParticipant(StatesGroup):
    query = State()


class SetBackground(StatesGroup):
    media = State()


class SetSponsorBackground(StatesGroup):
    media = State()


# ---------- Вспомогательное ----------


async def _send_menu(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    sponsors = await db.get_sponsors()
    total = await db.count_participants()
    await bot.send_message(
        chat_id,
        texts.admin_menu(config, sponsors, total),
        reply_markup=kb.admin_menu_kb(),
    )


async def _sponsor_card(bot: Bot, sponsor: Sponsor) -> tuple[str, InlineKeyboardMarkup]:
    rights = await bot_rights(bot, sponsor.chat_id) if sponsor.checkable else None
    return texts.sponsor_card(sponsor, rights), kb.sponsor_card_kb(sponsor.id, bool(sponsor.bg_file))


async def _send_sponsor_card(bot: Bot, chat_id: int, sponsor: Sponsor) -> None:
    text, markup = await _sponsor_card(bot, sponsor)
    await bot.send_message(chat_id, text, reply_markup=markup)


def _normalize_url(text: str) -> str | None:
    text = text.strip()
    if m := re.fullmatch(rf"@({USERNAME})", text):
        return f"https://t.me/{m.group(1)}"
    if re.fullmatch(r"(?:t\.me|telegram\.me)/\S+", text, re.I):
        return "https://" + text
    if re.fullmatch(r"(?:https?|tg)://\S+", text, re.I):
        return text
    return None


async def _ask_amount(bot: Bot, chat_id: int, state: FSMContext) -> None:
    await state.set_state(AddSponsor.amount)
    await bot.send_message(
        chat_id,
        "💰 Пришлите сумму, которую выделил этот спонсор (например: <code>500$</code>, "
        "<code>100 USDT</code>, <code>1000₽</code>).\n\n"
        f"Если суммы нет — нажмите «{kb.SKIP_TEXT}».",
        reply_markup=kb.amount_kb(editing=False),
    )


# ---------- Меню и отмена ----------


@router.message(Command("cancel"))
@router.message(F.text == kb.CANCEL_TEXT)
async def cancel(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    await _send_menu(bot, message.chat.id, db, config)


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    if await state.get_state() is not None:
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    await _send_menu(bot, message.chat.id, db, config)


@router.callback_query(kb.AdminCb.filter(F.action == "menu"))
async def menu(cb: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await state.clear()
    sponsors = await db.get_sponsors()
    total = await db.count_participants()
    await show(cb, texts.admin_menu(config, sponsors, total), kb.admin_menu_kb())
    await cb.answer()


# ---------- Список спонсоров, карточка, сумма, удаление ----------


@router.callback_query(kb.AdminCb.filter(F.action == "sponsors"))
async def sponsors_list(cb: CallbackQuery, db: Database) -> None:
    sponsors = await db.get_sponsors()
    await show(cb, texts.sponsors_list(sponsors), kb.admin_sponsors_kb(sponsors))
    await cb.answer()


@router.callback_query(kb.SponsorCb.filter(F.action == "view"))
async def sponsor_view(cb: CallbackQuery, callback_data: kb.SponsorCb, bot: Bot, db: Database) -> None:
    sponsor = await db.get_sponsor(callback_data.id)
    if sponsor is None:
        await cb.answer("Спонсор уже удалён", show_alert=True)
        return
    await show(cb, *await _sponsor_card(bot, sponsor))
    await cb.answer()


@router.callback_query(kb.SponsorCb.filter(F.action == "delete"))
async def sponsor_delete(cb: CallbackQuery, callback_data: kb.SponsorCb, db: Database) -> None:
    sponsor = await db.get_sponsor(callback_data.id)
    if sponsor is None:
        await cb.answer("Спонсор уже удалён", show_alert=True)
        return
    await show(
        cb,
        f"🗑 Удалить спонсора {texts.sponsor_line(sponsor)}?",
        kb.confirm_delete_kb(sponsor.id),
    )
    await cb.answer()


@router.callback_query(kb.SponsorCb.filter(F.action == "delete_ok"))
async def sponsor_delete_ok(cb: CallbackQuery, callback_data: kb.SponsorCb, db: Database, config: Config) -> None:
    sponsor = await db.get_sponsor(callback_data.id)
    if sponsor is not None:
        await media.remove_sponsor_background(db, config.media_dir, sponsor)
    deleted = await db.delete_sponsor(callback_data.id)
    sponsors = await db.get_sponsors()
    await show(cb, texts.sponsors_list(sponsors), kb.admin_sponsors_kb(sponsors))
    await cb.answer("Спонсор удалён" if deleted else "Спонсор уже удалён")


@router.callback_query(kb.SponsorCb.filter(F.action == "amount"))
async def amount_edit_start(
    cb: CallbackQuery, callback_data: kb.SponsorCb, state: FSMContext, bot: Bot, db: Database
) -> None:
    sponsor = await db.get_sponsor(callback_data.id)
    if sponsor is None:
        await cb.answer("Спонсор уже удалён", show_alert=True)
        return
    await state.set_state(EditAmount.amount)
    await state.update_data(sponsor_id=sponsor.id)
    current = sponsor.amount or "не указана"
    await bot.send_message(
        cb.from_user.id,
        f"💰 {texts.sponsor_line(sponsor)}\nТекущая сумма: <b>{escape(current)}</b>\n\n"
        f"Пришлите новую сумму или нажмите «{kb.REMOVE_AMOUNT_TEXT}».",
        reply_markup=kb.amount_kb(editing=True),
    )
    await cb.answer()


@router.message(EditAmount.amount, not_command)
async def amount_edit_received(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пришлите сумму текстом.")
        return
    if len(text) > AMOUNT_MAX_LEN:
        await message.answer(f"Слишком длинно — максимум {AMOUNT_MAX_LEN} символа.")
        return
    amount = None if text == kb.REMOVE_AMOUNT_TEXT else text
    sponsor_id = (await state.get_data())["sponsor_id"]
    await state.clear()

    if not await db.update_amount(sponsor_id, amount):
        await message.answer("Спонсор уже удалён.", reply_markup=ReplyKeyboardRemove())
        return
    await message.answer("✅ Сумма обновлена." if amount else "✅ Сумма убрана.", reply_markup=ReplyKeyboardRemove())
    await _send_sponsor_card(bot, message.chat.id, await db.get_sponsor(sponsor_id))


@router.callback_query(kb.SponsorCb.filter(F.action == "bg"))
async def sponsor_bg_start(
    cb: CallbackQuery, callback_data: kb.SponsorCb, state: FSMContext, bot: Bot, db: Database
) -> None:
    sponsor = await db.get_sponsor(callback_data.id)
    if sponsor is None:
        await cb.answer("Спонсор уже удалён", show_alert=True)
        return
    await state.set_state(SetSponsorBackground.media)
    await state.update_data(sponsor_id=sponsor.id)
    await bot.send_message(
        cb.from_user.id,
        f"🖼 Пришлите картинку, GIF или видео для капсулы {texts.sponsor_line(sponsor)} (до 20 МБ).\n\n"
        "Капсула вытянутая, поэтому лучше подходит горизонтальная картинка. Слева фон затемняется, "
        "чтобы название и сумма читались.",
        reply_markup=kb.cancel_kb(),
    )
    await cb.answer()


@router.message(SetSponsorBackground.media, not_command)
async def sponsor_bg_received(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    sponsor = await db.get_sponsor((await state.get_data())["sponsor_id"])
    if sponsor is None:
        await state.clear()
        await message.answer("Спонсор уже удалён.", reply_markup=ReplyKeyboardRemove())
        return
    progress = await message.answer("⏳ Загружаю…")
    error = await media.save_sponsor_background(bot, db, config.media_dir, sponsor, message)
    await progress.delete()
    if error:
        await message.answer(error)
        return
    await state.clear()
    await message.answer(
        "✅ Фон капсулы обновлён — откройте мини-приложение, чтобы посмотреть.", reply_markup=ReplyKeyboardRemove()
    )
    await _send_sponsor_card(bot, message.chat.id, await db.get_sponsor(sponsor.id))


@router.callback_query(kb.SponsorCb.filter(F.action == "bg_delete"))
async def sponsor_bg_delete(
    cb: CallbackQuery, callback_data: kb.SponsorCb, bot: Bot, db: Database, config: Config
) -> None:
    sponsor = await db.get_sponsor(callback_data.id)
    if sponsor is None:
        await cb.answer("Спонсор уже удалён", show_alert=True)
        return
    await media.remove_sponsor_background(db, config.media_dir, sponsor)
    await show(cb, *await _sponsor_card(bot, await db.get_sponsor(sponsor.id)))
    await cb.answer("Фон капсулы убран")


# ---------- Добавление спонсора ----------


ADD_PROMPT = (
    "➕ <b>Новый спонсор</b>\n\n"
    "Пришлите ссылку на канал, чат или бота — я сам пойму, что это:\n"
    "• <code>https://t.me/channel</code> или <code>@channel</code>\n"
    "• приглашение в приватный канал: <code>https://t.me/+AbCd…</code>\n"
    "• ссылку на любой пост, ID <code>-100…</code> или пересланный сюда пост\n"
    "• бота: <code>@some_bot</code> или <code>https://t.me/some_bot?start=ref</code>\n\n"
    "Или выберите кнопкой внизу. В каналах и чатах бот должен быть <b>администратором</b>, "
    "иначе он не сможет проверять подписку."
)


@router.callback_query(kb.AdminCb.filter(F.action == "add"))
@router.callback_query(kb.KindCb.filter())  # кнопки «Канал/Чат/Бот» из старых сообщений
async def add_start(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await state.set_state(AddSponsor.source)
    await bot.send_message(cb.from_user.id, ADD_PROMPT, reply_markup=kb.choose_source_kb())
    await cb.answer()


@router.message(AddSponsor.source, not_command)
async def add_source_received(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    ref = parse_message(message)
    if ref is None:
        await message.answer(
            "Не нашёл тут ссылку на канал, чат или бота. Пришлите ссылку вида <code>https://t.me/…</code> "
            "или <code>@username</code>, либо выберите кнопкой внизу."
        )
        return
    await _add_from_ref(message.chat.id, ref, state, bot, db, config)


@router.callback_query(kb.PickChatCb.filter())
async def add_chat_picked(
    cb: CallbackQuery, callback_data: kb.PickChatCb, state: FSMContext, bot: Bot, db: Database, config: Config
) -> None:
    await cb.answer()
    await state.set_state(AddSponsor.source)
    await _add_from_ref(cb.from_user.id, SponsorRef(chat_id=callback_data.chat_id), state, bot, db, config)


async def _match_invite(bot: Bot, db: Database, invite_url: str) -> int | None:
    """Приватный чат по ссылке-приглашению: сверяем с основной ссылкой чатов, где бот админ."""
    code = invite_url.rsplit("+", 1)[-1]
    for chat_id, _, _ in await db.known_chats_without_sponsor():
        try:
            chat = await bot.get_chat(chat_id)
        except TelegramAPIError:
            continue
        if chat.invite_link and chat.invite_link.rstrip("/").rsplit("+", 1)[-1].rsplit("/", 1)[-1] == code:
            return chat_id
    return None


async def _add_from_ref(
    admin_chat: int, ref: SponsorRef, state: FSMContext, bot: Bot, db: Database, config: Config
) -> None:
    """Определяет, канал это, чат или бот, проверяет права бота и переходит к сумме."""
    invite_url = ref.invite_url or (await state.get_data()).get("invite_url")

    if ref.invite_url and ref.chat_id is None:
        chat_id = await _match_invite(bot, db, ref.invite_url)
        if chat_id is None:
            await state.update_data(invite_url=ref.invite_url)
            known = await db.known_chats_without_sponsor()
            how = "выберите его в списке ниже, " if known else ""
            await bot.send_message(
                admin_chat,
                "🔗 Ссылку-приглашение запомнил.\n\nПо такой ссылке Telegram не даёт боту узнать, что это за "
                f"канал, поэтому подскажите: {how}нажмите «Выбрать канал/чат» внизу или перешлите сюда "
                "любой пост из этого канала.",
                reply_markup=kb.pick_chat_kb(known) if known else None,
            )
            return
        ref = SponsorRef(chat_id=chat_id)

    chat = None
    if not ref.is_bot:
        try:
            chat = await bot.get_chat(ref.chat_id if ref.chat_id is not None else f"@{ref.username}")
        except TelegramAPIError:
            chat = None

    if chat is None or chat.type == ChatType.PRIVATE:
        if ref.username and (ref.is_bot or ref.username.lower().endswith("bot")):
            await _add_bot(admin_chat, ref, state, bot, db)
        elif chat is not None:
            await bot.send_message(admin_chat, "Это ссылка на пользователя, а не на канал, чат или бота.")
        elif ref.username:
            await bot.send_message(
                admin_chat,
                f"Не нашёл @{escape(ref.username)}. Проверьте ссылку. Если канал приватный — пришлите "
                "ссылку-приглашение или перешлите сюда пост из него.",
            )
        else:
            await bot.send_message(
                admin_chat,
                "Не вижу этот канал/чат. Добавьте бота туда <b>администратором</b> и пришлите ссылку ещё раз.",
            )
        return

    kind = KIND_CHANNEL if chat.type == ChatType.CHANNEL else KIND_CHAT
    title = chat.title or str(chat.id)
    name = f"{texts.KIND_NAMES[kind].lower()} «{escape(title)}»"
    if not await bot_is_admin(bot, chat.id):
        await bot.send_message(
            admin_chat,
            f"⚠️ Нашёл {name}, но бот там не администратор. Назначьте его администратором и пришлите ссылку ещё раз.",
        )
        return
    await db.remember_chat(chat.id, title, chat.type)
    if await db.has_chat(chat.id):
        await bot.send_message(admin_chat, f"{name.capitalize()} уже есть в списке спонсоров.")
        return

    await state.update_data(kind=kind, chat_id=chat.id, title=title)
    found = f"✅ Распознал: {texts.KIND_ICONS[kind]} {name}"
    url = invite_url or (f"https://t.me/{chat.username}" if chat.username else None)
    if url:
        await state.update_data(url=url)
        await bot.send_message(admin_chat, f"{found}\nСсылка для участников: {escape(url)}")
        await _ask_amount(bot, admin_chat, state)
        return

    # Приватный чат выбран кнопкой или переслан без ссылки — создаём приглашение сами.
    try:
        suggested = (await bot.create_chat_invite_link(chat.id, name=config.giveaway_name[:32])).invite_link
    except TelegramAPIError:
        suggested = chat.invite_link
    await state.update_data(suggested_url=suggested)
    await state.set_state(AddSponsor.url)
    if suggested:
        await bot.send_message(
            admin_chat,
            f"{found}\n\nСсылка для участников: {suggested}\n\n"
            f"Нажмите «{kb.KEEP_URL_TEXT}» или пришлите свою ссылку-приглашение.",
            reply_markup=kb.url_kb(has_suggested=True),
        )
    else:
        await bot.send_message(
            admin_chat,
            f"{found}\n\nНе удалось создать ссылку автоматически. Пришлите ссылку-приглашение, "
            "по которой участники будут вступать.",
            reply_markup=kb.url_kb(has_suggested=False),
        )


async def _add_bot(admin_chat: int, ref: SponsorRef, state: FSMContext, bot: Bot, db: Database) -> None:
    title = f"@{ref.username}"
    if any(s.kind == KIND_BOT and s.title.lower() == title.lower() for s in await db.get_sponsors()):
        await bot.send_message(admin_chat, f"Бот {escape(title)} уже есть в списке спонсоров.")
        return
    url = ref.link or f"https://t.me/{ref.username}"
    await state.update_data(kind=KIND_BOT, chat_id=None, title=title, url=url)
    await bot.send_message(
        admin_chat,
        f"✅ Распознал: 🤖 бот <b>{escape(title)}</b>\nСсылка для участников: {escape(url)}\n\n"
        "ℹ️ Запуск чужого бота Telegram проверить не даёт — условие покажем участникам без проверки.",
    )
    await _ask_amount(bot, admin_chat, state)


@router.message(AddSponsor.url, not_command)
async def add_url_received(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    if message.text == kb.KEEP_URL_TEXT and data.get("suggested_url"):
        url = data["suggested_url"]
    else:
        url = _normalize_url(message.text or "")
        if url is None:
            await message.answer("Это не похоже на ссылку. Пример: <code>https://t.me/+AbCdEf123</code>")
            return
    await state.update_data(url=url)
    await _ask_amount(bot, message.chat.id, state)


@router.message(AddSponsor.amount, not_command)
async def add_amount_received(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пришлите сумму текстом.")
        return
    if len(text) > AMOUNT_MAX_LEN:
        await message.answer(f"Слишком длинно — максимум {AMOUNT_MAX_LEN} символа.")
        return

    data = await state.get_data()
    sponsor_id = await db.add_sponsor(
        kind=data["kind"],
        title=data["title"],
        url=data["url"],
        chat_id=data.get("chat_id"),
        amount=None if text == kb.SKIP_TEXT else text,
    )
    await state.clear()
    await message.answer("✅ Спонсор добавлен!", reply_markup=ReplyKeyboardRemove())
    await _send_sponsor_card(bot, message.chat.id, await db.get_sponsor(sponsor_id))


# ---------- Фон мини-приложения ----------


async def _background_menu(db: Database, config: Config) -> tuple[str, InlineKeyboardMarkup]:
    background = await media.get_background(db, config.media_dir)
    return texts.background_status(background, config.webapp_url), kb.background_kb(background is not None)


@router.callback_query(kb.AdminCb.filter(F.action == "bg"))
async def background_menu(cb: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await state.clear()
    await show(cb, *await _background_menu(db, config))
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.action == "bg_upload"))
async def background_upload_start(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.set_state(SetBackground.media)
    await bot.send_message(
        cb.from_user.id,
        "📤 Пришлите картинку, GIF или видео для фона (до 20 МБ).\n\n"
        "Совет: чтобы Telegram не сжимал картинку, отправьте её как файл.",
        reply_markup=kb.cancel_kb(),
    )
    await cb.answer()


@router.message(SetBackground.media, not_command)
async def background_received(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    progress = await message.answer("⏳ Загружаю…")
    error = await media.save_background(bot, db, config.media_dir, message)
    await progress.delete()
    if error:
        await message.answer(error)
        return
    await state.clear()
    await message.answer(
        "✅ Фон обновлён — откройте мини-приложение, чтобы посмотреть.", reply_markup=ReplyKeyboardRemove()
    )
    text, markup = await _background_menu(db, config)
    await message.answer(text, reply_markup=markup)


@router.callback_query(kb.AdminCb.filter(F.action == "bg_delete"))
async def background_delete(cb: CallbackQuery, db: Database, config: Config) -> None:
    await media.remove_background(db, config.media_dir)
    await show(cb, *await _background_menu(db, config))
    await cb.answer("Фон убран")


# ---------- Участники ----------


@router.callback_query(kb.PageCb.filter())
async def participants(cb: CallbackQuery, callback_data: kb.PageCb, db: Database) -> None:
    total = await db.count_participants()
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(callback_data.page, 0), pages - 1)
    rows = await db.list_participants(offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    await show(cb, texts.participants_page(rows, total, page, pages), kb.participants_kb(page, pages))
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.action == "export"))
async def export_csv(cb: CallbackQuery, bot: Bot, db: Database) -> None:
    rows = await db.list_participants()
    if not rows:
        await cb.answer("Участников пока нет", show_alert=True)
        return

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Номер", "Telegram ID", "Username", "Имя", "Дата (UTC)"])
    for p in rows:
        writer.writerow([p.number, p.user_id, f"@{p.username}" if p.username else "", p.full_name, p.joined_at])

    filename = f"participants_{datetime.now():%Y-%m-%d_%H-%M}.csv"
    await bot.send_document(
        cb.from_user.id,
        BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename=filename),
        caption=f"👥 Участников: {len(rows)}",
    )
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.action == "find"))
async def find_start(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.set_state(FindParticipant.query)
    await bot.send_message(
        cb.from_user.id,
        "🔎 Пришлите номер участника (например, <code>17</code>) или его @username.",
        reply_markup=kb.cancel_kb(),
    )
    await cb.answer()


@router.message(FindParticipant.query, not_command)
async def find_received(message: Message, state: FSMContext, db: Database) -> None:
    text = (message.text or "").strip().lstrip("№#")
    if text.isdigit():
        participant = await db.get_participant_by_number(int(text))
    elif re.fullmatch(r"@?\w+", text):
        participant = await db.get_participant_by_username(text.lstrip("@"))
    else:
        await message.answer("Пришлите номер или @username.")
        return

    if participant is None:
        await message.answer("Участник не найден. Попробуйте другой номер или нажмите «❌ Отмена».")
        return

    await state.clear()
    await message.answer("Найден:", reply_markup=ReplyKeyboardRemove())
    await message.answer(texts.participant_card(participant), reply_markup=kb.participant_kb(participant.user_id))


@router.callback_query(kb.RecheckCb.filter())
async def recheck(cb: CallbackQuery, callback_data: kb.RecheckCb, bot: Bot, db: Database) -> None:
    participant = await db.get_participant(callback_data.user_id)
    if participant is None:
        await cb.answer("Участник не найден", show_alert=True)
        return
    missing = await find_missing(bot, db, await db.get_sponsors(), participant.user_id)
    checked_at = datetime.now().strftime("%H:%M:%S")
    await show(
        cb,
        f"{texts.participant_card(participant)}\n\n{texts.recheck_result(missing)}\n<i>Проверено в {checked_at}</i>",
        kb.participant_kb(participant.user_id),
    )
    await cb.answer()
