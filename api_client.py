from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
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


@dataclass
class ChatMessage:
    """Represents a single message in conversation history."""
    role: str  # "user" or "model"
    text: str
    image_data: list[dict[str, Any]] = field(default_factory=list)  # inline_data dicts

    def to_api_part(self) -> dict[str, Any]:
        """Convert to Gemini API format."""
        parts: list[dict[str, Any]] = []
        if self.text:
            parts.append({"text": self.text})
        parts.extend(self.image_data)
        return {"role": self.role, "parts": parts}


@dataclass
class ChatResponse:
    """Response from chat API - can contain text and/or images."""
    text: str
    images: list[bytes] = field(default_factory=list)
    # Full inline_data dicts from API response (includes thought_signature if present)
    image_parts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_images(self) -> bool:
        return len(self.images) > 0


class ApiClient:
    def __init__(self, api_base_url: str, api_key: str, *, timeout: int) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def chat(
        self,
        model_id: str,
        messages: list[ChatMessage],
        system_instruction: str | None = None,
    ) -> ChatResponse:
        """
        Send a chat request with conversation history.
        Returns ChatResponse with text and optionally generated images.
        """
        contents = [msg.to_api_part() for msg in messages]

        payload: dict[str, Any] = {"contents": contents}
        
        if system_instruction:
            payload["system_instruction"] = {
                "parts": [{"text": system_instruction}]
            }

        url = f"{self._api_base_url}/models/{model_id}:generateContent?key={self._api_key}"
        client_timeout = aiohttp.ClientTimeout(total=self._timeout)
        
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            try:
                async with session.post(url, json=payload) as response:
                    response_text = await response.text()
                    if response.status != 200:
                        raise ApiError(
                            "Chat request failed",
                            f"{response.status} {response_text[:200]}",
                        )
                    try:
                        data = json.loads(response_text)
                    except json.JSONDecodeError as exc:
                        raise ApiError("Failed to decode API response", str(exc)) from exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise ApiError("API request failed", str(exc)) from exc

        # Extract text and images from response
        text_parts = _extract_text_parts(data)
        text = "\n".join(text_parts) if text_parts else ""
        
        # Extract full image parts (with thought_signature) for context
        image_parts = _extract_image_parts(data)
        
        # Decode images for sending to user
        images: list[bytes] = []
        for part in image_parts:
            inline = part.get("inline_data", {})
            image_b64 = inline.get("data", "")
            if image_b64:
                try:
                    images.append(base64.b64decode(image_b64.strip()))
                except (ValueError, TypeError):
                    continue

        if not text and not images:
            detail = _extract_error_detail(data)
            raise ApiError("API response contains no content", detail)

        return ChatResponse(text=text, images=images, image_parts=image_parts)

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
            detail = _extract_error_detail(data)
            raise ApiError("API response does not contain inline image data", detail)
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


def encode_image_bytes(data: bytes, mime_type: str = "image/png") -> dict[str, Any]:
    """Encode image bytes to inline_data format for API."""
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "inline_data": {
            "mime_type": mime_type,
            "data": encoded,
        }
    }


async def encode_image_from_path(path: str) -> dict[str, Any]:
    """Encode image from file path to inline_data format."""
    return await _encode_image(Path(path))


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    return "image/png"


def _extract_inline_image(payload: Any) -> str | None:
    """Extract a single inline image from API response."""
    images = _extract_all_inline_images(payload)
    return images[0] if images else None


def _extract_all_inline_images(payload: Any) -> list[str]:
    """Extract all inline images from API response."""
    results: list[str] = []
    seen: set[str] = set()
    
    def _add_unique(data: str) -> None:
        data = data.strip()
        if data and data not in seen:
            seen.add(data)
            results.append(data)
    
    candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
    for candidate in candidates:
        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            inline = part.get("inline_data") or part.get("inlineData")
            if isinstance(inline, dict):
                data = inline.get("data")
                if isinstance(data, str):
                    _add_unique(data)
            text = part.get("text")
            if isinstance(text, str):
                data = _extract_data_uri(text)
                if data:
                    _add_unique(data)
                data = _extract_base64_blob(text)
                if data:
                    _add_unique(data)

    # Fallback: deep search
    queue = [payload]
    while queue:
        current = queue.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in {"inline_data", "inlineData"} and isinstance(value, dict):
                    data = value.get("data")
                    if isinstance(data, str):
                        _add_unique(data)
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(current, list):
            queue.extend(current)
    
    return results


def _extract_image_parts(payload: Any) -> list[dict[str, Any]]:
    """Extract full inline_data parts from API response (including thought_signature)."""
    results: list[dict[str, Any]] = []
    seen_data: set[str] = set()
    
    candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
    for candidate in candidates:
        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            inline = part.get("inline_data") or part.get("inlineData")
            if isinstance(inline, dict):
                data = inline.get("data")
                if isinstance(data, str) and data not in seen_data:
                    seen_data.add(data)
                    # Return the full part structure to preserve thought_signature
                    results.append({"inline_data": inline})
    
    return results


def _extract_data_uri(text: str) -> str | None:
    match = re.search(
        r"data:image/(?:png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\\s]+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    data = match.group(1)
    return "".join(data.split())


def _extract_base64_blob(text: str) -> str | None:
    compact = "".join(text.split())
    if len(compact) < 256:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", compact):
        return None
    return compact


def _extract_text_parts(payload: Any) -> list[str]:
    results: list[str] = []
    if not isinstance(payload, dict):
        return results
    candidates = payload.get("candidates", [])
    for candidate in candidates:
        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                results.append(text.strip())
    return results


def _extract_error_detail(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    feedback = payload.get("promptFeedback") or {}
    if isinstance(feedback, dict):
        block_reason = feedback.get("blockReason")
        if isinstance(block_reason, str) and block_reason:
            return f"blocked: {block_reason}"
    texts = _extract_text_parts(payload)
    if not texts:
        return ""
    preview = texts[0]
    if len(preview) > 200:
        preview = f"{preview[:200]}..."
    return f"text response: {preview}"
