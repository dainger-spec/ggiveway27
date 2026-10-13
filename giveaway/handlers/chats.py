"""Запоминаем каналы и чаты, куда бота назначили администратором."""

from aiogram import Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import ChatMemberUpdated

from ..database import Database

router = Router(name="chats")

_ADMIN = {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


@router.my_chat_member()
async def bot_status_changed(event: ChatMemberUpdated, db: Database) -> None:
    chat = event.chat
    if chat.type == ChatType.PRIVATE:
        return
    if event.new_chat_member.status in _ADMIN:
        await db.remember_chat(chat.id, chat.title or str(chat.id), chat.type)
    else:
        await db.forget_chat(chat.id)
