#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/srv/stechpay

cd "$APP_DIR"
git pull --ff-only origin main

if [ -f /etc/stechpay.env ]; then
  set -a
  source /etc/stechpay.env
  set +a
fi

"$APP_DIR/backend/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"

cd "$APP_DIR/frontend/stechpay"
npm ci
npm run build

cd "$APP_DIR/backend"
"$APP_DIR/backend/.venv/bin/python" manage.py migrate --noinput
"$APP_DIR/backend/.venv/bin/python" manage.py collectstatic --noinput

sudo systemctl restart stechpay
sudo nginx -t
sudo systemctl reload nginx
