from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass(frozen=True)
class LastRequest:
    model_id: str
    prompt: str
    style_id: str
    photo_file_ids: list[str]
    variants_count: int
    updated_at: float


@dataclass
class ConversationMessage:
    """A single message in conversation history."""
    role: str  # "user" or "model"
    text: str
    has_image: bool = False  # Flag indicating image was present (not stored inline)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "has_image": self.has_image,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationMessage":
        return cls(
            role=data.get("role", "user"),
            text=data.get("text", ""),
            has_image=data.get("has_image", False),
            timestamp=data.get("timestamp", 0.0),
        )


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
                selected_model TEXT NOT NULL,
                variants_count INTEGER NOT NULL DEFAULT 1,
                selected_style TEXT NOT NULL DEFAULT 'none'
            )
            """
        )
        await self._ensure_column(
            "user_settings",
            "variants_count",
            "INTEGER NOT NULL DEFAULT 1",
        )
        await self._ensure_column(
            "user_settings",
            "selected_style",
            "TEXT NOT NULL DEFAULT 'none'",
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
            CREATE TABLE IF NOT EXISTS last_request (
                user_id INTEGER PRIMARY KEY,
                model_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                style_id TEXT NOT NULL DEFAULT 'none',
                photo_file_ids TEXT NOT NULL,
                variants_count INTEGER NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await self._ensure_column(
            "last_request",
            "style_id",
            "TEXT NOT NULL DEFAULT 'none'",
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
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_history (
                user_id INTEGER PRIMARY KEY,
                messages TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS last_generated_image (
                user_id INTEGER PRIMARY KEY,
                file_id TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        async with self._conn.execute(f"PRAGMA table_info({table})") as cursor:
            rows = await cursor.fetchall()
        existing = {row[1] for row in rows}
        if column in existing:
            return
        await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

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

    async def get_selected_style(self, user_id: int) -> str:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        async with self._conn.execute(
            "SELECT selected_style FROM user_settings WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row or not row[0]:
            return "none"
        return row[0]

    async def set_selected_style(self, user_id: int, style_id: str) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        selected_model = await self.get_selected_model(user_id)
        variants_count = await self.get_variants_count(user_id)
        await self._conn.execute(
            """
            INSERT INTO user_settings (
                user_id,
                selected_model,
                variants_count,
                selected_style
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET selected_style = excluded.selected_style
            """,
            (user_id, selected_model or "", variants_count, style_id),
        )
        await self._conn.commit()

    async def get_variants_count(self, user_id: int) -> int:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        async with self._conn.execute(
            "SELECT variants_count FROM user_settings WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] is None:
            return 1
        try:
            value = int(row[0])
        except (TypeError, ValueError):
            return 1
        return value

    async def set_variants_count(self, user_id: int, variants_count: int) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        try:
            variants_count = int(variants_count)
        except (TypeError, ValueError):
            variants_count = 1
        variants_count = max(1, min(4, variants_count))
        selected_model = await self.get_selected_model(user_id)
        selected_style = await self.get_selected_style(user_id)
        await self._conn.execute(
            """
            INSERT INTO user_settings (
                user_id,
                selected_model,
                variants_count,
                selected_style
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET variants_count = excluded.variants_count
            """,
            (user_id, selected_model or "", variants_count, selected_style),
        )
        await self._conn.commit()

    async def set_last_request(
        self,
        user_id: int,
        model_id: str,
        prompt: str,
        style_id: str,
        photo_file_ids: list[str],
        variants_count: int,
    ) -> None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        try:
            variants_count = int(variants_count)
        except (TypeError, ValueError):
            variants_count = 1
        variants_count = max(1, min(4, variants_count))
        payload = json.dumps(photo_file_ids)
        updated_at = time.time()
        await self._conn.execute(
            """
            INSERT INTO last_request (
                user_id,
                model_id,
                prompt,
                style_id,
                photo_file_ids,
                variants_count,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                model_id = excluded.model_id,
                prompt = excluded.prompt,
                style_id = excluded.style_id,
                photo_file_ids = excluded.photo_file_ids,
                variants_count = excluded.variants_count,
                updated_at = excluded.updated_at
            """,
            (user_id, model_id, prompt, style_id, payload, variants_count, updated_at),
        )
        await self._conn.commit()

    async def get_last_request(self, user_id: int) -> LastRequest | None:
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        async with self._conn.execute(
            """
            SELECT model_id, prompt, style_id, photo_file_ids, variants_count, updated_at
            FROM last_request
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        try:
            photo_file_ids = json.loads(row[3]) if row[3] else []
        except json.JSONDecodeError:
            photo_file_ids = []
        if not isinstance(photo_file_ids, list):
            photo_file_ids = []
        try:
            variants_count = int(row[4])
        except (TypeError, ValueError):
            variants_count = 1
        try:
            updated_at = float(row[5])
        except (TypeError, ValueError):
            updated_at = 0.0
        return LastRequest(
            model_id=row[0],
            prompt=row[1],
            style_id=row[2] or "none",
            photo_file_ids=photo_file_ids,
            variants_count=variants_count,
            updated_at=updated_at,
        )

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

    # ========== Conversation History ==========

    async def get_conversation(
        self, user_id: int, max_messages: int = 20
    ) -> list[ConversationMessage]:
        """Get conversation history for a user, limited to last N messages."""
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        async with self._conn.execute(
            "SELECT messages FROM conversation_history WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row or not row[0]:
            return []
        try:
            data = json.loads(row[0])
            if not isinstance(data, list):
                return []
            messages = [ConversationMessage.from_dict(item) for item in data]
            # Return only last N messages
            return messages[-max_messages:] if len(messages) > max_messages else messages
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    async def add_to_conversation(
        self, user_id: int, message: ConversationMessage, max_messages: int = 50
    ) -> None:
        """Add a message to conversation history, keeping only last N messages."""
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        
        # Get existing messages
        existing = await self.get_conversation(user_id, max_messages=max_messages)
        existing.append(message)
        
        # Keep only last N messages
        if len(existing) > max_messages:
            existing = existing[-max_messages:]
        
        # Save back
        payload = json.dumps([msg.to_dict() for msg in existing])
        updated_at = time.time()
        await self._conn.execute(
            """
            INSERT INTO conversation_history (user_id, messages, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                messages = excluded.messages,
                updated_at = excluded.updated_at
            """,
            (user_id, payload, updated_at),
        )
        await self._conn.commit()

    async def clear_conversation(self, user_id: int) -> None:
        """Clear conversation history for a user."""
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        await self._conn.execute(
            "DELETE FROM conversation_history WHERE user_id = ?",
            (user_id,),
        )
        await self._conn.execute(
            "DELETE FROM last_generated_image WHERE user_id = ?",
            (user_id,),
        )
        await self._conn.commit()

    # ========== Last Generated Image ==========

    async def set_last_generated_image(self, user_id: int, file_id: str) -> None:
        """Store the file_id of the last generated image for context."""
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        updated_at = time.time()
        await self._conn.execute(
            """
            INSERT INTO last_generated_image (user_id, file_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                file_id = excluded.file_id,
                updated_at = excluded.updated_at
            """,
            (user_id, file_id, updated_at),
        )
        await self._conn.commit()

    async def get_last_generated_image(self, user_id: int) -> str | None:
        """Get the file_id of the last generated image."""
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        async with self._conn.execute(
            "SELECT file_id FROM last_generated_image WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def clear_last_generated_image(self, user_id: int) -> None:
        """Clear the last generated image."""
        if self._conn is None:
            raise RuntimeError("Storage is not connected")
        await self._conn.execute(
            "DELETE FROM last_generated_image WHERE user_id = ?",
            (user_id,),
        )
        await self._conn.commit()
