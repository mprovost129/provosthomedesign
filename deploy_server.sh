#!/usr/bin/env bash
set -euo pipefail

# Server deploy script for Provost Home Design
# Usage (on server):
#   sudo -s
#   bash ./deploy_server.sh
#
# Env overrides (optional):
#   BRANCH=main VENV_DIR=.venv SERVICES=gunicorn bash ./deploy_server.sh

REPO_DIR=${REPO_DIR:-/srv/phdapp}
BRANCH=${BRANCH:-main}
VENV_DIR=${VENV_DIR:-.venv}
SERVICES=${SERVICES:-gunicorn}

echo "🏁 Starting server deploy in $REPO_DIR (branch: $BRANCH)"
cd "$REPO_DIR"

# Pull latest code
echo "⬇️  Pulling latest from origin/$BRANCH"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

# Python venv
if [ -d "$VENV_DIR" ]; then
  echo "🐍 Using existing venv: $VENV_DIR"
  source "$VENV_DIR/bin/activate"
else
  echo "🐍 Creating venv: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
  source "$VENV_DIR/bin/activate"
fi

# Install Python dependencies
echo "📦 Installing requirements"
pip install -r requirements.txt

# Django operations
echo "🗄️  Running migrations"
python manage.py migrate --noinput

echo "📦 Collecting static files"
python manage.py collectstatic --noinput

# Restart app service if managed by systemd
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-enabled --quiet "$SERVICES"; then
    echo "🔄 Restarting service: $SERVICES"
    systemctl restart "$SERVICES"
  else
    echo "ℹ️  Service $SERVICES not enabled in systemd; skipping restart"
  fi
else
  echo "ℹ️  systemctl not available; skipping service restart"
fi

echo "✅ Deploy finished"