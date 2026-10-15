import asyncio
import logging
import shutil
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import MenuButtonDefault, MenuButtonWebApp

from giveaway.broadcast import Broadcaster
from giveaway.commands import USER_COMMANDS, set_admin_commands
from giveaway.config import BASE_DIR, Config, load_config
from giveaway.database import Database
from giveaway.handlers import admin, chats, user
from giveaway.keyboards import webapp_info
from giveaway.webapp.server import start_web_server

log = logging.getLogger(__name__)


async def set_commands(bot: Bot, config: Config) -> None:
    await bot.set_my_commands(USER_COMMANDS)
    for admin_id in config.admin_ids:
        if not await set_admin_commands(bot, admin_id):
            log.warning("Админ %s ещё не нажимал /start в боте — меню команд обновится после /start", admin_id)

    # Кнопка мини-приложения слева от поля ввода у всех пользователей.
    if config.webapp_url:
        button = MenuButtonWebApp(text="🎁 Гив", web_app=webapp_info(config.webapp_url))
    else:
        button = MenuButtonDefault()
    try:
        await bot.set_chat_menu_button(menu_button=button)
    except TelegramAPIError as e:
        log.warning("Не удалось установить кнопку мини-приложения: %s", e)


def adopt_legacy_db(db_path: str) -> None:
    """Раньше база лежала в корне проекта; переносим её в постоянную папку, если там ещё пусто."""
    target = Path(db_path)
    legacy = BASE_DIR / "giveaway.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or not legacy.exists() or legacy.resolve() == target.resolve():
        return
    for suffix in ("", "-wal", "-shm"):
        src = legacy.with_name(legacy.name + suffix)
        if src.exists():
            shutil.copy2(src, target.with_name(target.name + suffix))
    log.info("База перенесена из %s в %s", legacy, target)


def build_dispatcher(config: Config, db: Database) -> Dispatcher:
    dp = Dispatcher(config=config, db=db, broadcaster=Broadcaster())
    # Порядок важен: админские диалоги должны получать сообщения раньше пользовательских.
    dp.include_routers(chats.router, admin.router, user.router)
    dp.startup.register(set_commands)
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()

    adopt_legacy_db(config.db_path)
    db = Database(config.db_path)
    await db.connect()
    sponsors, participants = await db.get_sponsors(), await db.count_participants()
    log.info("База данных: %s — спонсоров: %d, участников: %d", config.db_path, len(sponsors), participants)
    if not config.db_in_data_dir:
        log.warning("База лежит вне папки %s — на Bothost она пропадёт при обновлении бота!", config.data_dir)

    bot = Bot(
        config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = build_dispatcher(config, db)
    web_runner = await start_web_server(bot, db, config)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if web_runner is not None:
            await web_runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
