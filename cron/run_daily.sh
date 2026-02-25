#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs reports .data
source .venv/bin/activate

python daily_digest.py --max-items-per-source "${MAX_ITEMS_PER_SOURCE:-40}" >> logs/daily_digest.log 2>&1
