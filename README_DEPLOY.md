# Руководство по развёртыванию

NanoCraft Telegram Bot — AI-ассистент с поддержкой генерации изображений и памятью контекста диалога.

## Возможности

- 💬 **AI-чат** — общение как с ChatGPT, с памятью контекста
- 🎨 **Генерация изображений** — создание картинок по описанию
- 📷 **Анализ фото** — распознавание и описание изображений
- ✏️ **Редактирование** — "сделай ярче", "добавь котика" с учётом контекста
- 🔄 **Мультимодальность** — комбинация текста и изображений в одном диалоге

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Старт и выбор модели |
| `/clear` | Очистить историю диалога |
| `/swap` | Сменить модель |
| `/count` | Количество вариантов (1-4) |
| `/style` | Стиль генерации |

---

## Требования

- Debian/Ubuntu сервер (или совместимый дистрибутив)
- Python 3.10+
- Доступ к Gemini/NanoBanana API

---

## Шаг 1. Установка системных пакетов

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip build-essential curl ca-certificates
python3 --version
```

> Если Python < 3.10, установите более новую версию через deadsnakes или pyenv.

---

## Шаг 2. Создание пользователя и директорий

```bash
# Создаём системного пользователя
sudo useradd --system --create-home --home-dir /home/nanobot --shell /usr/sbin/nologin nanobot

# Создаём директории
sudo mkdir -p /opt/nanobot /var/lib/nanobot/tmp /var/log/nanobot

# Настраиваем права
sudo chown -R root:root /opt/nanobot
sudo chmod 755 /opt/nanobot
sudo chown -R nanobot:nanobot /var/lib/nanobot /var/log/nanobot
```

---

## Шаг 3. Клонирование репозитория

```bash
sudo git clone <URL_РЕПОЗИТОРИЯ> /opt/nanobot
sudo chown -R root:root /opt/nanobot
sudo chmod -R go-w /opt/nanobot
```

> Для приватного репозитория добавьте deploy key или используйте URL с токеном.

---

## Шаг 4. Создание виртуального окружения

```bash
sudo -H python3 -m venv /opt/nanobot/.venv
sudo /opt/nanobot/.venv/bin/pip install -r /opt/nanobot/requirements.txt
```

---

## Шаг 5. Настройка переменных окружения

Создайте файл `/opt/nanobot/.env`:

```bash
sudo nano /opt/nanobot/.env
```

Содержимое:

```ini
BOT_TOKEN=ваш_токен_бота
NANOBANANA_API_KEY=ваш_api_ключ
DATABASE_PATH=/var/lib/nanobot/bot.db
TEMP_DIR=/var/lib/nanobot/tmp
LOG_LEVEL=INFO
```

Настройте права доступа:

```bash
sudo chown nanobot:nanobot /opt/nanobot/.env
sudo chmod 600 /opt/nanobot/.env
```

### Опциональные переменные

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `MODEL_ALLOWLIST` | Список разрешённых моделей (через запятую) | все модели |
| `MODEL_KEYWORDS` | Ключевые слова для фильтрации моделей | `image,nano-banana,banana` |
| `REQUEST_TIMEOUT` | Таймаут запросов к API (сек) | `120` |
| `TEMP_MESSAGE_TTL` | Время жизни временных сообщений (сек) | `8.0` |

---

## Шаг 6. Создание systemd-сервиса

Создайте файл `/etc/systemd/system/nanobot.service`:

```bash
sudo nano /etc/systemd/system/nanobot.service
```

Содержимое:

```ini
[Unit]
Description=NanoCraft Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nanobot
Group=nanobot
WorkingDirectory=/opt/nanobot
EnvironmentFile=/opt/nanobot/.env
ExecStart=/opt/nanobot/.venv/bin/python /opt/nanobot/main.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
```

Активируйте и запустите:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nanobot.service
sudo systemctl status nanobot.service --no-pager
```

---

## Шаг 7. Проверка логов

```bash
# Последние 200 строк
journalctl -u nanobot.service -n 200 --no-pager

# В реальном времени
journalctl -u nanobot.service -f
```

---

## Шаг 8. Тестирование

Проверьте, что бот работает:

1. `systemctl status nanobot` — статус `active (running)`
2. В логах нет ошибок
3. В Telegram:
   - `/start` → выбор модели
   - "Привет!" → AI отвечает
   - "Нарисуй котика" → генерация изображения
   - Отправить фото + "Что это?" → анализ изображения
   - "Сделай ярче" → редактирование с учётом контекста
   - `/clear` → очистка истории

---

## Обновление бота

Сделайте скрипт исполняемым:

```bash
sudo chmod +x /opt/nanobot/scripts/update.sh
```

Запуск обновления:

```bash
sudo /opt/nanobot/scripts/update.sh
```

Обновление с указанием ветки:

```bash
sudo BRANCH=main /opt/nanobot/scripts/update.sh
```

---

## Откат изменений

Скрипт обновления выводит хэш предыдущего коммита. Для отката:

```bash
cd /opt/nanobot
sudo git checkout <ПРЕДЫДУЩИЙ_КОММИТ>
sudo systemctl restart nanobot.service
```

Возврат к ветке:

```bash
sudo git checkout main
sudo /opt/nanobot/scripts/update.sh
```

---

## Расположение файлов

| Что | Путь |
|-----|------|
| Код | `/opt/nanobot` |
| Конфигурация | `/opt/nanobot/.env` |
| База данных | `/var/lib/nanobot/bot.db` |
| Временные файлы | `/var/lib/nanobot/tmp` |
