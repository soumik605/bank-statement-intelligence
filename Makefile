# Bank Statement Intelligence: common tasks. Run `make help` to list them.
BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000

.PHONY: help setup dev backend frontend test lint check samples schema

help: ## List the tasks
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies; create .env if missing
	cd backend && uv sync
	cd frontend && npm ci
	@test -f .env || (cp .env.example .env && echo "Created .env: put your NVIDIA_API_KEY in it")

dev: ## Run the API and the web app together (Ctrl-C stops both)
	@$(MAKE) -j2 --no-print-directory backend frontend

backend: ## Run only the API, with auto-reload
	cd backend && CORS_ORIGINS=http://localhost:$(FRONTEND_PORT),http://127.0.0.1:$(FRONTEND_PORT) \
		uv run uvicorn app.main:app --reload --port $(BACKEND_PORT)

frontend: ## Run only the web app
	cd frontend && NEXT_PUBLIC_API_URL=http://localhost:$(BACKEND_PORT) npx next dev --port $(FRONTEND_PORT)

test: ## Backend tests (no network; never touches data/history.db)
	cd backend && uv run pytest -q

lint: ## Frontend type-check and lint; check docs/schema.json is current
	cd frontend && npx tsc --noEmit && npm run lint
	cd backend && uv run python scripts/export_schema.py --check

check: test lint ## Everything above, as CI would run it

samples: ## Regenerate the synthetic statements in backend/samples/
	cd backend && uv run python scripts/generate_samples.py

schema: ## Regenerate docs/schema.json from the backend models
	cd backend && uv run python scripts/export_schema.py
