import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Корень проекта (папка с main.py): там лежат .env и база данных,
# независимо от того, из какой папки хостинг запускает бота.
BASE_DIR = Path(__file__).resolve().parent.parent

# Админы по умолчанию — если переменная ADMIN_IDS не задана на хостинге.
DEFAULT_ADMIN_IDS = "1457986905"


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: str
    giveaway_name: str


def load_config() -> Config:
    load_dotenv(BASE_DIR / ".env")

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN: укажите его в переменных окружения хостинга или в файле .env")

    raw_admins = (os.getenv("ADMIN_IDS", "").strip() or DEFAULT_ADMIN_IDS).replace(" ", "")
    try:
        admin_ids = frozenset(int(x) for x in raw_admins.split(",") if x)
    except ValueError:
        raise RuntimeError("ADMIN_IDS должен содержать числовые Telegram ID через запятую") from None
    if not admin_ids:
        raise RuntimeError("Не задан ADMIN_IDS: укажите Telegram ID админов через запятую")

    db_path = Path(os.getenv("DB_PATH", "").strip() or "giveaway.db")
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        db_path=str(db_path),
        giveaway_name=os.getenv("GIVEAWAY_NAME", "").strip() or "Europe Private Giveaway",
    )
