# NanoCraft Telegram Bot

AI-ассистент в Telegram с поддержкой генерации изображений и памятью контекста диалога.

## Возможности

- 💬 **AI-чат** — общение как с ChatGPT, бот помнит контекст разговора
- 🎨 **Генерация изображений** — создание картинок по текстовому описанию
- 📷 **Анализ фото** — распознавание и описание отправленных изображений
- ✏️ **Редактирование с контекстом** — "сделай ярче", "добавь котика" к предыдущей картинке
- 🔄 **Мультимодальность** — комбинация текста и изображений в одном диалоге
- 🧠 **Память диалога** — до 30 сообщений в контексте

Built with aiogram 3, uses Gemini/NanoBanana API for AI capabilities.
User settings and conversation history stored in SQLite.

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Старт и выбор модели |
| `/clear` | Очистить историю диалога |
| `/swap` | Сменить модель |
| `/count` | Количество вариантов (1-4) |
| `/style` | Стиль генерации |

## Setup

1. Create a `.env` file based on `.env.example`.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the bot:

```bash
python main.py
```

For production deployment, see [README_DEPLOY.md](README_DEPLOY.md).

## Model list

On startup the bot requests the model catalog from the API base URL and filters
for image-capable `generateContent` models using `MODEL_KEYWORDS`. You can narrow
the list by setting `MODEL_ALLOWLIST` (comma-separated model ids).

## Variants & retry

- Use `/count` to pick how many variants to generate (1-4); this becomes the per-user default.
- The latest request is stored in `last_request` with `model_id`, `prompt`,
  `photo_file_ids`, and `variants_count`.
- Variants are generated as separate requests (max 2 in flight per user).
- Retry always uses the same model/prompt/photos but forces exactly 1 variant.

## Manual checklist

- /start → choose model → send text only → generation starts.
- send text + 5 photos → generation starts with all 5 in order.
- send 2 photos without caption → generation does not start, photos are stored.
- send text after photo-only → generation starts with stored photos, pending cleared.
- send photo-only, then text + photo → pending cleared, current photos used.
- send text with no pending images → generation runs with text only.
- /swap during generation → current generation completes, new model applies next run.
- "Генерирую..." исчезает сразу после результата, без таймера.
- "Фото принял..." исчезает при получении промпта и старте генерации.
- "Подожди..." исчезает после завершения генерации.
- variants=4 (no photos) -> 4 separate images + prompt message.
- variants=3 (with photos) -> 3 separate images + prompt message.
- generation error -> retry button appears -> retry returns 1 image.
- change variants to 4 -> generate -> retry -> still returns 1 image.
- spam retry quickly -> only one generation runs at a time.
- variants=4 with one failure -> prompt sent + partial retry notice.
