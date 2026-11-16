#!/usr/bin/env bash
set -euo pipefail
# The restricted SSH wrapper uses a private umask for its temporary script.
# Installed packages, checked-out code and frontend assets must be service-readable.
umask 022

APP_DIR=/srv/stechpay
TARGET_SHA=${1:?Pass the exact tested Git commit SHA}
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid commit SHA'; exit 1; }

cd "$APP_DIR"
test -z "$(git status --porcelain --untracked-files=no)" || { echo 'Deployment checkout has local changes'; exit 1; }
git fetch origin main
git merge-base --is-ancestor "$TARGET_SHA" origin/main
git checkout --detach "$TARGET_SHA"

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
"$APP_DIR/backend/.venv/bin/python" manage.py check --deploy --fail-level WARNING
sudo -u www-data bash -c 'set -a; source /etc/stechpay.env; set +a; cd /srv/stechpay/backend; .venv/bin/python manage.py check --deploy --fail-level WARNING'
BACKUP_DIR=${STECHPAY_BACKUP_DIR:?Set STECHPAY_BACKUP_DIR to a private PostgreSQL backup directory}
test -d "$BACKUP_DIR" && test -w "$BACKUP_DIR"
# Quiesce writers before the backup and schema changes. Never restore a database
# automatically after a failed release: that could erase newer transactions.
sudo systemctl stop stechpay-payments.timer stechpay-payments.service stechpay
umask 077
BACKUP_FILE="$BACKUP_DIR/stechpay-$(date -u +%Y%m%dT%H%M%SZ)-$TARGET_SHA.dump"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump --host "$POSTGRES_HOST" --port "${POSTGRES_PORT:-5432}" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format custom \
  --file "$BACKUP_FILE"
pg_restore --list "$BACKUP_FILE" > /dev/null
# Static files need to be readable by Nginx; only backups use the private umask.
umask 022
"$APP_DIR/backend/.venv/bin/python" manage.py migrate --noinput
"$APP_DIR/backend/.venv/bin/python" manage.py collectstatic --noinput

sudo systemctl restart stechpay
sudo nginx -t
sudo systemctl reload nginx
ready=false
for attempt in {1..30}; do
  if curl --fail --silent --max-time 5 --unix-socket /run/stechpay/gunicorn.sock \
      -H 'X-Forwarded-Proto: https' http://localhost/api/csrf/ > /dev/null; then
    ready=true
    break
  fi
  sleep 1
done
$ready || { echo 'Application health check failed'; exit 1; }
sudo systemctl enable --now stechpay-payments.timer
echo "Deployed $TARGET_SHA; PostgreSQL backup: $BACKUP_FILE"
