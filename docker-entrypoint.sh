#!/bin/sh
set -eu

cd /app
export PYTHONPATH="/app/backend${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -r /run/secrets/migration_database_url ]; then
  echo "Migration database secret is missing" >&2
  exit 1
fi
export MIGRATION_DATABASE_URL="$(cat /run/secrets/migration_database_url)"

echo "Running database migrations..."
alembic -c backend/alembic.ini upgrade head

# Do not leave the migration/superuser credential in the application process environment.
unset MIGRATION_DATABASE_URL

echo "Starting Mia AI..."
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-2}"
