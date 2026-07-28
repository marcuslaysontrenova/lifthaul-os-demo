#!/usr/bin/env sh
# PostgreSQL backup (compose). Usage: sh scripts/backup.sh > backup.sql
set -e
docker compose exec -T db pg_dump -U rgo rgo
