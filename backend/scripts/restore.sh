#!/usr/bin/env sh
# PostgreSQL restore (compose). Usage: sh scripts/restore.sh < backup.sql
set -e
docker compose exec -T db psql -U rgo -d rgo
