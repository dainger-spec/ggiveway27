import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from giveaway.commands import USER_COMMANDS, set_admin_commands
from giveaway.config import Config, load_config
from giveaway.database import Database
from giveaway.handlers import admin, user

log = logging.getLogger(__name__)


async def set_commands(bot: Bot, config: Config) -> None:
    await bot.set_my_commands(USER_COMMANDS)
    for admin_id in config.admin_ids:
        if not await set_admin_commands(bot, admin_id):
            log.warning("Админ %s ещё не нажимал /start в боте — меню команд обновится после /start", admin_id)


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
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
