# NanoCraft Telegram Bot

Simple aiogram 3 bot that lets users pick an image-capable model, send photos,
and provide a prompt to generate a single output image. User model selection is
stored in SQLite; generation history is not stored.

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

## Model list

On startup the bot requests the model catalog from the API base URL and filters
for image-capable `generateContent` models using `MODEL_KEYWORDS`. You can narrow
the list by setting `MODEL_ALLOWLIST` (comma-separated model ids).

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
