#!/usr/bin/env bash
# Boot Postgres and the internal API. Safe to run again.
set -euo pipefail
cd /workspace
export PATH="/usr/local/bin:${HOME}/.local/bin:${PATH}"

if ! pg_isready -q; then
  sudo pg_ctlcluster 16 main start
fi
for _ in $(seq 1 30); do
  pg_isready -q && break
  sleep 1
done

sudo -u postgres psql -v ON_ERROR_STOP=1 -tc "SELECT 1 FROM pg_roles WHERE rolname='evorove_lead'" | grep -q 1 \
  || sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE evorove_lead LOGIN PASSWORD 'local_development_only'"
sudo -u postgres psql -v ON_ERROR_STOP=1 -tc "SELECT 1 FROM pg_database WHERE datname='evorove_lead'" | grep -q 1 \
  || sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE evorove_lead OWNER evorove_lead"

export DATABASE_URL="postgresql+psycopg://evorove_lead:local_development_only@127.0.0.1:5432/evorove_lead"
alembic upgrade head

# 200 is this branch's /health. 404 means uvicorn is up on main, which has no /health yet.
if [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:8002/health" || true)" =~ ^(200|404)$ ]]; then
  exit 0
fi

TMUX=(tmux)
if [[ -f /exec-daemon/tmux.portal.conf ]]; then
  TMUX=(tmux -f /exec-daemon/tmux.portal.conf)
fi
if ! "${TMUX[@]}" has-session -t evorove_lead_api 2>/dev/null; then
  "${TMUX[@]}" new-session -d -s evorove_lead_api \
    "cd /workspace && DATABASE_URL='${DATABASE_URL}' /usr/local/bin/uvicorn evorove_lead.api:app --host 0.0.0.0 --port 8002"
fi

for _ in $(seq 1 30); do
  if [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:8002/health" || true)" =~ ^(200|404)$ ]]; then
    exit 0
  fi
  sleep 1
done
echo "evorove_lead api did not become ready" >&2
exit 1
