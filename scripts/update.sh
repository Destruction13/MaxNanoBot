#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/nanobot"
SERVICE_NAME="${SERVICE_NAME:-nanobot}"
BRANCH="${BRANCH:-main}"

if [[ ! -d "$PROJECT_DIR/.git" ]]; then
  echo "ERROR: $PROJECT_DIR is not a git repository" >&2
  exit 1
fi

cd "$PROJECT_DIR"

previous_commit="$(git rev-parse HEAD)"
echo "Previous commit: $previous_commit"

echo "Fetching origin..."
git fetch origin

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$current_branch" != "$BRANCH" ]]; then
  echo "Switching to branch: $BRANCH"
  git checkout "$BRANCH"
fi

echo "Pulling origin/$BRANCH..."
git pull --ff-only origin "$BRANCH"

current_commit="$(git rev-parse HEAD)"
echo "Current commit: $current_commit"

VENV_PY="$PROJECT_DIR/.venv/bin/python"
VENV_PIP="$PROJECT_DIR/.venv/bin/pip"

if [[ ! -x "$VENV_PY" ]]; then
  echo "ERROR: venv not found at $VENV_PY" >&2
  exit 1
fi

echo "Installing dependencies..."
"$VENV_PIP" install -r requirements.txt

if [[ $EUID -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

echo "Restarting service: $SERVICE_NAME"
$SUDO systemctl restart "$SERVICE_NAME.service"
$SUDO systemctl status "$SERVICE_NAME.service" --no-pager
