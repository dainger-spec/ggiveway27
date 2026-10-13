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
    MessageOriginChannel,
    MessageOriginChat,
    ReplyKeyboardRemove,
)

from .. import keyboards as kb
from .. import media, texts
from ..checker import bot_is_admin, find_missing
from ..config import Config
from ..database import KIND_BOT, KIND_CHANNEL, KIND_CHAT, KINDS, Database, Sponsor
from ..utils import IsAdmin, not_command, show

router = Router(name="admin")
router.message.filter(F.chat.type == ChatType.PRIVATE, IsAdmin())
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 20
AMOUNT_MAX_LEN = 32

_USERNAME_RE = r"[A-Za-z][A-Za-z0-9_]{3,31}"
_LINK_RE = re.compile(rf"(?:https?://)?(?:t\.me|telegram\.me)/({_USERNAME_RE})/?(\?\S*)?", re.I)


class AddSponsor(StatesGroup):
    chat = State()
    url = State()
    bot = State()
    amount = State()


class EditAmount(StatesGroup):
    amount = State()


class FindParticipant(StatesGroup):
    query = State()


class SetBackground(StatesGroup):
    media = State()


# ---------- Вспомогательное ----------


async def _send_menu(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    sponsors = await db.get_sponsors()
    total = await db.count_participants()
    await bot.send_message(
        chat_id,
        texts.admin_menu(config.giveaway_name, sponsors, total, config.webapp_url),
        reply_markup=kb.admin_menu_kb(),
    )


async def _send_sponsor_card(bot: Bot, chat_id: int, sponsor: Sponsor) -> None:
    is_admin = await bot_is_admin(bot, sponsor.chat_id) if sponsor.checkable else None
    await bot.send_message(chat_id, texts.sponsor_card(sponsor, is_admin), reply_markup=kb.sponsor_card_kb(sponsor.id))


def _extract_chat_ref(message: Message) -> int | str | None:
    """Достаёт канал/чат из выбора кнопкой, пересланного поста, @username, ссылки или ID."""
    if message.chat_shared:
        return message.chat_shared.chat_id
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        return origin.chat.id
    if isinstance(origin, MessageOriginChat):
        return origin.sender_chat.id

    text = (message.text or "").strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if m := re.fullmatch(rf"@({_USERNAME_RE})", text):
        return "@" + m.group(1)
    if m := _LINK_RE.fullmatch(text):
        return "@" + m.group(1)
    return None


def _normalize_url(text: str) -> str | None:
    text = text.strip()
    if m := re.fullmatch(rf"@({_USERNAME_RE})", text):
        return f"https://t.me/{m.group(1)}"
    if re.fullmatch(r"(?:t\.me|telegram\.me)/\S+", text, re.I):
        return "https://" + text
    if re.fullmatch(r"(?:https?|tg)://\S+", text, re.I):
        return text
    return None


async def _ask_amount(message: Message, state: FSMContext) -> None:
    await state.set_state(AddSponsor.amount)
    await message.answer(
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
    await show(cb, texts.admin_menu(config.giveaway_name, sponsors, total, config.webapp_url), kb.admin_menu_kb())
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
    is_admin = await bot_is_admin(bot, sponsor.chat_id) if sponsor.checkable else None
    await show(cb, texts.sponsor_card(sponsor, is_admin), kb.sponsor_card_kb(sponsor.id))
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
async def sponsor_delete_ok(cb: CallbackQuery, callback_data: kb.SponsorCb, db: Database) -> None:
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


# ---------- Добавление спонсора ----------


@router.callback_query(kb.AdminCb.filter(F.action == "add"))
async def add_start(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, "➕ <b>Новый спонсор</b>\n\nЧто добавляем?", kb.kinds_kb())
    await cb.answer()


@router.callback_query(kb.KindCb.filter(F.kind.in_(KINDS)))
async def add_kind_chosen(cb: CallbackQuery, callback_data: kb.KindCb, state: FSMContext, bot: Bot) -> None:
    kind = callback_data.kind
    await state.update_data(kind=kind)

    if kind == KIND_BOT:
        await state.set_state(AddSponsor.bot)
        await bot.send_message(
            cb.from_user.id,
            "🤖 Пришлите @username бота или ссылку на него.\n"
            "Можно реферальную: <code>https://t.me/some_bot?start=ref123</code>",
            reply_markup=kb.cancel_kb(),
        )
    else:
        where = "канала" if kind == KIND_CHANNEL else "чата"
        await state.set_state(AddSponsor.chat)
        await bot.send_message(
            cb.from_user.id,
            f"{texts.KIND_ICONS[kind]} Сначала добавьте этого бота <b>администратором</b> {where} — "
            "без этого он не сможет проверять подписку.\n\n"
            f"Затем выберите {where[:-1]} кнопкой ниже, или пришлите его @username / ссылку / "
            "ID (<code>-100…</code>), или перешлите сюда любой пост из канала.",
            reply_markup=kb.choose_chat_kb(kind),
        )
    await cb.answer()


@router.message(AddSponsor.chat, not_command)
async def add_chat_received(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    kind = (await state.get_data())["kind"]
    ref = _extract_chat_ref(message)
    if ref is None:
        await message.answer(
            "Не получилось распознать. Воспользуйтесь кнопкой выбора, пришлите @username, "
            "ссылку вида t.me/username или числовой ID."
        )
        return

    try:
        chat = await bot.get_chat(ref)
    except TelegramAPIError:
        await message.answer(
            "Не удалось найти этот чат. Убедитесь, что бот добавлен туда администратором, и попробуйте ещё раз."
        )
        return

    if kind == KIND_CHANNEL and chat.type != ChatType.CHANNEL:
        await message.answer("Это не канал. Пришлите канал или нажмите «❌ Отмена».")
        return
    if kind == KIND_CHAT and chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("Это не группа/чат. Пришлите чат или нажмите «❌ Отмена».")
        return
    if not await bot_is_admin(bot, chat.id):
        await message.answer(
            f"⚠️ Бот не является администратором в «{escape(chat.title or '')}». "
            "Назначьте его администратором и пришлите чат ещё раз."
        )
        return
    if await db.has_chat(chat.id):
        await message.answer("Этот спонсор уже есть в списке.")
        return

    if chat.username:
        suggested = f"https://t.me/{chat.username}"
    else:
        try:
            link = await bot.create_chat_invite_link(chat.id, name=config.giveaway_name[:32])
            suggested = link.invite_link
        except TelegramAPIError:
            suggested = chat.invite_link

    await state.update_data(chat_id=chat.id, title=chat.title or str(chat.id), suggested_url=suggested)
    await state.set_state(AddSponsor.url)

    found = f"✅ Найден {texts.KIND_NAMES[kind].lower()}: <b>{escape(chat.title or '')}</b>\n\n"
    if suggested:
        await message.answer(
            found + f"Ссылка для участников: {suggested}\n\n"
            f"Нажмите «{kb.KEEP_URL_TEXT}» или пришлите другую ссылку (например, свою пригласительную).",
            reply_markup=kb.url_kb(has_suggested=True),
        )
    else:
        await message.answer(
            found + "Не удалось получить ссылку автоматически. Пришлите ссылку-приглашение, "
            "по которой участники будут вступать.",
            reply_markup=kb.url_kb(has_suggested=False),
        )


@router.message(AddSponsor.url, not_command)
async def add_url_received(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if message.text == kb.KEEP_URL_TEXT and data.get("suggested_url"):
        url = data["suggested_url"]
    else:
        url = _normalize_url(message.text or "")
        if url is None:
            await message.answer("Это не похоже на ссылку. Пример: <code>https://t.me/+AbCdEf123</code>")
            return
    await state.update_data(url=url)
    await _ask_amount(message, state)


@router.message(AddSponsor.bot, not_command)
async def add_bot_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if m := re.fullmatch(rf"@?({_USERNAME_RE})", text):
        username, query = m.group(1), ""
    elif m := _LINK_RE.fullmatch(text):
        username, query = m.group(1), m.group(2) or ""
    else:
        await message.answer("Пришлите @username бота или ссылку вида https://t.me/some_bot")
        return
    if not username.lower().endswith("bot"):
        await message.answer("Юзернейм бота в Telegram всегда оканчивается на «bot». Проверьте и пришлите ещё раз.")
        return

    await state.update_data(title=f"@{username}", url=f"https://t.me/{username}{query}", chat_id=None)
    await _ask_amount(message, state)


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
    missing = await find_missing(bot, await db.get_sponsors(), participant.user_id)
    checked_at = datetime.now().strftime("%H:%M:%S")
    await show(
        cb,
        f"{texts.participant_card(participant)}\n\n{texts.recheck_result(missing)}\n<i>Проверено в {checked_at}</i>",
        kb.participant_kb(participant.user_id),
    )
    await cb.answer()
