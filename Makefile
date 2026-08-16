# Everything here runs offline. No GCP credentials, no live project.

.PHONY: help setup fmt lint types guard test check emulator e2e clean gcp-setup deploy-rules

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install the Python workspace and the web dependencies
	uv sync
	cd web && npm install

fmt: ## Format Python
	uv run ruff format .
	uv run ruff check --fix .

lint: ## Lint without changing anything
	uv run ruff check .
	uv run ruff format --check .

types: ## Type-check
	uv run basedpyright

guard: ## Fail if anything outside the clock module reads real time
	uv run python scripts/check_no_wallclock.py

test: ## Unit tests
	uv run pytest -q

check: lint types guard test ## Everything that must pass before a merge

emulator: ## Start the Firestore + Auth emulators (leave running while you work)
	firebase emulators:start --only firestore,auth --project demo-cinema

e2e: ## The daily ten-minute habit: boot the emulators, run the loop end to end
	firebase emulators:exec --only firestore,auth --project demo-cinema \
		"uv run python scripts/run_e2e.py"

gcp-setup: ## Stand up the Google Cloud project (run once; needs gcloud + PROJECT_ID)
	PROJECT_ID=$(PROJECT_ID) ./scripts/gcp_setup.sh

deploy-rules: ## Push firestore.rules and the indexes to the real project
	firebase deploy --only firestore --project $(PROJECT_ID)

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ web/dist
