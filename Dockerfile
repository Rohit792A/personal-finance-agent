# Production image for the Personal Finance AI backend (Cloud Run, DEC-025).
# One image serves both environments: APP_ENV, settings and secrets are set per Cloud Run
# service (finance-backend = personal, finance-backend-demo = demo). See scripts/deploy_backend.sh.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app

# Python dependencies, pinned in requirements.txt. Every package ships prebuilt wheels and the
# database driver (pg8000) is pure Python, so no compiler or system libraries are installed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code. app/ is the import root: modules import each other by plain name.
COPY app/ /app/

# Run as an unprivileged user. Only the per-request upload folder needs to be writable
# (statement PDFs go to Cloud Storage, other scratch files to /tmp).
# app/private/ (the git-ignored owner profile, DEC-062) is made readable by that user even if
# the local copy is mode 600; without it the image simply has no owner profile.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/temp_uploads \
    && chown appuser /app/temp_uploads \
    && if [ -d /app/private ]; then chmod -R a+rX /app/private; fi
USER appuser

# Expose standard Cloud Run / Container port
EXPOSE 8080

# Run FastAPI backend with Uvicorn
CMD exec uvicorn api:app --host 0.0.0.0 --port ${PORT:-8080}
