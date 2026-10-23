#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python scripts/export_contracts.py
npm run generate --prefix apps/dashboard
npm run build --prefix apps/dashboard
