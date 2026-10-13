"""Распознаёт, что админ прислал как спонсора: ссылку, @username, ID, пересланный пост или выбор кнопкой."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl

from aiogram.types import Message, MessageOriginChannel, MessageOriginChat, MessageOriginUser

USERNAME = r"[A-Za-z][A-Za-z0-9_]{3,31}"
_TG_LINK = re.compile(r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(\S+)", re.I)
_TG_SCHEME = re.compile(r"tg://(resolve|join)\?(\S+)", re.I)
_MENTION = re.compile(rf"(?<![\w@/])@({USERNAME})\b")
_TRAILING = ".,;:!?)]}»\"'"


@dataclass(frozen=True)
class SponsorRef:
    chat_id: int | None = None  # канал/чат с известным ID
    username: str | None = None  # публичный @username канала, чата или бота
    invite_url: str | None = None  # ссылка-приглашение в приватный канал/чат
    link: str | None = None  # ссылка как прислали (для ботов сохраняет ?start=)
    is_bot: bool = False  # точно бот: выбран кнопкой или переслано от бота


def _public(username: str, link: str | None = None, *, is_bot: bool = False) -> SponsorRef:
    return SponsorRef(username=username, link=link or f"https://t.me/{username}", is_bot=is_bot)


def _from_path(path: str) -> SponsorRef | None:
    """Путь после t.me/: «name», «name/123», «+hash», «joinchat/hash», «c/123/45», «s/name»."""
    path, _, query = path.rstrip(_TRAILING).partition("?")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    head = parts[0]
    if head.startswith("+") and len(head) > 1:
        return SponsorRef(invite_url=f"https://t.me/{head}")
    if head.lower() == "joinchat" and len(parts) > 1:
        return SponsorRef(invite_url=f"https://t.me/+{parts[1]}")
    if head.lower() == "c" and len(parts) > 1 and parts[1].isdigit():
        return SponsorRef(chat_id=int(f"-100{parts[1]}"))  # ссылка на пост приватного канала
    if head.lower() == "s" and len(parts) > 1:
        parts = parts[1:]  # веб-превью канала
        head = parts[0]
    if not re.fullmatch(USERNAME, head):
        return None
    link = "https://t.me/" + "/".join(parts) + (f"?{query}" if query else "")
    return _public(head, link)


def parse_text(text: str) -> SponsorRef | None:
    text = text.strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d{5,}", text):
        return SponsorRef(chat_id=int(text))
    if m := _TG_SCHEME.search(text):
        params = dict(parse_qsl(m.group(2)))
        if m.group(1).lower() == "join" and params.get("invite"):
            return SponsorRef(invite_url=f"https://t.me/+{params['invite']}")
        domain = params.get("domain", "")
        if re.fullmatch(USERNAME, domain):
            start = params.get("start")
            return _public(domain, f"https://t.me/{domain}" + (f"?start={start}" if start else ""))
    if m := _TG_LINK.search(text):
        if ref := _from_path(m.group(1)):
            return ref
    if m := _MENTION.search(text):
        return _public(m.group(1))
    if re.fullmatch(USERNAME, text):
        return _public(text)
    return None


def parse_message(message: Message) -> SponsorRef | None:
    if message.chat_shared:
        return SponsorRef(chat_id=message.chat_shared.chat_id)
    if message.users_shared and message.users_shared.users:
        user = message.users_shared.users[0]
        return _public(user.username, is_bot=True) if user.username else None

    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        return SponsorRef(chat_id=origin.chat.id)
    if isinstance(origin, MessageOriginChat):
        return SponsorRef(chat_id=origin.sender_chat.id)
    if isinstance(origin, MessageOriginUser) and origin.sender_user.is_bot and origin.sender_user.username:
        return _public(origin.sender_user.username, is_bot=True)

    # Ссылки могут прятаться под текстом (text_link) или стоять посреди фразы.
    text = message.text or message.caption or ""
    candidates = []
    for entity in message.entities or message.caption_entities or []:
        if entity.type == "text_link" and entity.url:
            candidates.append(entity.url)
        elif entity.type in ("url", "mention"):
            candidates.append(entity.extract_from(text))
    for candidate in [*candidates, text]:
        if ref := parse_text(candidate):
            return ref
    return None
