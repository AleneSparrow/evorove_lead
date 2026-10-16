# Opt-in deployment image. Existing production Dockerfile is unchanged.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /app
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
# Explicit allowlist: no .env, reports, local databases, or checkout credentials.
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY deploy/client-zero-brief.txt ./owner-materials/client-zero-brief.txt
RUN python -m pip install --no-cache-dir . \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright
USER appuser
EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8002')+'/openapi.json',timeout=3)"
# Migration is an explicit, separately approved one-off operation.
CMD ["sh", "-c", "exec uvicorn evorove_lead.api:app --host 0.0.0.0 --port ${PORT:-8002}"]
