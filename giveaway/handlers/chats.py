"""Запоминаем каналы и чаты, где бот администратор, заявки на вступление в них и кто заблокировал бота."""

from aiogram import Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import ChatJoinRequest, ChatMemberUpdated

from ..database import Database

router = Router(name="chats")

_ADMIN = {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


@router.my_chat_member()
async def bot_status_changed(event: ChatMemberUpdated, db: Database) -> None:
    chat = event.chat
    if chat.type == ChatType.PRIVATE:  # пользователь заблокировал или разблокировал бота
        if event.new_chat_member.status == ChatMemberStatus.KICKED:
            await db.mark_blocked(chat.id)
        else:
            await db.touch_user(chat.id)
        return
    if event.new_chat_member.status in _ADMIN:
        await db.remember_chat(chat.id, chat.title or str(chat.id), chat.type)
    else:
        await db.forget_chat(chat.id)


@router.chat_join_request()
async def join_requested(request: ChatJoinRequest, db: Database) -> None:
    """Заявку в канал/чат «по заявкам» засчитываем как выполненное условие (одобрять её — решение спонсора)."""
    await db.add_join_request(request.chat.id, request.from_user.id)
