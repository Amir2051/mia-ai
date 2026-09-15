#!/bin/sh
set -eu

cd /app
export PYTHONPATH="/app/backend${PYTHONPATH:+:$PYTHONPATH}"

echo "Running database migrations..."
alembic -c backend/alembic.ini upgrade head

echo "Starting Mia AI..."
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-2}"
