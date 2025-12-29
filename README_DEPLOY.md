# Deployment Guide (systemd)

Assumes a Debian/Ubuntu-like server. Adjust paths if you choose a different project name.

## 1) System packages

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip build-essential curl ca-certificates
python3 --version
```

If Python < 3.10, install a newer version (for example via deadsnakes) or use pyenv.

## 2) User and directories

```bash
sudo useradd --system --create-home --home-dir /home/nanobot --shell /usr/sbin/nologin nanobot
sudo mkdir -p /opt/nanobot /var/lib/nanobot/tmp /var/log/nanobot
sudo chown -R root:root /opt/nanobot
sudo chmod 755 /opt/nanobot
sudo chown -R nanobot:nanobot /var/lib/nanobot /var/log/nanobot
```

## 3) Clone code

```bash
sudo git clone <REPO_URL> /opt/nanobot
sudo chown -R root:root /opt/nanobot
sudo chmod -R go-w /opt/nanobot
```

If the repo is private, add a deploy key or use a token-based URL.

## 4) Virtualenv and dependencies

```bash
sudo -H python3 -m venv /opt/nanobot/.venv
sudo /opt/nanobot/.venv/bin/pip install -r /opt/nanobot/requirements.txt
```

## 5) .env configuration

Create `/opt/nanobot/.env` (not committed) with at least:

```bash
BOT_TOKEN=...
NANOBANANA_API_KEY=...
DATABASE_PATH=/var/lib/nanobot/bot.db
TEMP_DIR=/var/lib/nanobot/tmp
LOG_LEVEL=INFO
```

You can also use `SQLITE_PATH` instead of `DATABASE_PATH` and `TMP_DIR` instead of `TEMP_DIR`.

```bash
sudo chown nanobot:nanobot /opt/nanobot/.env
sudo chmod 600 /opt/nanobot/.env
```

## 6) systemd unit

Create `/etc/systemd/system/nanobot.service`:

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

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nanobot.service
sudo systemctl status nanobot.service --no-pager
```

Logs:

```bash
journalctl -u nanobot.service -n 200 --no-pager
journalctl -u nanobot.service -f
```

## 7) Update script

Make the script executable:

```bash
sudo chmod +x /opt/nanobot/scripts/update.sh
```

Run update (from anywhere):

```bash
sudo /opt/nanobot/scripts/update.sh
```

Change branch if needed:

```bash
sudo BRANCH=main /opt/nanobot/scripts/update.sh
```

## 8) Smoke test

- `systemctl status nanobot` shows active (running)
- `journalctl -u nanobot -n 200 --no-pager` has no tracebacks
- Telegram: `/start`, `/swap`, and a prompt trigger generation

## 9) Rollback

The update script prints the previous commit hash. To rollback:

```bash
cd /opt/nanobot
git checkout <PREVIOUS_HASH>
sudo systemctl restart nanobot.service
```

To return to the branch later:

```bash
git checkout main
sudo /opt/nanobot/scripts/update.sh
```

## Paths

- Code: `/opt/nanobot`
- Env: `/opt/nanobot/.env`
- DB: `/var/lib/nanobot/bot.db`
- Temp files: `/var/lib/nanobot/tmp`
