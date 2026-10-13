import asyncio
from dataclasses import dataclass

import aiosqlite

KIND_CHANNEL = "channel"
KIND_CHAT = "chat"
KIND_BOT = "bot"
KINDS = (KIND_CHANNEL, KIND_CHAT, KIND_BOT)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sponsors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL CHECK (kind IN ('channel', 'chat', 'bot')),
    title      TEXT    NOT NULL,
    url        TEXT    NOT NULL,
    chat_id    INTEGER,
    amount     TEXT,
    bg_kind    TEXT,  -- фон капсулы в мини-приложении: image | video
    bg_file    TEXT,
    avatar_file TEXT,  -- своя аватарка спонсора (вместо фото канала)
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS participants (
    user_id    INTEGER PRIMARY KEY,
    number     INTEGER NOT NULL UNIQUE,
    username   TEXT,
    full_name  TEXT    NOT NULL DEFAULT '',
    joined_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Заявки на вступление в чаты/каналы по заявкам: поданная заявка засчитывается как выполненное условие
CREATE TABLE IF NOT EXISTS join_requests (
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, user_id)
);

-- Каналы и чаты, где бот стал администратором (нужны, чтобы узнать приватный канал по ссылке-приглашению)
CREATE TABLE IF NOT EXISTS known_chats (
    chat_id    INTEGER PRIMARY KEY,
    title      TEXT    NOT NULL,
    type       TEXT    NOT NULL,
    updated_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_SPONSOR_COLUMNS = "id, kind, title, url, chat_id, amount, bg_kind, bg_file, avatar_file"
# Каналы, затем чаты, затем боты; внутри группы — в порядке добавления.
_SPONSOR_ORDER = "CASE kind WHEN 'channel' THEN 0 WHEN 'chat' THEN 1 ELSE 2 END, id"


@dataclass(frozen=True)
class Sponsor:
    id: int
    kind: str
    title: str
    url: str
    chat_id: int | None
    amount: str | None
    bg_kind: str | None = None  # фон капсулы: image | video
    bg_file: str | None = None
    avatar_file: str | None = None  # своя аватарка, загруженная админом

    @property
    def checkable(self) -> bool:
        """Можно ли проверить выполнение условия через Telegram API."""
        return self.kind in (KIND_CHANNEL, KIND_CHAT) and self.chat_id is not None


@dataclass(frozen=True)
class Participant:
    user_id: int
    number: int
    username: str | None
    full_name: str
    joined_at: str


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._register_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не подключена")
        return self._conn

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Добавляет новые колонки в базу, созданную старой версией бота."""
        async with self.conn.execute("PRAGMA table_info(sponsors)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        for column in ("bg_kind", "bg_file", "avatar_file"):
            if column not in columns:
                await self.conn.execute(f"ALTER TABLE sponsors ADD COLUMN {column} TEXT")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ---------- Спонсоры ----------

    async def add_sponsor(self, kind: str, title: str, url: str, chat_id: int | None, amount: str | None) -> int:
        cur = await self.conn.execute(
            "INSERT INTO sponsors (kind, title, url, chat_id, amount) VALUES (?, ?, ?, ?, ?)",
            (kind, title, url, chat_id, amount),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_sponsors(self) -> list[Sponsor]:
        async with self.conn.execute(f"SELECT {_SPONSOR_COLUMNS} FROM sponsors ORDER BY {_SPONSOR_ORDER}") as cur:
            rows = await cur.fetchall()
        return [Sponsor(**dict(row)) for row in rows]

    async def get_sponsor(self, sponsor_id: int) -> Sponsor | None:
        async with self.conn.execute(
            f"SELECT {_SPONSOR_COLUMNS} FROM sponsors WHERE id = ?",
            (sponsor_id,),
        ) as cur:
            row = await cur.fetchone()
        return Sponsor(**dict(row)) if row else None

    async def has_chat(self, chat_id: int) -> bool:
        async with self.conn.execute("SELECT 1 FROM sponsors WHERE chat_id = ?", (chat_id,)) as cur:
            return await cur.fetchone() is not None

    async def update_amount(self, sponsor_id: int, amount: str | None) -> bool:
        cur = await self.conn.execute("UPDATE sponsors SET amount = ? WHERE id = ?", (amount, sponsor_id))
        await self.conn.commit()
        return cur.rowcount > 0

    async def set_sponsor_background(self, sponsor_id: int, kind: str | None, file: str | None) -> None:
        await self.conn.execute("UPDATE sponsors SET bg_kind = ?, bg_file = ? WHERE id = ?", (kind, file, sponsor_id))
        await self.conn.commit()

    async def set_sponsor_avatar(self, sponsor_id: int, file: str | None) -> None:
        await self.conn.execute("UPDATE sponsors SET avatar_file = ? WHERE id = ?", (file, sponsor_id))
        await self.conn.commit()

    async def delete_sponsor(self, sponsor_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM sponsors WHERE id = ?", (sponsor_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    # ---------- Заявки на вступление ----------

    async def add_join_request(self, chat_id: int, user_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO join_requests (chat_id, user_id) VALUES (?, ?)", (chat_id, user_id)
        )
        await self.conn.commit()

    async def join_requested_chats(self, user_id: int) -> set[int]:
        async with self.conn.execute("SELECT chat_id FROM join_requests WHERE user_id = ?", (user_id,)) as cur:
            return {row["chat_id"] for row in await cur.fetchall()}

    # ---------- Чаты, где бот администратор ----------

    async def remember_chat(self, chat_id: int, title: str, chat_type: str) -> None:
        await self.conn.execute(
            "INSERT INTO known_chats (chat_id, title, type) VALUES (?, ?, ?) ON CONFLICT(chat_id) DO UPDATE "
            "SET title = excluded.title, type = excluded.type, updated_at = CURRENT_TIMESTAMP",
            (chat_id, title, chat_type),
        )
        await self.conn.commit()

    async def forget_chat(self, chat_id: int) -> None:
        await self.conn.execute("DELETE FROM known_chats WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    async def known_chats_without_sponsor(self) -> list[tuple[int, str, str]]:
        """(chat_id, title, type) чатов, где бот админ и которые ещё не добавлены в спонсоры."""
        async with self.conn.execute(
            "SELECT chat_id, title, type FROM known_chats "
            "WHERE chat_id NOT IN (SELECT chat_id FROM sponsors WHERE chat_id IS NOT NULL) "
            "ORDER BY updated_at DESC LIMIT 30"
        ) as cur:
            return [tuple(row) for row in await cur.fetchall()]

    # ---------- Настройки ----------

    async def get_setting(self, key: str) -> str | None:
        async with self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    async def get_flag(self, key: str, default: bool = True) -> bool:
        value = await self.get_setting(key)
        return default if value is None else value == "1"

    async def set_settings(self, values: dict[str, str]) -> None:
        await self.conn.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(values.items()),
        )
        await self.conn.commit()

    async def delete_settings(self, *keys: str) -> None:
        await self.conn.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in keys])
        await self.conn.commit()

    # ---------- Участники ----------

    async def get_participant(self, user_id: int) -> Participant | None:
        async with self.conn.execute(
            "SELECT user_id, number, username, full_name, joined_at FROM participants WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return Participant(**dict(row)) if row else None

    async def get_participant_by_number(self, number: int) -> Participant | None:
        async with self.conn.execute(
            "SELECT user_id, number, username, full_name, joined_at FROM participants WHERE number = ?",
            (number,),
        ) as cur:
            row = await cur.fetchone()
        return Participant(**dict(row)) if row else None

    async def get_participant_by_username(self, username: str) -> Participant | None:
        async with self.conn.execute(
            "SELECT user_id, number, username, full_name, joined_at FROM participants "
            "WHERE username = ? COLLATE NOCASE",
            (username,),
        ) as cur:
            row = await cur.fetchone()
        return Participant(**dict(row)) if row else None

    async def register_participant(
        self, user_id: int, username: str | None, full_name: str
    ) -> tuple[Participant, bool]:
        """Выдаёт участнику следующий по порядку номер.

        Возвращает (участник, True), если номер выдан сейчас, или (участник, False),
        если пользователь уже был зарегистрирован раньше.
        """
        async with self._register_lock:
            existing = await self.get_participant(user_id)
            if existing is not None:
                return existing, False
            await self.conn.execute(
                "INSERT INTO participants (user_id, number, username, full_name) "
                "SELECT ?, COALESCE(MAX(number), 0) + 1, ?, ? FROM participants",
                (user_id, username, full_name),
            )
            await self.conn.commit()
            participant = await self.get_participant(user_id)
        assert participant is not None
        return participant, True

    async def count_participants(self) -> int:
        async with self.conn.execute("SELECT COUNT(*) FROM participants") as cur:
            (count,) = await cur.fetchone()
        return count

    async def list_participants(self, offset: int = 0, limit: int = -1) -> list[Participant]:
        async with self.conn.execute(
            "SELECT user_id, number, username, full_name, joined_at FROM participants ORDER BY number LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [Participant(**dict(row)) for row in rows]
