from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import aiohttp


@dataclass(frozen=True)
class ApiError(RuntimeError):
    message: str
    detail: str = ""

    def __str__(self) -> str:
        if self.detail:
            return f"{self.message}: {self.detail}"
        return self.message


class ApiClient:
    def __init__(self, api_base_url: str, api_key: str, *, timeout: int) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def generate_image(
        self,
        model_id: str,
        image_paths: Iterable[str],
        prompt: str,
    ) -> bytes:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for path in image_paths:
            parts.append(await _encode_image(Path(path)))

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ]
        }

        url = f"{self._api_base_url}/models/{model_id}:generateContent?key={self._api_key}"
        client_timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            try:
                async with session.post(url, json=payload) as response:
                    text = await response.text()
                    if response.status != 200:
                        raise ApiError(
                            "Generation request failed",
                            f"{response.status} {text[:200]}",
                        )
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise ApiError("Failed to decode API response", str(exc)) from exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise ApiError("API request failed", str(exc)) from exc

        image_b64 = _extract_inline_image(data)
        if not image_b64:
            raise ApiError("API response does not contain inline image data")
        try:
            return base64.b64decode(image_b64.strip())
        except (ValueError, TypeError) as exc:
            raise ApiError("Invalid base64 image data") from exc


async def _encode_image(path: Path) -> dict[str, Any]:
    data = await asyncio.to_thread(path.read_bytes)
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "inline_data": {
            "mime_type": _guess_mime(path),
            "data": encoded,
        }
    }


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    return "image/png"


def _extract_inline_image(payload: Any) -> str | None:
    candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
    for candidate in candidates:
        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            inline = part.get("inline_data") or part.get("inlineData")
            if isinstance(inline, dict):
                data = inline.get("data")
                if isinstance(data, str) and data.strip():
                    return data

    queue = [payload]
    while queue:
        current = queue.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in {"inline_data", "inlineData"} and isinstance(value, dict):
                    data = value.get("data")
                    if isinstance(data, str) and data.strip():
                        return data
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(current, list):
            queue.extend(current)
    return None
