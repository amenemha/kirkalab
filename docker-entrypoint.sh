#!/usr/bin/env sh
set -e

# Apply database migrations before starting the application.
echo "Running database migrations..."
alembic upgrade head

# Hand off to the container CMD (e.g. uvicorn).
echo "Starting application..."
exec "$@"
