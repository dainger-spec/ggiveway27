from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat

USER_COMMANDS = [BotCommand(command="start", description="Участвовать в розыгрыше")]
ADMIN_COMMANDS = [
    *USER_COMMANDS,
    BotCommand(command="admin", description="Админ-панель"),
    BotCommand(command="cancel", description="Отменить действие"),
]


async def set_admin_commands(bot: Bot, admin_id: int) -> bool:
    """Показывает /admin в меню команд админа. Не сработает, пока админ не нажал /start."""
    try:
        await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
    except TelegramAPIError:
        return False
    return True
