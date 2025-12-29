from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Message,
)
from aiogram.types.input_file import BufferedInputFile

from api_client import ApiClient, ApiError
from config import Settings, load_settings
from models import ModelInfo, ModelRegistry, fetch_models, filter_image_models
from storage import Storage

MEDIA_GROUP_DELAY = 0.6
INSTRUCTION_MESSAGE = (
    "Ок, модель выбрана. Теперь пришли одним сообщением промпт "
    "(и опционально фото)."
)
NEED_PROMPT_MESSAGE = "Нужен промпт текстом"
PENDING_PHOTOS_MESSAGE = "Ок, фото принял. Теперь пришли промпт текстом."
PENDING_PHOTOS_WAIT_MESSAGE = "Ок, фото принял. Сейчас генерирую, промпт после."
WAIT_MESSAGE = "Сейчас генерирую предыдущий запрос. Подожди и пришли ещё раз."
GENERATING_MESSAGE = "Генерирую..."


@dataclass(slots=True)
class MessageSnapshot:
    message_id: int
    text: str | None
    caption: str | None
    photo_file_id: str | None


@dataclass(slots=True)
class MediaGroupBucket:
    user_id: int
    chat_id: int
    bot: Bot
    state: FSMContext
    snapshots: list[MessageSnapshot]


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


async def _safe_delete(bot: Bot, chat_id: int, message_id: int) -> None:
    with suppress(Exception):
        await bot.delete_message(chat_id, message_id)


async def _send_aux(
    bot: Bot,
    storage: Storage,
    user_id: int,
    chat_id: int,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    message = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    await storage.add_aux_message(user_id, chat_id, message.message_id)
    return message


async def _delete_aux_messages(
    bot: Bot,
    storage: Storage,
    user_id: int,
    *,
    keep: set[tuple[int, int]] | None = None,
) -> None:
    entries = await storage.get_aux_messages(user_id)
    keep_set = keep or set()
    for chat_id, message_id in entries:
        if (chat_id, message_id) in keep_set:
            continue
        await _safe_delete(bot, chat_id, message_id)
    if keep_set:
        await storage.set_aux_messages(user_id, list(keep_set))
    else:
        await storage.clear_aux_messages(user_id)


def _build_models_keyboard(
    models: Iterable[ModelInfo],
    selected_model: str | None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for model in models:
        text = model.id
        rows.append(
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"model:{model.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _get_user_lock(locks: dict[int, asyncio.Lock], user_id: int) -> asyncio.Lock:
    if user_id not in locks:
        locks[user_id] = asyncio.Lock()
    return locks[user_id]


def _snapshot_message(message: Message) -> MessageSnapshot:
    photo_file_id = None
    if message.photo:
        photo_file_id = message.photo[-1].file_id
    return MessageSnapshot(
        message_id=message.message_id,
        text=message.text,
        caption=message.caption,
        photo_file_id=photo_file_id,
    )


def _extract_prompt(snapshots: Iterable[MessageSnapshot]) -> str:
    for item in snapshots:
        if item.text:
            return item.text
        if item.caption:
            return item.caption
    return ""


def _extract_photo_ids(snapshots: Iterable[MessageSnapshot]) -> list[str]:
    ids: list[str] = []
    for item in snapshots:
        if item.photo_file_id:
            ids.append(item.photo_file_id)
    return ids


async def _download_photo(
    bot: Bot,
    file_id: str,
    *,
    temp_dir: Path,
    user_id: int,
    index: int,
) -> str:
    user_dir = temp_dir / str(user_id)
    _ensure_dir(user_dir)
    file_info = await bot.get_file(file_id)
    suffix = Path(file_info.file_path or "").suffix or ".jpg"
    destination = user_dir / f"photo_{index}{suffix}"
    await bot.download(file_id, destination=destination)
    return str(destination)


async def _cleanup_paths(paths: Iterable[str], *, temp_dir: Path, user_id: int) -> None:
    for path in paths:
        with suppress(OSError):
            Path(path).unlink()
    user_dir = temp_dir / str(user_id)
    with suppress(OSError):
        if user_dir.exists() and not any(user_dir.iterdir()):
            user_dir.rmdir()


async def _prompt_model_selection(
    bot: Bot,
    chat_id: int,
    user_id: int,
    state: FSMContext,
    storage: Storage,
    registry: ModelRegistry,
    settings: Settings,
    *,
    greeting: bool,
) -> None:
    data = await state.get_data()
    previous_id = data.get("model_message_id")
    if isinstance(previous_id, int):
        await _safe_delete(bot, chat_id, previous_id)

    selected_model = await storage.get_selected_model(user_id)
    text = "Привет! Выбери модель." if greeting else "Выбери модель."
    keyboard = _build_models_keyboard(registry.all(), selected_model)
    message = await bot.send_message(chat_id, text, reply_markup=keyboard)
    await state.update_data(model_message_id=message.message_id)


def create_router(
    *,
    settings: Settings,
    storage: Storage,
    registry: ModelRegistry,
    api_client: ApiClient,
) -> Router:
    router = Router()
    locks: dict[int, asyncio.Lock] = {}
    media_groups: dict[str, MediaGroupBucket] = {}

    async def _clear_aux_if_idle(bot: Bot, user_id: int) -> None:
        lock = _get_user_lock(locks, user_id)
        if not lock.locked():
            await _delete_aux_messages(bot, storage, user_id)

    async def _process_snapshots(
        snapshots: list[MessageSnapshot],
        *,
        bot: Bot,
        chat_id: int,
        user_id: int,
        state: FSMContext,
    ) -> None:
        ordered = sorted(snapshots, key=lambda item: item.message_id)
        prompt = _extract_prompt(ordered)
        prompt_present = bool(prompt and prompt.strip())
        photo_ids = _extract_photo_ids(ordered)
        lock = _get_user_lock(locks, user_id)

        if not prompt_present:
            if photo_ids:
                if not lock.locked():
                    await _delete_aux_messages(bot, storage, user_id)
                await storage.set_pending_images(user_id, photo_ids)
                pending_message = (
                    PENDING_PHOTOS_WAIT_MESSAGE
                    if lock.locked()
                    else PENDING_PHOTOS_MESSAGE
                )
                await _send_aux(
                    bot,
                    storage,
                    user_id,
                    chat_id,
                    pending_message,
                )
            else:
                if not lock.locked():
                    await _delete_aux_messages(bot, storage, user_id)
                await _send_aux(
                    bot,
                    storage,
                    user_id,
                    chat_id,
                    NEED_PROMPT_MESSAGE,
                )
            return

        if lock.locked():
            await _send_aux(
                bot,
                storage,
                user_id,
                chat_id,
                WAIT_MESSAGE,
            )
            return

        await _delete_aux_messages(bot, storage, user_id)

        selected_model = await storage.get_selected_model(user_id)
        if not selected_model or not registry.get(selected_model):
            await _prompt_model_selection(
                bot,
                chat_id,
                user_id,
                state,
                storage,
                registry,
                settings,
                greeting=False,
            )
            return

        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.01)
        except asyncio.TimeoutError:
            await _send_aux(
                bot,
                storage,
                user_id,
                chat_id,
                WAIT_MESSAGE,
            )
            return

        pending_used = False
        if photo_ids:
            await storage.clear_pending_images(user_id)
            active_photo_ids = photo_ids
        else:
            pending = await storage.get_pending_images(user_id)
            if pending:
                pending_used = True
                active_photo_ids = pending
            else:
                active_photo_ids = []

        await _send_aux(
            bot,
            storage,
            user_id,
            chat_id,
            GENERATING_MESSAGE,
        )
        paths: list[str] = []
        success = False
        try:
            for index, file_id in enumerate(active_photo_ids, start=1):
                path = await _download_photo(
                    bot,
                    file_id,
                    temp_dir=settings.temp_dir,
                    user_id=user_id,
                    index=index,
                )
                paths.append(path)
            image_bytes = await api_client.generate_image(
                selected_model,
                paths,
                prompt,
            )
            success = True
            await bot.send_photo(
                chat_id,
                BufferedInputFile(image_bytes, filename="result.png"),
            )
            await _delete_aux_messages(bot, storage, user_id)
        except ApiError as exc:
            logging.exception("Generation failed: %s", exc)
            error_message = await _send_aux(
                bot,
                storage,
                user_id,
                chat_id,
                "Ошибка генерации",
            )
            keep = {(error_message.chat.id, error_message.message_id)}
            await _delete_aux_messages(bot, storage, user_id, keep=keep)
        except Exception as exc:
            logging.exception("Unexpected generation error: %s", exc)
            error_message = await _send_aux(
                bot,
                storage,
                user_id,
                chat_id,
                "Ошибка генерации",
            )
            keep = {(error_message.chat.id, error_message.message_id)}
            await _delete_aux_messages(bot, storage, user_id, keep=keep)
        finally:
            lock.release()
            await _cleanup_paths(paths, temp_dir=settings.temp_dir, user_id=user_id)
            if pending_used and success:
                await storage.clear_pending_images(user_id)

    async def _flush_media_group(group_key: str) -> None:
        await asyncio.sleep(MEDIA_GROUP_DELAY)
        bucket = media_groups.pop(group_key, None)
        if not bucket:
            return
        await _process_snapshots(
            bucket.snapshots,
            bot=bucket.bot,
            chat_id=bucket.chat_id,
            user_id=bucket.user_id,
            state=bucket.state,
        )

    @router.message(CommandStart())
    async def handle_start(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id if message.from_user else 0
        await _safe_delete(message.bot, message.chat.id, message.message_id)
        await _clear_aux_if_idle(message.bot, user_id)
        await _prompt_model_selection(
            message.bot,
            message.chat.id,
            user_id,
            state,
            storage,
            registry,
            settings,
            greeting=True,
        )

    @router.message(Command("swap"))
    async def handle_swap(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id if message.from_user else 0
        await _safe_delete(message.bot, message.chat.id, message.message_id)
        await _clear_aux_if_idle(message.bot, user_id)
        await _prompt_model_selection(
            message.bot,
            message.chat.id,
            user_id,
            state,
            storage,
            registry,
            settings,
            greeting=False,
        )

    @router.callback_query(F.data.startswith("model:"))
    async def handle_model_select(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        model_id = callback.data.split(":", 1)[1]
        if not registry.get(model_id):
            await callback.answer("Модель недоступна", show_alert=True)
            return

        await storage.set_selected_model(user_id, model_id)
        await callback.answer()
        if callback.message:
            await _safe_delete(callback.message.bot, callback.message.chat.id, callback.message.message_id)
        await state.update_data(model_message_id=None)
        await _clear_aux_if_idle(callback.bot, user_id)
        await _send_aux(
            callback.bot,
            storage,
            user_id,
            callback.message.chat.id if callback.message else callback.from_user.id,
            INSTRUCTION_MESSAGE,
        )

    @router.message(~F.text.startswith("/"))
    async def handle_user_message(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id if message.from_user else 0
        snapshot = _snapshot_message(message)
        await _safe_delete(message.bot, message.chat.id, message.message_id)

        if message.media_group_id:
            group_key = f"{message.chat.id}:{message.media_group_id}"
            bucket = media_groups.get(group_key)
            if not bucket:
                bucket = MediaGroupBucket(
                    user_id=user_id,
                    chat_id=message.chat.id,
                    bot=message.bot,
                    state=state,
                    snapshots=[],
                )
                media_groups[group_key] = bucket
                asyncio.create_task(_flush_media_group(group_key))
            bucket.snapshots.append(snapshot)
            return

        await _process_snapshots(
            [snapshot],
            bot=message.bot,
            chat_id=message.chat.id,
            user_id=user_id,
            state=state,
        )

    return router


async def load_model_registry(settings: Settings) -> ModelRegistry:
    try:
        catalog = await fetch_models(
            settings.api_base_url,
            settings.api_key,
            timeout=settings.request_timeout,
        )
    except Exception as exc:
        if settings.model_allowlist:
            logging.warning("Failed to fetch models, using allowlist: %s", exc)
            fallback = [
                ModelInfo(
                    id=model_id,
                    name=f"models/{model_id}",
                    display_name=model_id,
                    description="",
                    methods=("generateContent",),
                )
                for model_id in settings.model_allowlist
            ]
            return ModelRegistry(fallback)
        raise

    filtered = filter_image_models(
        catalog,
        keywords=settings.model_keywords,
        allowlist=settings.model_allowlist,
    )
    return ModelRegistry(filtered)


async def _setup_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Старт и выбор модели"),
            BotCommand(command="swap", description="Сменить модель"),
        ]
    )
    with suppress(Exception):
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    registry = await load_model_registry(settings)
    logging.info("Available models: %s", ", ".join(registry.ids()))

    storage = Storage(settings.db_path)
    await storage.connect()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await _setup_commands(bot)

    api_client = ApiClient(
        settings.api_base_url,
        settings.api_key,
        timeout=settings.request_timeout,
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(
        create_router(
            settings=settings,
            storage=storage,
            registry=registry,
            api_client=api_client,
        )
    )

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await storage.close()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
