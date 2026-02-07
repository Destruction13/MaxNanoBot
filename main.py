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
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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

from api_client import (
    ApiClient,
    ApiError,
    ChatMessage,
    ChatResponse,
    encode_image_from_path,
)
from config import Settings, load_settings
from models import ModelInfo, ModelRegistry, fetch_models, filter_image_models
from storage import Storage, ConversationMessage

MEDIA_GROUP_DELAY = 0.6
INSTRUCTION_MESSAGE = (
    "Ок, модель выбрана! Теперь можешь общаться со мной как с ChatGPT.\n\n"
    "Я могу:\n"
    "• Отвечать на вопросы\n"
    "• Генерировать и редактировать изображения\n"
    "• Анализировать фото\n"
    "• Помнить контекст диалога\n\n"
    "Команды: /clear — очистить историю, /swap — сменить модель"
)
NEED_PROMPT_MESSAGE = "Напиши что-нибудь 😊"
PENDING_PHOTOS_MESSAGE = "Ок, фото принял. Что сделать с ним?"
PENDING_PHOTOS_WAIT_MESSAGE = "Ок, фото принял. Сейчас обрабатываю, подожди."
WAIT_MESSAGE = "Сейчас обрабатываю предыдущий запрос. Подожди и пришли ещё раз."
THINKING_MESSAGE = "Думаю..."
GENERATING_MESSAGE = "Генерирую..."
ERROR_MESSAGE = "Что-то пошло не так 😕"
CONVERSATION_CLEARED_MESSAGE = "✨ История диалога очищена. Начинаем с чистого листа!"
MAX_CONVERSATION_MESSAGES = 30  # Keep last N messages in context
MAX_TEXT_RESPONSE_LENGTH = 4000  # Telegram message limit

# System instruction for the AI assistant
AI_SYSTEM_INSTRUCTION = """You are a helpful, friendly AI assistant in a Telegram bot with image generation capabilities.

CAPABILITIES:
1. Answer questions on any topic
2. Generate images when asked
3. Analyze and describe images sent by the user
4. Edit or modify images based on user requests
5. Remember full conversation context including generated images

IMAGE EDITING RULES (CRITICAL):
- When user asks to modify a previously generated image (e.g. "make it pink", "add a hat", "change background"), you MUST use the exact same image from context and apply ONLY the requested changes
- Preserve ALL aspects of the original image: composition, style, lighting, perspective, characters, objects - change ONLY what the user specifically asked to change
- If user says "make HER pink" or "add a hat to IT", refer to the last generated image and modify that exact image
- Keep the same art style, color palette (except requested changes), proportions, and overall aesthetic
- Do NOT regenerate the image from scratch - edit the existing one

GENERAL GUIDELINES:
- Be concise but helpful
- Use emoji occasionally to be friendly 😊
- Respond in the same language the user writes in
- When generating NEW images, briefly describe what you're creating
- When EDITING images, just confirm what you changed
"""
RETRY_BUTTON_TEXT = "🔁 Попробовать ещё"
RETRY_CALLBACK_DATA = "retry_last"
COPY_BUTTON_TEXT = "📋 Скопировать"
COPY_CALLBACK_DATA = "copy_prompt"
EDIT_BUTTON_TEXT = "✏️ Отредактировать и повторить"
EDIT_CALLBACK_DATA = "edit_prompt"
STYLE_CALLBACK_PREFIX = "style:"
RETRY_BUSY_MESSAGE = "Генерация уже идёт"
NO_LAST_REQUEST_MESSAGE = "Нет сохранённого запроса для повтора."
PROMPT_PREFIX = "🧠 Prompt: "
STYLE_PROMPT_MESSAGE = "Выбери стиль генерации:"
EDIT_PROMPT_MESSAGE = "Пришли новый текст промпта.\nМодель и стиль оставлю прежними."
FINAL_MESSAGE_TEMPLATE = "🎨 Стиль: {style_name}\n\n🧠 Prompt:\n{prompt}"
COUNT_PROMPT_MESSAGE = "Сколько вариантов сгенерировать? Выбери 1-4."
VARIANTS_MIN = 1
VARIANTS_MAX = 4
MAX_IN_FLIGHT = 2
CAPTION_PROMPT_LIMIT = 900
VARIANT_HINTS = (
    "мягче свет",
    "чуть больше контраста",
    "чуть теплее",
    "более кинематографично",
)


@dataclass(frozen=True, slots=True)
class StyleDefinition:
    id: str
    title: str
    system_prompt: str | None


CINEMATIC_REALISM_PROMPT = (
    "You are generating a highly cinematic, photorealistic image.\n"
    "Visual style inspired by high-end cinema photography and modern film grading.\n\n"
    "Key characteristics:\n"
    "- realistic human proportions and anatomy\n"
    "- natural skin texture, pores, imperfections\n"
    "- cinematic lighting (soft key light, subtle rim light, controlled shadows)\n"
    "- shallow depth of field where appropriate\n"
    "- professional color grading (film-like contrast, rich but natural tones)\n"
    "- dramatic but realistic atmosphere\n"
    "- no cartoonish or stylized exaggerations\n\n"
    "The result should look like a frame from a high-budget movie, shot on a professional cinema camera."
)
HYPERREALISM_PROMPT = (
    "You are generating an ultra-detailed hyperrealistic image.\n\n"
    "Key characteristics:\n"
    "- extreme level of detail and sharpness\n"
    "- highly realistic textures (skin, fabric, materials)\n"
    "- accurate lighting with physically plausible reflections\n"
    "- realistic imperfections and micro-details\n"
    "- high-resolution photographic look\n"
    "- no stylization, no illustration, no painterly effects\n\n"
    "The image should look more realistic than a photograph, as if captured with perfect lighting and optics."
)
DIGITAL_ILLUSTRATION_PROMPT = (
    "You are generating a high-quality digital illustration.\n\n"
    "Key characteristics:\n"
    "- clearly illustrated, non-photorealistic style\n"
    "- painterly or clean digital brush strokes\n"
    "- intentional stylization of shapes and forms\n"
    "- expressive lighting and color composition\n"
    "- artistic interpretation over realism\n"
    "- smooth gradients or textured strokes depending on composition\n\n"
    "The image should look like a premium digital artwork created by a professional concept artist."
)
CARTOON_PROMPT = (
    "You are generating a cartoon-style image.\n\n"
    "Key characteristics:\n"
    "- stylized proportions and simplified anatomy\n"
    "- clean shapes and readable silhouettes\n"
    "- expressive facial features and emotions\n"
    "- bold, clear colors\n"
    "- smooth or cel-shaded rendering\n"
    "- playful or animated look\n\n"
    "The image should look like a high-quality animated cartoon or modern animated film frame, not photorealistic."
)
DARK_MYTHOLOGY_PROMPT = (
    "You are generating a dark, epic, mythological image.\n\n"
    "Key characteristics:\n"
    "- monumental and powerful composition\n"
    "- dark fantasy atmosphere\n"
    "- dramatic, high-contrast lighting\n"
    "- deep shadows, volumetric fog, smoke or mist\n"
    "- mythological, god-like or legendary presence\n"
    "- epic scale and emotional intensity\n\n"
    "The image should feel ancient, powerful, and cinematic, like dark mythological concept art or epic fantasy illustration."
)
SCI_FI_PROMPT = (
    "You are generating a futuristic science fiction image.\n\n"
    "Key characteristics:\n"
    "- advanced technology and futuristic design\n"
    "- neon accents, holograms, or high-tech materials\n"
    "- clean but complex shapes\n"
    "- controlled artificial lighting\n"
    "- cyberpunk or sci-fi atmosphere (depending on prompt)\n"
    "- sense of technological advancement\n\n"
    "The image should look like high-end sci-fi concept art or a frame from a futuristic movie."
)
MINIMAL_ART_PROMPT = (
    "You are generating a minimalistic artistic image.\n\n"
    "Key characteristics:\n"
    "- simple, clean composition\n"
    "- strong focus on subject\n"
    "- limited color palette\n"
    "- intentional use of negative space\n"
    "- artistic framing and balance\n"
    "- calm, aesthetic mood\n\n"
    "The image should feel like modern art photography or gallery-level visual art."
)

STYLE_NONE = StyleDefinition(id="none", title="🧼 Без стиля", system_prompt=None)
STYLES: tuple[StyleDefinition, ...] = (
    STYLE_NONE,
    StyleDefinition(
        id="cinematic_realism",
        title="🎬 Кинематографичный реализм",
        system_prompt=CINEMATIC_REALISM_PROMPT,
    ),
    StyleDefinition(
        id="hyperrealism",
        title="📸 Гиперреализм",
        system_prompt=HYPERREALISM_PROMPT,
    ),
    StyleDefinition(
        id="digital_illustration",
        title="🎨 Иллюстрация / Digital Art",
        system_prompt=DIGITAL_ILLUSTRATION_PROMPT,
    ),
    StyleDefinition(
        id="cartoon",
        title="🧸 Мультфильм",
        system_prompt=CARTOON_PROMPT,
    ),
    StyleDefinition(
        id="dark_mythology",
        title="🗿 Тёмная мифология",
        system_prompt=DARK_MYTHOLOGY_PROMPT,
    ),
    StyleDefinition(
        id="sci_fi",
        title="🤖 Футуризм / Sci-Fi",
        system_prompt=SCI_FI_PROMPT,
    ),
    StyleDefinition(
        id="minimal_art",
        title="🖼 Минимализм / Арт-фото",
        system_prompt=MINIMAL_ART_PROMPT,
    ),
)
STYLE_BY_ID = {style.id: style for style in STYLES}


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


class EditPromptState(StatesGroup):
    waiting = State()


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


def _get_style(style_id: str | None) -> StyleDefinition:
    if not style_id:
        return STYLE_NONE
    return STYLE_BY_ID.get(style_id, STYLE_NONE)


def _build_style_keyboard(selected_style_id: str | None) -> InlineKeyboardMarkup:
    selected_style = _get_style(selected_style_id)
    rows: list[list[InlineKeyboardButton]] = []
    for style in STYLES:
        label = style.title
        if style.id == selected_style.id:
            label = f"✅ {label}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{STYLE_CALLBACK_PREFIX}{style.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_final_prompt(user_prompt: str, style_id: str) -> str:
    style = _get_style(style_id)
    if style.system_prompt is None:
        return user_prompt
    return f"{style.system_prompt}\n\nUSER PROMPT:\n{user_prompt}"


def _format_final_message(style_id: str, prompt: str) -> str:
    style = _get_style(style_id)
    return FINAL_MESSAGE_TEMPLATE.format(style_name=style.title, prompt=prompt)


def _build_final_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=RETRY_BUTTON_TEXT,
                    callback_data=RETRY_CALLBACK_DATA,
                )
            ],
            [
                InlineKeyboardButton(
                    text=COPY_BUTTON_TEXT,
                    callback_data=COPY_CALLBACK_DATA,
                )
            ],
            [
                InlineKeyboardButton(
                    text=EDIT_BUTTON_TEXT,
                    callback_data=EDIT_CALLBACK_DATA,
                )
            ],
        ]
    )


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


def _normalize_variants_count(value: int) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return VARIANTS_MIN
    return min(VARIANTS_MAX, max(VARIANTS_MIN, count))


def _build_variants_keyboard(selected: int) -> InlineKeyboardMarkup:
    selected = _normalize_variants_count(selected)
    row: list[InlineKeyboardButton] = []
    for count in range(VARIANTS_MIN, VARIANTS_MAX + 1):
        label = f"✅ {count}" if count == selected else str(count)
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"count:{count}",
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _build_variant_prompt(base_prompt: str, variant_index: int) -> str:
    hint = VARIANT_HINTS[(variant_index - 1) % len(VARIANT_HINTS)]
    return f"{base_prompt}\n\nВариант {variant_index}: {hint}"


def _format_partial_failure(failed: int, total: int) -> str:
    verb = "не получился" if failed == 1 else "не получилось"
    return f"{failed} из {total} {verb}"


def _get_user_lock(locks: dict[int, asyncio.Lock], user_id: int) -> asyncio.Lock:
    if user_id not in locks:
        locks[user_id] = asyncio.Lock()
    return locks[user_id]


def _split_long_message(text: str, max_length: int = MAX_TEXT_RESPONSE_LENGTH) -> list[str]:
    """Split long message into chunks for Telegram."""
    if len(text) <= max_length:
        return [text]
    
    chunks: list[str] = []
    current = ""
    
    for line in text.split("\n"):
        if len(current) + len(line) + 1 <= max_length:
            current = f"{current}\n{line}" if current else line
        else:
            if current:
                chunks.append(current)
            # If single line is too long, split it
            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]
            current = line
    
    if current:
        chunks.append(current)
    
    return chunks if chunks else [""]


def _conversation_to_chat_messages(
    messages: list[ConversationMessage],
) -> list[ChatMessage]:
    """Convert stored conversation to API format."""
    result: list[ChatMessage] = []
    for msg in messages:
        result.append(ChatMessage(
            role=msg.role,
            text=msg.text,
            image_data=msg.image_data,
        ))
    return result


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


async def _send_generation_results(
    bot: Bot,
    chat_id: int,
    images: list[bytes],
    prompt: str,
    style_id: str,
) -> None:
    if len(images) == 1:
        await bot.send_photo(
            chat_id,
            BufferedInputFile(images[0], filename="result.png"),
        )
    else:
        for index, image in enumerate(images, start=1):
            await bot.send_photo(
                chat_id,
                BufferedInputFile(image, filename=f"result_{index}.png"),
            )
    await bot.send_message(
        chat_id,
        _format_final_message(style_id, prompt),
        reply_markup=_build_final_actions_keyboard(),
        parse_mode=None,
    )


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

    async def _send_error(
        bot: Bot,
        chat_id: int,
        user_id: int,
        message: str = ERROR_MESSAGE,
    ) -> None:
        error_message = await _send_aux(
            bot,
            storage,
            user_id,
            chat_id,
            message,
        )
        keep = {(error_message.chat.id, error_message.message_id)}
        await _delete_aux_messages(bot, storage, user_id, keep=keep)

    async def _run_ai_chat(
        *,
        bot: Bot,
        chat_id: int,
        user_id: int,
        model_id: str,
        user_text: str,
        user_image_data: list[dict],
    ) -> bool:
        """
        Main AI chat function - handles conversation with context.
        Returns True on success, False on failure.
        """
        try:
            # Get conversation history
            history = await storage.get_conversation(user_id, max_messages=MAX_CONVERSATION_MESSAGES)
            
            # Create user message and add to history
            user_msg = ConversationMessage(
                role="user",
                text=user_text,
                image_data=user_image_data,
            )
            await storage.add_to_conversation(user_id, user_msg, max_messages=MAX_CONVERSATION_MESSAGES)
            
            # Build messages for API
            chat_messages = _conversation_to_chat_messages(history)
            chat_messages.append(ChatMessage(
                role="user",
                text=user_text,
                image_data=user_image_data,
            ))
            
            # Call AI API
            response: ChatResponse = await api_client.chat(
                model_id,
                chat_messages,
                system_instruction=AI_SYSTEM_INSTRUCTION,
            )
            
            # Process response
            await _delete_aux_messages(bot, storage, user_id)
            
            # Send text response
            if response.text:
                chunks = _split_long_message(response.text)
                for chunk in chunks:
                    await bot.send_message(chat_id, chunk, parse_mode=None)
            
            # Send images if any
            for i, image_bytes in enumerate(response.images):
                await bot.send_photo(
                    chat_id,
                    BufferedInputFile(image_bytes, filename=f"image_{i+1}.png"),
                )
            
            # Save model response to conversation with full image_parts (includes thought_signature)
            # Limit to 2 images to avoid huge history
            response_image_parts = response.image_parts[:2] if response.image_parts else []
            
            model_msg = ConversationMessage(
                role="model",
                text=response.text if response.text else "",
                image_data=response_image_parts,
            )
            await storage.add_to_conversation(user_id, model_msg, max_messages=MAX_CONVERSATION_MESSAGES)
            
            return True
            
        except ApiError as exc:
            logging.exception("AI chat failed: %s", exc)
            await _send_error(bot, chat_id, user_id, f"{ERROR_MESSAGE}\n\n{exc}")
            return False
        except Exception as exc:
            logging.exception("Unexpected AI chat error: %s", exc)
            await _send_error(bot, chat_id, user_id)
            return False

    # Keep legacy function for backwards compatibility with retry
    async def _send_generation_error(
        bot: Bot,
        chat_id: int,
        user_id: int,
    ) -> None:
        await _send_error(bot, chat_id, user_id)

    async def _send_partial_failure_notice(
        bot: Bot,
        chat_id: int,
        user_id: int,
        *,
        failed: int,
        total: int,
    ) -> None:
        message = _format_partial_failure(failed, total)
        error_message = await _send_aux(
            bot,
            storage,
            user_id,
            chat_id,
            message,
        )
        keep = {(error_message.chat.id, error_message.message_id)}
        await _delete_aux_messages(bot, storage, user_id, keep=keep)

    async def _run_generation(
        *,
        bot: Bot,
        chat_id: int,
        user_id: int,
        model_id: str,
        prompt: str,
        style_id: str,
        photo_file_ids: list[str],
        variants_count: int,
    ) -> bool:
        paths: list[str] = []
        try:
            for index, file_id in enumerate(photo_file_ids, start=1):
                path = await _download_photo(
                    bot,
                    file_id,
                    temp_dir=settings.temp_dir,
                    user_id=user_id,
                    index=index,
                )
                paths.append(path)
            if variants_count <= 1:
                image = await api_client.generate_image(
                    model_id,
                    paths,
                    prompt,
                )
                await _send_generation_results(bot, chat_id, [image], prompt, style_id)
                await _delete_aux_messages(bot, storage, user_id)
                return True

            semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)

            async def _generate_variant(index: int) -> tuple[int, bytes | None, Exception | None]:
                variant_prompt = _build_variant_prompt(prompt, index)
                try:
                    async with semaphore:
                        image = await api_client.generate_image(
                            model_id,
                            paths,
                            variant_prompt,
                        )
                    return index, image, None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    return index, None, exc

            tasks = [
                asyncio.create_task(_generate_variant(index))
                for index in range(1, variants_count + 1)
            ]
            success_count = 0
            failure_count = 0
            for task in asyncio.as_completed(tasks):
                try:
                    index, image, error = await task
                except Exception as exc:
                    logging.exception("Unexpected generation error: %s", exc)
                    failure_count += 1
                    continue
                if error is not None:
                    if isinstance(error, ApiError):
                        logging.exception(
                            "Generation failed for variant %s: %s",
                            index,
                            error,
                        )
                    else:
                        logging.exception(
                            "Unexpected generation error for variant %s: %s",
                            index,
                            error,
                        )
                    failure_count += 1
                    continue
                if image is None:
                    failure_count += 1
                    continue
                success_count += 1
                await bot.send_photo(
                    chat_id,
                    BufferedInputFile(image, filename=f"result_{index}.png"),
                )

            if success_count:
                await bot.send_message(
                    chat_id,
                    _format_final_message(style_id, prompt),
                    reply_markup=_build_final_actions_keyboard(),
                    parse_mode=None,
                )
                if failure_count:
                    await _send_partial_failure_notice(
                        bot,
                        chat_id,
                        user_id,
                        failed=failure_count,
                        total=variants_count,
                    )
                else:
                    await _delete_aux_messages(bot, storage, user_id)
                return True

            await _send_generation_error(bot, chat_id, user_id)
            return False
        except ApiError as exc:
            logging.exception("Generation failed: %s", exc)
            await _send_generation_error(bot, chat_id, user_id)
        except Exception as exc:
            logging.exception("Unexpected generation error: %s", exc)
            await _send_generation_error(bot, chat_id, user_id)
        finally:
            await _cleanup_paths(paths, temp_dir=settings.temp_dir, user_id=user_id)
        return False

    async def _process_snapshots(
        snapshots: list[MessageSnapshot],
        *,
        bot: Bot,
        chat_id: int,
        user_id: int,
        state: FSMContext,
    ) -> None:
        """Process user message(s) through AI chat with context."""
        ordered = sorted(snapshots, key=lambda item: item.message_id)
        prompt = _extract_prompt(ordered)
        prompt_present = bool(prompt and prompt.strip())
        photo_ids = _extract_photo_ids(ordered)
        lock = _get_user_lock(locks, user_id)

        # If only photos without text, store them and ask what to do
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

        # Check if already processing
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

        # Check if model is selected
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

        # Try to acquire lock
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

        # Collect images (from message or pending)
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

        success = False
        try:
            # Show thinking indicator
            await _send_aux(
                bot,
                storage,
                user_id,
                chat_id,
                THINKING_MESSAGE,
            )
            
            # Download and encode images for API
            user_image_data: list[dict] = []
            paths_to_cleanup: list[str] = []
            
            for index, file_id in enumerate(active_photo_ids, start=1):
                try:
                    path = await _download_photo(
                        bot,
                        file_id,
                        temp_dir=settings.temp_dir,
                        user_id=user_id,
                        index=index,
                    )
                    paths_to_cleanup.append(path)
                    image_data = await encode_image_from_path(path)
                    user_image_data.append(image_data)
                except Exception as exc:
                    logging.warning("Failed to download photo %s: %s", file_id, exc)
            
            # Run AI chat
            success = await _run_ai_chat(
                bot=bot,
                chat_id=chat_id,
                user_id=user_id,
                model_id=selected_model,
                user_text=prompt,
                user_image_data=user_image_data,
            )
            
            # Cleanup downloaded files
            await _cleanup_paths(paths_to_cleanup, temp_dir=settings.temp_dir, user_id=user_id)
            
        finally:
            lock.release()
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

    @router.message(Command("count"))
    async def handle_count(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        await _clear_aux_if_idle(message.bot, user_id)
        selected = _normalize_variants_count(
            await storage.get_variants_count(user_id)
        )
        await _send_aux(
            message.bot,
            storage,
            user_id,
            message.chat.id,
            COUNT_PROMPT_MESSAGE,
            reply_markup=_build_variants_keyboard(selected),
        )

    @router.message(Command("style"))
    async def handle_style(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        await _clear_aux_if_idle(message.bot, user_id)
        selected_style = await storage.get_selected_style(user_id)
        await _send_aux(
            message.bot,
            storage,
            user_id,
            message.chat.id,
            STYLE_PROMPT_MESSAGE,
            reply_markup=_build_style_keyboard(selected_style),
        )

    @router.message(Command("clear"))
    async def handle_clear(message: Message) -> None:
        """Clear conversation history."""
        user_id = message.from_user.id if message.from_user else 0
        await _clear_aux_if_idle(message.bot, user_id)
        await storage.clear_conversation(user_id)
        await message.answer(CONVERSATION_CLEARED_MESSAGE)

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

    @router.callback_query(F.data.startswith("count:"))
    async def handle_count_select(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        raw = callback.data.split(":", 1)[1]
        try:
            selected = _normalize_variants_count(int(raw))
        except ValueError:
            await callback.answer("Некорректное значение", show_alert=True)
            return
        await storage.set_variants_count(user_id, selected)
        await callback.answer(f"Буду генерировать {selected}")
        if callback.message:
            await _safe_delete(
                callback.message.bot,
                callback.message.chat.id,
                callback.message.message_id,
            )

    @router.callback_query(F.data.startswith(STYLE_CALLBACK_PREFIX))
    async def handle_style_select(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        style_id = callback.data.split(":", 1)[1]
        style = _get_style(style_id)
        if style.id != style_id:
            await callback.answer("Неизвестный стиль", show_alert=True)
            return
        await storage.set_selected_style(user_id, style.id)
        await callback.answer(f"Стиль: {style.title}")
        if callback.message:
            await callback.message.edit_reply_markup(
                reply_markup=_build_style_keyboard(style.id)
            )

    @router.callback_query(F.data == RETRY_CALLBACK_DATA)
    async def handle_retry(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        lock = _get_user_lock(locks, user_id)
        if lock.locked():
            await callback.answer(RETRY_BUSY_MESSAGE, show_alert=True)
            return

        last_request = await storage.get_last_request(user_id)
        if not last_request:
            await callback.answer(NO_LAST_REQUEST_MESSAGE, show_alert=True)
            return

        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.01)
        except asyncio.TimeoutError:
            await callback.answer(RETRY_BUSY_MESSAGE, show_alert=True)
            return

        await callback.answer()
        chat_id = callback.message.chat.id if callback.message else callback.from_user.id
        await _delete_aux_messages(callback.bot, storage, user_id)

        try:
            style = _get_style(last_request.style_id)
            final_prompt = _build_final_prompt(last_request.prompt, style.id)
            await _send_aux(
                callback.bot,
                storage,
                user_id,
                chat_id,
                GENERATING_MESSAGE,
            )
            await _run_generation(
                bot=callback.bot,
                chat_id=chat_id,
                user_id=user_id,
                model_id=last_request.model_id,
                prompt=final_prompt,
                style_id=style.id,
                photo_file_ids=last_request.photo_file_ids,
                variants_count=1,
            )
        finally:
            lock.release()

    @router.callback_query(F.data == COPY_CALLBACK_DATA)
    async def handle_copy_prompt(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        last_request = await storage.get_last_request(user_id)
        if not last_request:
            await callback.answer(NO_LAST_REQUEST_MESSAGE, show_alert=True)
            return
        style = _get_style(last_request.style_id)
        final_prompt = _build_final_prompt(last_request.prompt, style.id)
        await callback.answer()
        chat_id = callback.message.chat.id if callback.message else callback.from_user.id
        await callback.bot.send_message(chat_id, final_prompt, parse_mode=None)

    @router.callback_query(F.data == EDIT_CALLBACK_DATA)
    async def handle_edit_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        last_request = await storage.get_last_request(user_id)
        if not last_request:
            await callback.answer(NO_LAST_REQUEST_MESSAGE, show_alert=True)
            return
        await callback.answer()
        chat_id = callback.message.chat.id if callback.message else callback.from_user.id
        await callback.bot.send_message(chat_id, EDIT_PROMPT_MESSAGE)
        await state.set_state(EditPromptState.waiting)

    @router.message(StateFilter(EditPromptState.waiting))
    async def handle_edit_message(message: Message, state: FSMContext) -> None:
        if message.text and message.text.startswith("/"):
            raise SkipHandler()
        user_id = message.from_user.id if message.from_user else 0
        prompt = message.text or message.caption or ""
        if not prompt.strip():
            await _send_aux(
                message.bot,
                storage,
                user_id,
                message.chat.id,
                NEED_PROMPT_MESSAGE,
            )
            return
        last_request = await storage.get_last_request(user_id)
        if not last_request:
            await state.clear()
            await _send_aux(
                message.bot,
                storage,
                user_id,
                message.chat.id,
                NO_LAST_REQUEST_MESSAGE,
            )
            return

        lock = _get_user_lock(locks, user_id)
        if lock.locked():
            await _send_aux(
                message.bot,
                storage,
                user_id,
                message.chat.id,
                WAIT_MESSAGE,
            )
            return

        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.01)
        except asyncio.TimeoutError:
            await _send_aux(
                message.bot,
                storage,
                user_id,
                message.chat.id,
                WAIT_MESSAGE,
            )
            return

        await state.clear()
        await _delete_aux_messages(message.bot, storage, user_id)

        style = _get_style(last_request.style_id)
        final_prompt = _build_final_prompt(prompt, style.id)
        try:
            await _send_aux(
                message.bot,
                storage,
                user_id,
                message.chat.id,
                GENERATING_MESSAGE,
            )
            await storage.set_last_request(
                user_id,
                last_request.model_id,
                prompt,
                style.id,
                last_request.photo_file_ids,
                last_request.variants_count,
            )
            await _run_generation(
                bot=message.bot,
                chat_id=message.chat.id,
                user_id=user_id,
                model_id=last_request.model_id,
                prompt=final_prompt,
                style_id=style.id,
                photo_file_ids=last_request.photo_file_ids,
                variants_count=last_request.variants_count,
            )
        finally:
            lock.release()

    @router.message(~F.text.startswith("/"), StateFilter(None))
    async def handle_user_message(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id if message.from_user else 0
        snapshot = _snapshot_message(message)

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
            BotCommand(command="clear", description="Очистить историю диалога"),
            BotCommand(command="swap", description="Сменить модель"),
            BotCommand(command="count", description="Количество вариантов (1-4)"),
            BotCommand(command="style", description="Стиль генерации"),
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
