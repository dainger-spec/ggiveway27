from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .database import KIND_BOT, KIND_CHANNEL, KIND_CHAT, Sponsor
from .texts import KIND_ICONS

CHECK_CB = "check_subs"

WEBAPP_TEXT = "🎁 Открыть приложение"
CANCEL_TEXT = "❌ Отмена"
KEEP_URL_TEXT = "✅ Оставить эту ссылку"
SKIP_TEXT = "⏭ Пропустить"
REMOVE_AMOUNT_TEXT = "🚫 Убрать сумму"


class AdminCb(CallbackData, prefix="adm"):
    action: str  # menu | sponsors | add | find | export | bg | bg_upload | bg_delete


class KindCb(CallbackData, prefix="kind"):
    kind: str


class SponsorCb(CallbackData, prefix="sp"):
    action: str  # view | amount | delete | delete_ok
    id: int


class PageCb(CallbackData, prefix="pg"):
    page: int


class RecheckCb(CallbackData, prefix="rc"):
    user_id: int


def _sponsor_button_text(s: Sponsor) -> str:
    title = s.title if len(s.title) <= 40 else s.title[:39] + "…"
    text = f"{KIND_ICONS[s.kind]} {title}"
    if s.amount:
        text += f" · {s.amount}"
    return text


# ---------- Участники розыгрыша ----------


def webapp_info(webapp_url: str) -> WebAppInfo:
    return WebAppInfo(url=webapp_url + "/")


def webapp_kb(webapp_url: str | None) -> InlineKeyboardMarkup | None:
    if not webapp_url:
        return None
    b = InlineKeyboardBuilder()
    b.button(text=WEBAPP_TEXT, web_app=webapp_info(webapp_url))
    return b.as_markup()


def sponsors_kb(sponsors: list[Sponsor], webapp_url: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if webapp_url:
        b.button(text=WEBAPP_TEXT, web_app=webapp_info(webapp_url))
    for s in sponsors:
        b.button(text=_sponsor_button_text(s), url=s.url)
    b.button(text="✅ Проверить подписку", callback_data=CHECK_CB)
    b.adjust(1)
    return b.as_markup()


# ---------- Админка ----------


def admin_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 Спонсоры", callback_data=AdminCb(action="sponsors"))
    b.button(text="➕ Добавить спонсора", callback_data=AdminCb(action="add"))
    b.button(text="👥 Участники", callback_data=PageCb(page=0))
    b.button(text="🔎 Найти участника", callback_data=AdminCb(action="find"))
    b.button(text="📥 Выгрузить участников (CSV)", callback_data=AdminCb(action="export"))
    b.button(text="🖼 Фон мини-приложения", callback_data=AdminCb(action="bg"))
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def background_kb(has_background: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="📤 Загрузить новый фон" if has_background else "📤 Загрузить фон",
        callback_data=AdminCb(action="bg_upload"),
    )
    if has_background:
        b.button(text="🗑 Убрать фон", callback_data=AdminCb(action="bg_delete"))
    b.button(text="« В меню", callback_data=AdminCb(action="menu"))
    b.adjust(1)
    return b.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="« В меню", callback_data=AdminCb(action="menu"))
    return b.as_markup()


def admin_sponsors_kb(sponsors: list[Sponsor]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in sponsors:
        b.button(text=_sponsor_button_text(s), callback_data=SponsorCb(action="view", id=s.id))
    b.button(text="➕ Добавить спонсора", callback_data=AdminCb(action="add"))
    b.button(text="« В меню", callback_data=AdminCb(action="menu"))
    b.adjust(1)
    return b.as_markup()


def kinds_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📢 Канал", callback_data=KindCb(kind=KIND_CHANNEL))
    b.button(text="💬 Чат", callback_data=KindCb(kind=KIND_CHAT))
    b.button(text="🤖 Бот", callback_data=KindCb(kind=KIND_BOT))
    b.button(text="« Назад", callback_data=AdminCb(action="sponsors"))
    b.adjust(3, 1)
    return b.as_markup()


def sponsor_card_kb(sponsor_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💰 Изменить сумму", callback_data=SponsorCb(action="amount", id=sponsor_id))
    b.button(text="🗑 Удалить", callback_data=SponsorCb(action="delete", id=sponsor_id))
    b.button(text="« К списку спонсоров", callback_data=AdminCb(action="sponsors"))
    b.adjust(2, 1)
    return b.as_markup()


def confirm_delete_kb(sponsor_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, удалить", callback_data=SponsorCb(action="delete_ok", id=sponsor_id))
    b.button(text="« Отмена", callback_data=SponsorCb(action="view", id=sponsor_id))
    b.adjust(2)
    return b.as_markup()


def participants_kb(page: int, pages: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    nav = 0
    if page > 0:
        b.button(text="◀️", callback_data=PageCb(page=page - 1))
        nav += 1
    if page < pages - 1:
        b.button(text="▶️", callback_data=PageCb(page=page + 1))
        nav += 1
    b.button(text="📥 CSV", callback_data=AdminCb(action="export"))
    b.button(text="« В меню", callback_data=AdminCb(action="menu"))
    b.adjust(*([nav] if nav else []), 2)
    return b.as_markup()


def participant_kb(user_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Проверить подписки сейчас", callback_data=RecheckCb(user_id=user_id))
    b.button(text="🔎 Найти другого", callback_data=AdminCb(action="find"))
    b.button(text="« В меню", callback_data=AdminCb(action="menu"))
    b.adjust(1, 2)
    return b.as_markup()


def _reply_kb(*rows: list[KeyboardButton]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=list(rows), resize_keyboard=True)


def cancel_kb() -> ReplyKeyboardMarkup:
    return _reply_kb([KeyboardButton(text=CANCEL_TEXT)])


def choose_chat_kb(kind: str) -> ReplyKeyboardMarkup:
    is_channel = kind == KIND_CHANNEL
    button = KeyboardButton(
        text="📢 Выбрать канал" if is_channel else "💬 Выбрать чат",
        request_chat=KeyboardButtonRequestChat(request_id=1, chat_is_channel=is_channel),
    )
    return _reply_kb([button], [KeyboardButton(text=CANCEL_TEXT)])


def url_kb(has_suggested: bool) -> ReplyKeyboardMarkup:
    if not has_suggested:
        return cancel_kb()
    return _reply_kb([KeyboardButton(text=KEEP_URL_TEXT)], [KeyboardButton(text=CANCEL_TEXT)])


def amount_kb(editing: bool) -> ReplyKeyboardMarkup:
    first = REMOVE_AMOUNT_TEXT if editing else SKIP_TEXT
    return _reply_kb([KeyboardButton(text=first)], [KeyboardButton(text=CANCEL_TEXT)])
