from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return
    load_dotenv()


def _split_list(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    parts: list[str] = []
    for raw in value.replace("\n", ",").split(","):
        item = raw.strip()
        if item:
            parts.append(item)
    return tuple(parts)


def _normalize_models(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        if value.startswith("models/"):
            value = value.split("/", 1)[1]
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_key: str
    api_base_url: str
    db_path: Path
    temp_dir: Path
    model_allowlist: tuple[str, ...]
    model_keywords: tuple[str, ...]
    temp_message_ttl: float
    request_timeout: int
    log_level: str


def load_settings() -> Settings:
    load_env()
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set")

    api_key = (
        os.getenv("NANOBANANA_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("API_KEY")
    )
    if not api_key:
        raise RuntimeError("API key is not set (NANOBANANA_API_KEY or GOOGLE_API_KEY)")

    api_base_url = os.getenv(
        "API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")

    db_value = os.getenv("DATABASE_PATH") or os.getenv("SQLITE_PATH") or "bot.db"
    db_path = Path(db_value)
    temp_value = os.getenv("TEMP_DIR") or os.getenv("TMP_DIR") or "tmp"
    temp_dir = Path(temp_value)

    allowlist = _normalize_models(_split_list(os.getenv("MODEL_ALLOWLIST", "")))
    keywords = _split_list(os.getenv("MODEL_KEYWORDS", "image,nano-banana,banana"))

    temp_message_ttl = _float_env("TEMP_MESSAGE_TTL", 8.0)
    request_timeout = _int_env("REQUEST_TIMEOUT", 120)
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    return Settings(
        bot_token=bot_token,
        api_key=api_key,
        api_base_url=api_base_url,
        db_path=db_path,
        temp_dir=temp_dir,
        model_allowlist=allowlist,
        model_keywords=keywords,
        temp_message_ttl=temp_message_ttl,
        request_timeout=request_timeout,
        log_level=log_level,
    )
