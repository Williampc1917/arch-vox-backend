#!/bin/bash
set -e

# Shared entrypoint for both FastAPI web service and background workers.

ROLE="${SERVICE_ROLE:-web}"

echo "Starting container in '${ROLE}' mode"

if [ "$ROLE" = "worker" ]; then
  JOB_NAME="${WORKER_JOB:-vip_backfill}"
  echo "Launching worker job: ${JOB_NAME}"
  exec python -m app.jobs.worker "${JOB_NAME}"
else
  # Default to running the FastAPI app via uvicorn.
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" ${UVICORN_EXTRA_ARGS}
fi
