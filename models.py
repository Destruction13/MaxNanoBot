from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

import aiohttp


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    display_name: str
    description: str
    methods: tuple[str, ...]


class ModelRegistry:
    def __init__(self, models: Iterable[ModelInfo]) -> None:
        ordered = tuple(models)
        if not ordered:
            raise RuntimeError("No image-capable models found")
        self._models = ordered
        self._by_id = {model.id: model for model in ordered}

    def all(self) -> tuple[ModelInfo, ...]:
        return self._models

    def get(self, model_id: str) -> ModelInfo | None:
        return self._by_id.get(model_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(self._by_id.keys())


def normalize_model_id(value: str) -> str:
    value = value.strip()
    if value.startswith("models/"):
        return value.split("/", 1)[1]
    return value


def _matches_keywords(model: ModelInfo, keywords: Iterable[str]) -> bool:
    haystack = " ".join(
        [
            model.name.lower(),
            model.display_name.lower(),
            model.description.lower(),
        ]
    )
    for keyword in keywords:
        if keyword.lower() in haystack:
            return True
    return False


async def fetch_models(
    api_base_url: str,
    api_key: str,
    *,
    timeout: int,
) -> list[ModelInfo]:
    url = f"{api_base_url}/models?key={api_key}"
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url) as response:
            text = await response.text()
            if response.status != 200:
                raise RuntimeError(f"Model list request failed: {response.status} {text}")
            payload = json.loads(text)

    models = payload.get("models", [])
    results: list[ModelInfo] = []
    for item in models:
        name = item.get("name", "")
        model_id = normalize_model_id(name)
        methods = tuple(item.get("supportedGenerationMethods") or ())
        results.append(
            ModelInfo(
                id=model_id,
                name=name,
                display_name=item.get("displayName", model_id),
                description=item.get("description", ""),
                methods=methods,
            )
        )
    return results


def filter_image_models(
    models: Iterable[ModelInfo],
    *,
    keywords: Iterable[str],
    allowlist: Iterable[str] = (),
) -> list[ModelInfo]:
    allow_set = {normalize_model_id(item) for item in allowlist if item}
    filtered: list[ModelInfo] = []
    for model in models:
        if "generateContent" not in model.methods:
            continue
        if not _matches_keywords(model, keywords):
            continue
        if allow_set and model.id not in allow_set:
            continue
        filtered.append(model)
    filtered.sort(key=lambda item: item.id)
    return filtered

