FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --chown=appuser:appuser . .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

USER appuser

EXPOSE 8002

# The only network-facing code in this repo is the outcome endpoint
# (evorove_lead.api). A cycle-1 run is a one-off command, not a server:
#   docker compose run --rm lead python -m evorove_lead.run --business-id ... --site-url ...
# The headless-render fallback needs a Chromium binary this image does not
# ship; without it presence.py keeps the plain fetch, as it already does.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn evorove_lead.api:app --host 0.0.0.0 --port ${PORT:-8002}"]
