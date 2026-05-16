#!/usr/bin/env bash
#
# Patent Hunter weekly runner. Invoked by launchd / cron.
#
# Cleans potentially-shared env vars so the run uses the project's own
# ADC + .env, not the calling shell. This matters when the user has a
# different GOOGLE_APPLICATION_CREDENTIALS exported for an unrelated
# workspace.

set -euo pipefail

# Resolve repo root (the script lives in scripts/, so go one level up).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

# launchd starts with a minimal env (PATH usually just /usr/bin:/bin:/usr/sbin:/sbin).
# Restore the directories that hold node-version-managed binaries (claude / codex
# CLIs via ndenv) and the usual local install prefixes. Without this the
# subprocess scorers cannot locate `claude` or `codex`.
export PATH="${HOME}/.local/bin:${HOME}/.ndenv/shims:${HOME}/.ndenv/bin:/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"

# Force the project to use its own ADC (gcloud auth application-default login)
# regardless of any inherited service-account JSON path from an unrelated
# workspace.
unset GOOGLE_APPLICATION_CREDENTIALS

# Load project .env (KEY=VALUE lines). Skip if missing.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# Prefer the project venv; fall back to system python3.
PYTHON="${PYTHON:-${PROJECT_DIR}/.venv/bin/python}"
if [ ! -x "${PYTHON}" ]; then
  PYTHON="$(command -v python3)"
fi

# Per-week log file.
mkdir -p logs
LOG_FILE="logs/weekly-$(date -u +%Y-W%V).log"

{
  echo "[run_weekly] started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[run_weekly] PROJECT_DIR=${PROJECT_DIR}"
  echo "[run_weekly] PYTHON=${PYTHON}"
} >>"${LOG_FILE}"

# Forward any extra CLI args from launchd / cron through to the runner.
"${PYTHON}" -m patent_hunter run "$@" >>"${LOG_FILE}" 2>&1
EXIT_CODE=$?

# Best-effort autonomous issue creation. Failures must NOT affect the run's exit code.
"${PYTHON}" "${PROJECT_DIR}/scripts/auto_issue.py" >>"${LOG_FILE}" 2>&1 || true
"${PYTHON}" "${PROJECT_DIR}/scripts/weekly_insights.py" >>"${LOG_FILE}" 2>&1 || true

echo "[run_weekly] exit code ${EXIT_CODE} at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"${LOG_FILE}"
exit "${EXIT_CODE}"
