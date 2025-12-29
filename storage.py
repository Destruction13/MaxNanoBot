from __future__ import annotations

from pathlib import Path

import aiosqlite


class Storage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(str(self._db_path))
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                selected_model TEXT NOT NULL
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_images (
                user_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                PRIMARY KEY (user_id, position)
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aux_messages (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, chat_id, message_id)
            )
            """
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def get_selected_model(self, user_id: int) -> str | None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        async with self._conn.execute(
            "SELECT selected_model FROM user_settings WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_selected_model(self, user_id: int, model_id: str) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        await self._conn.execute(
            """
            INSERT INTO user_settings (user_id, selected_model)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET selected_model = excluded.selected_model
            """,
            (user_id, model_id),
        )
        await self._conn.commit()

    async def get_pending_images(self, user_id: int) -> list[str]:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        async with self._conn.execute(
            "SELECT file_id FROM pending_images WHERE user_id = ? ORDER BY position",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def set_pending_images(self, user_id: int, file_ids: list[str]) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        await self._conn.execute(
            "DELETE FROM pending_images WHERE user_id = ?",
            (user_id,),
        )
        if file_ids:
            records = [(user_id, index, file_id) for index, file_id in enumerate(file_ids)]
            await self._conn.executemany(
                "INSERT INTO pending_images (user_id, position, file_id) VALUES (?, ?, ?)",
                records,
            )
        await self._conn.commit()

    async def clear_pending_images(self, user_id: int) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        await self._conn.execute(
            "DELETE FROM pending_images WHERE user_id = ?",
            (user_id,),
        )
        await self._conn.commit()

    async def add_aux_message(self, user_id: int, chat_id: int, message_id: int) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO aux_messages (user_id, chat_id, message_id)
            VALUES (?, ?, ?)
            """,
            (user_id, chat_id, message_id),
        )
        await self._conn.commit()

    async def add_aux_messages(
        self, user_id: int, entries: list[tuple[int, int]]
    ) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        if not entries:
            return
        records = [(user_id, chat_id, message_id) for chat_id, message_id in entries]
        await self._conn.executemany(
            """
            INSERT OR IGNORE INTO aux_messages (user_id, chat_id, message_id)
            VALUES (?, ?, ?)
            """,
            records,
        )
        await self._conn.commit()

    async def get_aux_messages(self, user_id: int) -> list[tuple[int, int]]:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        async with self._conn.execute(
            "SELECT chat_id, message_id FROM aux_messages WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [(row[0], row[1]) for row in rows]

    async def clear_aux_messages(self, user_id: int) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        await self._conn.execute(
            "DELETE FROM aux_messages WHERE user_id = ?",
            (user_id,),
        )
        await self._conn.commit()

    async def set_aux_messages(
        self, user_id: int, entries: list[tuple[int, int]]
    ) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        await self._conn.execute(
            "DELETE FROM aux_messages WHERE user_id = ?",
            (user_id,),
        )
        if entries:
            records = [(user_id, chat_id, message_id) for chat_id, message_id in entries]
            await self._conn.executemany(
                """
                INSERT OR IGNORE INTO aux_messages (user_id, chat_id, message_id)
                VALUES (?, ?, ?)
                """,
                records,
            )
        await self._conn.commit()
