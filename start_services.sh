#!/bin/bash
# Local development launcher for Personal Finance AI (Cloud Shell or any machine with gcloud).
#
# Since Phase 1 (DEC-025) the app runs on Cloud Run (finance-backend / finance-backend-demo) and
# Firebase Hosting. This script no longer starts a Cloudflare tunnel, rewrites .env files or
# redeploys Firebase Hosting, and nothing should run it from cron. Deploys are explicit:
#   backend:  scripts/deploy_backend.sh build
#   frontend: (cd app/frontend && npm run build && firebase deploy --only hosting)
#
# Usage:
#   ./start_services.sh                 # personal backend on http://127.0.0.1:8080
#   ./start_services.sh demo            # demo backend (demo database) on http://127.0.0.1:8081
#   ./start_services.sh demo --frontend # ...plus the Vite dev server on http://localhost:5173
#   ./start_services.sh stop            # stop local backends and the dev server
#
# Secrets come from Google Secret Manager using your gcloud login; nothing is written to disk.
# Every API route needs a Firebase sign-in, so use the app (or a real ID token) to call it.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$ROOT_DIR/venv/bin/python"
ENV_NAME="${1:-personal}"
WITH_FRONTEND="${2:-}"

if [[ "$ENV_NAME" == "stop" ]]; then
    pkill -f "uvicorn api:app" 2>/dev/null && echo "Stopped local backend(s)." || echo "No local backend running."
    pkill -f "vite" 2>/dev/null && echo "Stopped Vite dev server." || true
    exit 0
fi

case "$ENV_NAME" in
    personal) PORT=8080 ;;
    demo) PORT=8081 ;;
    *) echo "Unknown environment '$ENV_NAME' (use personal, demo or stop)." >&2; exit 1 ;;
esac

LOG="/tmp/pfa_backend_${ENV_NAME}.log"
echo "=== Starting local $ENV_NAME backend on http://127.0.0.1:$PORT ($(date -u '+%Y-%m-%d %H:%M:%S UTC')) ==="
if curl -s --max-time 3 "http://127.0.0.1:$PORT/health" | grep -q healthy; then
    echo "Already running."
else
    cd "$ROOT_DIR/app"
    APP_ENV="$ENV_NAME" setsid "$VENV_PYTHON" -m uvicorn api:app --host 127.0.0.1 --port "$PORT" \
        >> "$LOG" 2>&1 < /dev/null &
    for _ in {1..40}; do
        if curl -s --max-time 2 "http://127.0.0.1:$PORT/health" | grep -q healthy; then
            echo "Backend is healthy (log: $LOG)."
            break
        fi
        sleep 1
    done
fi

if [[ "$WITH_FRONTEND" == "--frontend" ]]; then
    cd "$ROOT_DIR/app/frontend"
    echo "Starting Vite dev server against http://127.0.0.1:$PORT ..."
    VITE_API_BASE_URL="http://127.0.0.1:$PORT" setsid npm run dev -- --host 127.0.0.1 \
        >> /tmp/pfa_vite.log 2>&1 < /dev/null &
    echo "Frontend: http://localhost:5173 (log: /tmp/pfa_vite.log)"
fi
