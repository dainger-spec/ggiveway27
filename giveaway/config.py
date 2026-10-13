import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Корень проекта (папка с main.py), независимо от того, из какой папки хостинг запускает бота.
BASE_DIR = Path(__file__).resolve().parent.parent

# Админы по умолчанию — если переменная ADMIN_IDS не задана на хостинге.
DEFAULT_ADMIN_IDS = "1457986905"
# Адрес мини-приложения по умолчанию — если не заданы WEBAPP_URL и DOMAIN.
DEFAULT_WEBAPP_URL = "https://giveaway.bothost.tech"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: str
    giveaway_name: str
    media_dir: str
    webapp_url: str | None  # публичный HTTPS-адрес мини-приложения
    web_host: str
    web_port: int
    data_dir: str = ""  # постоянная папка (на Bothost — /app/data)

    @property
    def db_in_data_dir(self) -> bool:
        """База лежит в постоянной папке и переживёт обновление бота."""
        return bool(self.data_dir) and Path(self.db_path).resolve().is_relative_to(Path(self.data_dir).resolve())


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

    # Bothost сохраняет между обновлениями только папку data (DATA_DIR=/app/data),
    # поэтому база и файлы лежат там.
    data_dir = Path(os.getenv("DATA_DIR", "").strip() or BASE_DIR / "data")
    if not data_dir.is_absolute():
        data_dir = BASE_DIR / data_dir

    db_path = Path(os.getenv("DB_PATH", "").strip() or data_dir / "giveaway.db")
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    media_dir = Path(os.getenv("MEDIA_DIR", "").strip() or data_dir / "media")
    if not media_dir.is_absolute():
        media_dir = BASE_DIR / media_dir

    webapp_url = (os.getenv("WEBAPP_URL", "").strip() or os.getenv("DOMAIN", "").strip() or DEFAULT_WEBAPP_URL).rstrip(
        "/"
    )
    if "://" not in webapp_url:
        webapp_url = "https://" + webapp_url  # Bothost отдаёт DOMAIN без протокола
    if not webapp_url.startswith("https://"):
        log.error("Адрес мини-приложения должен начинаться с https:// — мини-приложение отключено")
        webapp_url = None

    raw_port = os.getenv("WEB_PORT", "").strip() or os.getenv("PORT", "").strip() or "8080"
    try:
        web_port = int(raw_port)
    except ValueError:
        raise RuntimeError("WEB_PORT/PORT должен быть числом") from None

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        db_path=str(db_path),
        giveaway_name=os.getenv("GIVEAWAY_NAME", "").strip() or "Europe Private Giveaway",
        media_dir=str(media_dir),
        webapp_url=webapp_url,
        web_host=os.getenv("WEB_HOST", "").strip() or "0.0.0.0",
        web_port=web_port,
        data_dir=str(data_dir),
    )
