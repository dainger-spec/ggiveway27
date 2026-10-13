"""Фон мини-приложения (картинка, GIF или видео) и аватарки спонсоров."""

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from .database import Database, Sponsor

log = logging.getLogger(__name__)

BG_KIND, BG_FILE, BG_VERSION = "bg_kind", "bg_file", "bg_version"
SNOW, NEW_YEAR = "snow", "new_year"  # оформление мини-приложения: «1» — включено
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # больше Bot API скачать не даёт
AVATAR_TTL = 24 * 3600

_IMAGE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
_VIDEO_EXT = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}

_avatar_locks: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class Background:
    kind: str  # image | video
    path: Path
    version: str


async def get_background(db: Database, media_dir: str) -> Background | None:
    kind, name = await db.get_setting(BG_KIND), await db.get_setting(BG_FILE)
    if not kind or not name:
        return None
    path = Path(media_dir) / name
    if not path.is_file():
        return None
    return Background(kind=kind, path=path, version=await db.get_setting(BG_VERSION) or "0")


def _background_source(message: Message) -> tuple[str, int | None, str, str] | None:
    """(file_id, размер, image|video, расширение) из фото, GIF, видео или файла."""
    if message.photo:
        photo = message.photo[-1]
        return photo.file_id, photo.file_size, "image", ".jpg"
    if message.animation:  # GIF Telegram присылает как беззвучное MP4
        anim = message.animation
        if anim.mime_type == "image/gif":
            return anim.file_id, anim.file_size, "image", ".gif"
        return anim.file_id, anim.file_size, "video", _VIDEO_EXT.get(anim.mime_type or "", ".mp4")
    if message.video:
        video = message.video
        return video.file_id, video.file_size, "video", _VIDEO_EXT.get(video.mime_type or "", ".mp4")
    if message.document:
        doc, mime = message.document, message.document.mime_type or ""
        if mime in _IMAGE_EXT:
            return doc.file_id, doc.file_size, "image", _IMAGE_EXT[mime]
        if mime in _VIDEO_EXT:
            return doc.file_id, doc.file_size, "video", _VIDEO_EXT[mime]
    return None


class MediaError(Exception):
    """Понятная админу причина, почему файл не подошёл."""


async def download_media(bot: Bot, media_dir: str, message: Message, prefix: str) -> tuple[str, str]:
    """Скачивает картинку, GIF или видео из сообщения. Возвращает (image|video, имя файла)."""
    source = _background_source(message)
    if source is None:
        raise MediaError("Пришлите картинку, GIF или видео (MP4).")
    file_id, size, kind, ext = source
    if size and size > MAX_DOWNLOAD_BYTES:
        raise MediaError(
            "Файл больше 20 МБ — Telegram не даёт ботам скачивать такие файлы. Сожмите его и пришлите снова."
        )

    folder = Path(media_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}_{time.time_ns() // 1_000_000}{ext}"
    try:
        await bot.download(file_id, destination=folder / name)
    except TelegramAPIError as e:
        raise MediaError(f"Не удалось скачать файл: {e.message}") from None
    return kind, name


def _remove_file(media_dir: str, name: str | None) -> None:
    if name:
        (Path(media_dir) / name).unlink(missing_ok=True)


async def save_background(bot: Bot, db: Database, media_dir: str, message: Message) -> str | None:
    """Сохраняет фон всего мини-приложения. Возвращает текст ошибки или None."""
    try:
        kind, name = await download_media(bot, media_dir, message, "background")
    except MediaError as e:
        return str(e)
    old = await db.get_setting(BG_FILE)
    await db.set_settings({BG_KIND: kind, BG_FILE: name, BG_VERSION: name.rsplit("_", 1)[-1].split(".")[0]})
    if old != name:
        _remove_file(media_dir, old)
    return None


async def save_sponsor_background(
    bot: Bot, db: Database, media_dir: str, sponsor: Sponsor, message: Message
) -> str | None:
    """Сохраняет фон капсулы спонсора. Возвращает текст ошибки или None."""
    try:
        kind, name = await download_media(bot, media_dir, message, f"sponsor{sponsor.id}")
    except MediaError as e:
        return str(e)
    await db.set_sponsor_background(sponsor.id, kind, name)
    _remove_file(media_dir, sponsor.bg_file)
    return None


async def remove_sponsor_background(db: Database, media_dir: str, sponsor: Sponsor) -> None:
    await db.set_sponsor_background(sponsor.id, None, None)
    _remove_file(media_dir, sponsor.bg_file)


async def save_sponsor_avatar(bot: Bot, db: Database, media_dir: str, sponsor: Sponsor, message: Message) -> str | None:
    """Сохраняет свою аватарку спонсора. Возвращает текст ошибки или None."""
    source = _background_source(message)
    if source is None or source[2] != "image":
        return "Для аватарки пришлите картинку: фото или файл JPG / PNG / WebP / GIF."
    try:
        _, name = await download_media(bot, media_dir, message, f"avatar{sponsor.id}")
    except MediaError as e:
        return str(e)
    await db.set_sponsor_avatar(sponsor.id, name)
    _remove_file(media_dir, sponsor.avatar_file)
    return None


async def remove_sponsor_avatar(db: Database, media_dir: str, sponsor: Sponsor) -> None:
    await db.set_sponsor_avatar(sponsor.id, None)
    _remove_file(media_dir, sponsor.avatar_file)


def sponsor_background_path(media_dir: str, sponsor: Sponsor) -> Path | None:
    if not sponsor.bg_file:
        return None
    path = Path(media_dir) / sponsor.bg_file
    return path if path.is_file() else None


async def remove_background(db: Database, media_dir: str) -> None:
    name = await db.get_setting(BG_FILE)
    await db.delete_settings(BG_KIND, BG_FILE, BG_VERSION)
    _remove_file(media_dir, name)


def _fresh(path: Path) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < AVATAR_TTL


async def sponsor_avatar(bot: Bot, media_dir: str, sponsor: Sponsor) -> Path | None:
    """Своя аватарка спонсора, иначе фото канала/чата (скачивается через бота, кэш на сутки)."""
    if sponsor.avatar_file and (custom := Path(media_dir) / sponsor.avatar_file).is_file():
        return custom
    if not sponsor.checkable:
        return None
    path = Path(media_dir) / "avatars" / f"{abs(sponsor.chat_id)}.jpg"
    no_photo = path.with_suffix(".none")  # помним, что у чата нет фото

    async with _avatar_locks.setdefault(sponsor.chat_id, asyncio.Lock()):
        if _fresh(path):
            return path
        if _fresh(no_photo):
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            chat = await bot.get_chat(sponsor.chat_id)
            if chat.photo is None:
                no_photo.touch()
                path.unlink(missing_ok=True)
                return None
            await bot.download(chat.photo.small_file_id, destination=path)
        except TelegramAPIError as e:
            log.warning("Не удалось получить аватарку %s: %s", sponsor.chat_id, e)
            return path if path.exists() else None
        no_photo.unlink(missing_ok=True)
        return path
