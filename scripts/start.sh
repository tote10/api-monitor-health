#!/bin/sh
set -eu

# The in-process scheduler assumes one worker. Scale with an external scheduler
# before increasing this number.
alembic upgrade head

exec uvicorn main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers 1 \
    --proxy-headers
