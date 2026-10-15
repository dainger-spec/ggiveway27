from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    KeyboardButtonRequestUsers,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .database import KIND_BOT, KIND_CHANNEL, KIND_CHAT, Sponsor
from .texts import KIND_ICONS

KIND_BUTTONS = {KIND_CHANNEL: "Подписаться", KIND_CHAT: "Вступить", KIND_BOT: "Запустить"}

CHECK_CB = "check_subs"

WEBAPP_TEXT = "🎁 Открыть приложение"
CANCEL_TEXT = "❌ Отмена"
KEEP_URL_TEXT = "✅ Оставить эту ссылку"
SKIP_TEXT = "⏭ Пропустить"
REMOVE_AMOUNT_TEXT = "🚫 Убрать сумму"


class AdminCb(CallbackData, prefix="adm"):
    action: str  # menu | sponsors | add | find | export | bg | bg_upload | bg_delete | captcha | bc | bc_*


class KindCb(CallbackData, prefix="kind"):  # только чтобы старые кнопки «Канал/Чат/Бот» не зависали
    kind: str


class PickChatCb(CallbackData, prefix="pick"):
    chat_id: int


class SponsorCb(CallbackData, prefix="sp"):
    action: str  # view | amount | avatar | avatar_delete | bg | bg_delete | delete | delete_ok
    id: int


class PageCb(CallbackData, prefix="pg"):
    page: int


class RecheckCb(CallbackData, prefix="rc"):
    user_id: int


class CaptchaCb(CallbackData, prefix="cc"):
    action: str  # pick | new
    id: str  # код картинки: кнопки старых картинок не срабатывают
    option: int = -1


def _sponsor_button_text(s: Sponsor) -> str:
    title = s.title if len(s.title) <= 40 else s.title[:39] + "…"
    text = f"{KIND_ICONS[s.kind]} {title}"
    if s.amount:
        text += f" · {s.amount}"
    return text


# ---------- Участники розыгрыша ----------


def webapp_info(webapp_url: str) -> WebAppInfo:
    return WebAppInfo(url=webapp_url + "/")


def captcha_kb(challenge_id: str, options: list[str]) -> InlineKeyboardMarkup:
    """Числа под картинкой капчи — по три в ряд — и «Другая картинка»."""
    b = InlineKeyboardBuilder()
    for i, number in enumerate(options):
        b.button(text=number, callback_data=CaptchaCb(action="pick", id=challenge_id, option=i))
    b.button(text="🔄 Другая картинка", callback_data=CaptchaCb(action="new", id=challenge_id))
    b.adjust(3, 3, 1)
    return b.as_markup()


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


def new_sponsors_kb(sponsors: list[Sponsor], webapp_url: str | None) -> InlineKeyboardMarkup:
    """Кнопки к рассылке: ссылка на каждого нового спонсора, приложение и проверка."""
    b = InlineKeyboardBuilder()
    for s in sponsors:
        title = s.title if len(s.title) <= 32 else s.title[:31] + "…"
        b.button(text=f"{KIND_ICONS[s.kind]} {KIND_BUTTONS[s.kind]}: {title}", url=s.url)
    if webapp_url:
        b.button(text=WEBAPP_TEXT, web_app=webapp_info(webapp_url))
    b.button(text="✅ Проверить подписку", callback_data=CHECK_CB)
    b.adjust(1)
    return b.as_markup()


# ---------- Админка ----------


def admin_menu_kb(new_sponsors: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 Спонсоры", callback_data=AdminCb(action="sponsors"))
    b.button(text="➕ Добавить спонсора", callback_data=AdminCb(action="add"))
    b.button(text="👥 Участники", callback_data=PageCb(page=0))
    b.button(text="🔎 Найти участника", callback_data=AdminCb(action="find"))
    b.button(text="📥 Выгрузить участников (CSV)", callback_data=AdminCb(action="export"))
    b.button(
        text=f"📣 Рассылка о новых спонсорах ({new_sponsors})" if new_sponsors else "📣 Рассылка",
        callback_data=AdminCb(action="bc"),
    )
    b.button(text="⚙️ Настройки приложения", callback_data=AdminCb(action="bg"))
    b.adjust(2, 2, 1, 1, 1)
    return b.as_markup()


def broadcast_kb(recipients: int | None) -> InlineKeyboardMarkup:
    """recipients — сколько людей получат рассылку; None — рассылать нечего."""
    b = InlineKeyboardBuilder()
    if recipients is not None:
        b.button(text=f"📣 Разослать ({recipients})", callback_data=AdminCb(action="bc_go"))
        b.button(text="👀 Как это увидят", callback_data=AdminCb(action="bc_preview"))
        b.button(text="« Позже", callback_data=AdminCb(action="menu"))
        b.adjust(1, 2)
    else:
        b.button(text="« В меню", callback_data=AdminCb(action="menu"))
    return b.as_markup()


def broadcast_stop_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⏹ Остановить рассылку", callback_data=AdminCb(action="bc_stop"))
    return b.as_markup()


def appearance_kb(has_background: bool, captcha: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="📤 Загрузить новый фон" if has_background else "📤 Загрузить фон",
        callback_data=AdminCb(action="bg_upload"),
    )
    if has_background:
        b.button(text="🗑 Убрать фон", callback_data=AdminCb(action="bg_delete"))
    b.button(text="🧩 Выключить капчу" if captcha else "🧩 Включить капчу", callback_data=AdminCb(action="captcha"))
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


def sponsor_card_kb(sponsor_id: int, has_background: bool = False, has_avatar: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💰 Изменить сумму", callback_data=SponsorCb(action="amount", id=sponsor_id))
    b.button(text="🖼 Аватарка", callback_data=SponsorCb(action="avatar", id=sponsor_id))
    b.button(text="🌄 Фон капсулы", callback_data=SponsorCb(action="bg", id=sponsor_id))
    rows = [1, 2]
    removals = []
    if has_avatar:
        removals.append(("🚫 Убрать аватарку", "avatar_delete"))
    if has_background:
        removals.append(("🚫 Убрать фон", "bg_delete"))
    for text, action in removals:
        b.button(text=text, callback_data=SponsorCb(action=action, id=sponsor_id))
    if removals:
        rows.append(len(removals))
    b.button(text="🗑 Удалить спонсора", callback_data=SponsorCb(action="delete", id=sponsor_id))
    b.button(text="« К списку спонсоров", callback_data=AdminCb(action="sponsors"))
    b.adjust(*rows, 1, 1)
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


def choose_source_kb() -> ReplyKeyboardMarkup:
    return _reply_kb(
        [
            KeyboardButton(
                text="📢 Выбрать канал", request_chat=KeyboardButtonRequestChat(request_id=1, chat_is_channel=True)
            ),
            KeyboardButton(
                text="💬 Выбрать чат", request_chat=KeyboardButtonRequestChat(request_id=2, chat_is_channel=False)
            ),
        ],
        [
            KeyboardButton(
                text="🤖 Выбрать бота",
                request_users=KeyboardButtonRequestUsers(request_id=3, user_is_bot=True, request_username=True),
            )
        ],
        [KeyboardButton(text=CANCEL_TEXT)],
    )


def pick_chat_kb(chats: list[tuple[int, str, str]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for chat_id, title, chat_type in chats:
        icon = KIND_ICONS[KIND_CHANNEL if chat_type == "channel" else KIND_CHAT]
        b.button(text=f"{icon} {title[:48]}", callback_data=PickChatCb(chat_id=chat_id))
    b.adjust(1)
    return b.as_markup()


def url_kb(has_suggested: bool) -> ReplyKeyboardMarkup:
    if not has_suggested:
        return cancel_kb()
    return _reply_kb([KeyboardButton(text=KEEP_URL_TEXT)], [KeyboardButton(text=CANCEL_TEXT)])


def amount_kb(editing: bool) -> ReplyKeyboardMarkup:
    first = REMOVE_AMOUNT_TEXT if editing else SKIP_TEXT
    return _reply_kb([KeyboardButton(text=first)], [KeyboardButton(text=CANCEL_TEXT)])
