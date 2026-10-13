import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import MenuButtonDefault, MenuButtonWebApp

from giveaway.commands import USER_COMMANDS, set_admin_commands
from giveaway.config import Config, load_config
from giveaway.database import Database
from giveaway.handlers import admin, user
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


def build_dispatcher(config: Config, db: Database) -> Dispatcher:
    dp = Dispatcher(config=config, db=db)
    # Порядок важен: админские диалоги должны получать сообщения раньше пользовательских.
    dp.include_routers(admin.router, user.router)
    dp.startup.register(set_commands)
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()

    db = Database(config.db_path)
    await db.connect()

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
