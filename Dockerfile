FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --chown=appuser:appuser . .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir . \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

USER appuser

EXPOSE 8002

# The only network-facing code in this repo is the outcome endpoint
# (evorove_lead.api). A cycle-1 run is a one-off command, not a server:
#   docker compose run --rm lead python -m evorove_lead.run --business-id ... --site-url ...
# Chromium ships in the image: a client-rendered site (evorove.com itself)
# is an empty <div id="root"> over plain HTTP, and presence.py only reads it
# through the headless-render fallback.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn evorove_lead.api:app --host 0.0.0.0 --port ${PORT:-8002}"]
