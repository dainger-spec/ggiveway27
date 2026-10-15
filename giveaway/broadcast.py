"""Рассылка о новых спонсорах всем пользователям бота: идёт в фоне, прогресс видно у админа."""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from . import keyboards as kb
from . import texts
from .config import Config
from .database import Database, Sponsor

log = logging.getLogger(__name__)

RATE = 25  # сообщений в секунду — Telegram разрешает боту около 30
PROGRESS_EVERY = 5.0  # как часто обновлять прогресс у админа, секунд
UNREACHABLE = ("chat not found", "user is deactivated", "bot can't initiate conversation")


@dataclass
class Report:
    total: int
    sent: int = 0
    blocked: int = 0  # заблокировали бота или никогда его не запускали
    failed: int = 0
    stopped: bool = False
    started: float = field(default_factory=time.monotonic)

    @property
    def done(self) -> int:
        return self.sent + self.blocked + self.failed

    @property
    def seconds(self) -> int:
        return int(time.monotonic() - self.started)


class Broadcaster:
    """Одна рассылка за раз. Хранится в диспетчере и доступна обработчикам как `broadcaster`."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(
        self,
        bot: Bot,
        db: Database,
        config: Config,
        sponsors: list[Sponsor],
        recipients: list[tuple[int, int | None]],
        status: tuple[int, int],
    ) -> bool:
        """Запускает рассылку; status — (chat_id, message_id) сообщения админа с прогрессом."""
        if self.running:
            return False
        self._stopping = False
        self._task = asyncio.create_task(self._run(bot, db, config, sponsors, recipients, list(status)))
        return True

    def stop(self) -> bool:
        if not self.running:
            return False
        self._stopping = True
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)

    async def _run(
        self,
        bot: Bot,
        db: Database,
        config: Config,
        sponsors: list[Sponsor],
        recipients: list[tuple[int, int | None]],
        status: list[int],
    ) -> None:
        await db.mark_announced([s.id for s in sponsors])  # сразу, чтобы второй раз не разослать
        report = Report(total=len(recipients))
        keyboard = kb.new_sponsors_kb(sponsors, config.webapp_url)
        last_progress = time.monotonic()
        log.info("Рассылка о новых спонсорах: %d получателей", report.total)
        try:
            for user_id, number in recipients:
                if self._stopping:
                    report.stopped = True
                    break
                text = texts.new_sponsors(sponsors, config.giveaway_name, number)
                await _deliver(bot, db, user_id, text, keyboard, report)
                await asyncio.sleep(1 / RATE)
                if time.monotonic() - last_progress >= PROGRESS_EVERY:
                    last_progress = time.monotonic()
                    await _edit(bot, status, texts.broadcast_progress(report), kb.broadcast_stop_kb())
        except Exception:
            log.exception("Рассылка прервалась с ошибкой")
            report.stopped = True
        finally:
            log.info(
                "Рассылка закончена: доставлено %d, недоступны %d, ошибки %d из %d",
                report.sent,
                report.blocked,
                report.failed,
                report.total,
            )
            await _edit(bot, status, texts.broadcast_report(report), kb.back_to_menu_kb())


async def _deliver(
    bot: Bot, db: Database, user_id: int, text: str, keyboard: InlineKeyboardMarkup, report: Report
) -> None:
    for _ in range(3):
        try:
            await bot.send_message(user_id, text, reply_markup=keyboard)
            report.sent += 1
            return
        except TelegramRetryAfter as e:  # Telegram просит притормозить
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            report.blocked += 1
            await db.mark_blocked(user_id)
            return
        except TelegramBadRequest as e:
            if any(reason in str(e).lower() for reason in UNREACHABLE):
                report.blocked += 1
                await db.mark_blocked(user_id)
            else:
                log.warning("Рассылка: не удалось отправить %s: %s", user_id, e)
                report.failed += 1
            return
        except TelegramAPIError as e:
            log.warning("Рассылка: не удалось отправить %s: %s", user_id, e)
            report.failed += 1
            return
    report.failed += 1


async def _edit(bot: Bot, status: list[int], text: str, keyboard: InlineKeyboardMarkup) -> None:
    """Обновляет сообщение с прогрессом; если его удалили — присылает новое и дальше обновляет его."""
    chat_id, message_id = status
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=keyboard)
        return
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
    except Exception as e:  # прогресс — не главное, рассылка из-за него не должна падать
        log.warning("Рассылка: не удалось обновить прогресс: %s", e)
        return
    try:
        status[1] = (await bot.send_message(chat_id, text, reply_markup=keyboard)).message_id
    except Exception as e:
        log.warning("Рассылка: не удалось отправить прогресс: %s", e)
