#!/usr/bin/env bash
set -euo pipefail

# Disposable test database only: random localhost port, tmpfs storage, no password.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
container="evorove-lead-test-$$-$RANDOM"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run --rm -d --name "$container" \
  -p 127.0.0.1::5432 \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_HOST_AUTH_METHOD=trust \
  -e POSTGRES_USER=evorove_test \
  -e POSTGRES_DB=evorove_test \
  postgres:17-alpine >/dev/null

for _ in $(seq 1 30); do
  if docker exec "$container" pg_isready -U evorove_test -d evorove_test >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$container" pg_isready -U evorove_test -d evorove_test >/dev/null
port="$(docker port "$container" 5432/tcp | awk -F: 'END { print $NF }')"
test_url="postgresql+psycopg://evorove_test@127.0.0.1:${port}/evorove_test"

cd "$repo_root"
python3 - "$test_url" <<'PY'
import sys
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

engine = create_engine(sys.argv[1])
config = Config("alembic.ini")
with engine.connect() as connection:
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
    command.current(config)
    command.check(config)
engine.dispose()
PY

EVOROVE_TEST_POSTGRES=1 EVOROVE_TEST_POSTGRES_URL="$test_url" python3 -m pytest -q -rs "$@"
