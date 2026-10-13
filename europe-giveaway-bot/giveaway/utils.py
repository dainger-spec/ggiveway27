from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from .config import Config


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery, config: Config) -> bool:
        return event.from_user is not None and event.from_user.id in config.admin_ids


def not_command(message: Message) -> bool:
    """Пропускает команды (/start, /admin…) мимо пошаговых диалогов админки."""
    return not (message.text or "").startswith("/")


async def show(cb: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Обновляет сообщение с кнопками, а если это невозможно — присылает новое."""
    if isinstance(cb.message, Message):
        try:
            await cb.message.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
    await cb.bot.send_message(cb.from_user.id, text, reply_markup=reply_markup)
