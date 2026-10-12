#!/usr/bin/env bash
# Dependency install for Cloud Agents. Idempotent. Does not start servers.
set -euo pipefail
cd /workspace
python3 -m pip install --user -e ".[dev]"
python3 -m playwright install --with-deps chromium
for cmd in pytest uvicorn alembic playwright; do
  if [[ -x "${HOME}/.local/bin/${cmd}" ]]; then
    sudo ln -sfn "${HOME}/.local/bin/${cmd}" "/usr/local/bin/${cmd}"
  fi
done
