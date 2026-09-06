#!/usr/bin/env bash
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo "Starting Stechpay Backend and Frontend..."
echo "========================================"

# Launch Backend
(
  cd "$ROOT_DIR/backend"
  if [ -f "appenv/Scripts/activate" ]; then
    source appenv/Scripts/activate
  elif [ -f "appenv/bin/activate" ]; then
    source appenv/bin/activate
  fi
  python manage.py runserver
) &
BACKEND_PID=$!

# Launch Frontend
(
  cd "$ROOT_DIR/frontend/stechpay"
  npm run dev
) &
FRONTEND_PID=$!

echo "Backend PID: $BACKEND_PID (http://127.0.0.1:8000/)"
echo "Frontend PID: $FRONTEND_PID (http://localhost:5173/)"
echo "Press Ctrl+C to stop both servers."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" SIGINT SIGTERM EXIT
wait
