#!/bin/sh
set -eu

# The in-process scheduler assumes one worker. Scale with an external scheduler
# before increasing this number.
alembic upgrade head

# Create initial admin user if ADMIN_USERNAME and ADMIN_PASSWORD are provided
if [ -n "${ADMIN_USERNAME:-}" ] && [ -n "${ADMIN_PASSWORD:-}" ]; then
    python -c "
from app.db.database import SessionLocal
from app.db.models import User
from app.core.auth import hash_password
import os

username = os.environ['ADMIN_USERNAME']
password = os.environ['ADMIN_PASSWORD']

with SessionLocal() as db:
    user = db.query(User).filter(User.username == username).first()
    if not user:
        db.add(User(username=username, password_hash=hash_password(password), role='admin'))
        db.commit()
        print(f'Admin user {username} initialized.')
    else:
        print(f'Admin user {username} already exists.')
" || true
fi

exec uvicorn main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers 1 \
    --proxy-headers
