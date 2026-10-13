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
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS participants (
    user_id    INTEGER PRIMARY KEY,
    number     INTEGER NOT NULL UNIQUE,
    username   TEXT,
    full_name  TEXT    NOT NULL DEFAULT '',
    joined_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

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
        await self._conn.commit()

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
        async with self.conn.execute(
            f"SELECT id, kind, title, url, chat_id, amount FROM sponsors ORDER BY {_SPONSOR_ORDER}"
        ) as cur:
            rows = await cur.fetchall()
        return [Sponsor(**dict(row)) for row in rows]

    async def get_sponsor(self, sponsor_id: int) -> Sponsor | None:
        async with self.conn.execute(
            "SELECT id, kind, title, url, chat_id, amount FROM sponsors WHERE id = ?",
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

    async def delete_sponsor(self, sponsor_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM sponsors WHERE id = ?", (sponsor_id,))
        await self.conn.commit()
        return cur.rowcount > 0

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
