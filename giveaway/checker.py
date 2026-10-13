import asyncio
import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import ChatMemberAdministrator, ChatMemberRestricted

from .database import Database, Sponsor

log = logging.getLogger(__name__)

_MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}


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


async def find_missing(bot: Bot, db: Database, sponsors: list[Sponsor], user_id: int) -> list[Sponsor]:
    """Каналы и чаты, где пользователь не состоит и не подавал заявку на вступление."""
    checkable = [s for s in sponsors if s.checkable]
    if not checkable:
        return []
    requested = await db.join_requested_chats(user_id)  # заявка в чат «по заявкам» засчитывается
    to_check = [s for s in checkable if s.chat_id not in requested]
    results = await asyncio.gather(*(is_member(bot, s.chat_id, user_id) for s in to_check))
    return [s for s, ok in zip(to_check, results, strict=True) if not ok]


async def bot_rights(bot: Bot, chat_id: int) -> tuple[bool, bool]:
    """(бот — администратор, бот получает заявки на вступление)."""
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
    except TelegramAPIError:
        return False, False
    if member.status == ChatMemberStatus.CREATOR:
        return True, True
    if isinstance(member, ChatMemberAdministrator):
        return True, bool(member.can_invite_users)
    return False, False


async def bot_is_admin(bot: Bot, chat_id: int) -> bool:
    return (await bot_rights(bot, chat_id))[0]
