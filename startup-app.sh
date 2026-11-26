#!/bin/sh
set -eu
cd "$(dirname "$0")"

case "${DATABASE_URL:-}" in
    postgres://*) DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgres://}" ;;
    postgresql://*) DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgresql://}" ;;
esac

export DATABASE_URL
export ENVIRONMENT="${ENVIRONMENT:-production}"
export DEBUG=false
export AUTH_SECURE_COOKIE="${AUTH_SECURE_COOKIE:-true}"
exec sh scripts/start.sh
