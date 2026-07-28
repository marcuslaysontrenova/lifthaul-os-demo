.PHONY: up down build migrate seed test lint backup restore logs health
up:       ; docker compose up --build -d
down:     ; docker compose down
build:    ; docker compose build
migrate:  ; docker compose exec backend python migrate.py
seed:     ; docker compose exec -e APP_ENV=development backend python seed.py
test:     ; python -m unittest discover -p 'test_*.py' -v
lint:     ; python -m py_compile *.py && echo "py_compile OK"
backup:   ; sh scripts/backup.sh > backup_$$(date +%Y%m%d_%H%M%S).sql && echo "backup written"
restore:  ; @echo "usage: sh scripts/restore.sh < backup.sql"
logs:     ; docker compose logs -f --tail=100
health:   ; curl -fsS http://localhost:8787/health && echo && curl -fsS http://localhost:8787/ready
