import asyncio
import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import ChatMemberRestricted

from .database import Sponsor

log = logging.getLogger(__name__)

_MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}
_ADMIN_STATUSES = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


async def is_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Состоит ли пользователь в канале/чате. Бот должен быть там администратором."""
    for _ in range(3):
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            continue
        except TelegramAPIError as e:
            log.error("Не удалось проверить пользователя %s в чате %s: %s", user_id, chat_id, e)
            return False
        if isinstance(member, ChatMemberRestricted):
            return member.is_member
        return member.status in _MEMBER_STATUSES
    return False


async def find_missing(bot: Bot, sponsors: list[Sponsor], user_id: int) -> list[Sponsor]:
    """Спонсоры (каналы и чаты), на которые пользователь ещё не подписан."""
    checkable = [s for s in sponsors if s.checkable]
    results = await asyncio.gather(*(is_member(bot, s.chat_id, user_id) for s in checkable))
    return [s for s, ok in zip(checkable, results, strict=True) if not ok]


async def bot_is_admin(bot: Bot, chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
    except TelegramAPIError:
        return False
    return member.status in _ADMIN_STATUSES
