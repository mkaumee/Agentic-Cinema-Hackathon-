# Everything here runs offline. No GCP credentials, no live project.

# `test-all` pipes pytest through tee and needs the pipeline's exit status, not
# tee's. /bin/sh is dash on Debian and has no `pipefail`, so ask for bash.
SHELL := /bin/bash

.PHONY: help setup fmt lint types guard test test-all rules-test check emulator e2e clean gcp-setup deploy-rules

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

test: ## Unit tests, fast. Skips anything needing an emulator.
	uv run pytest -q

# What CI runs, and what `check` runs. The emulators are booted here rather
# than assumed, and a skip is a failure: the emulator-backed tests skip
# themselves when nothing is listening, which is right for the inner loop above
# and unacceptable in a gate. A green run that quietly skipped every guardrail
# test looks like coverage.
test-all: ## Every test, with the emulators up. Fails if any test skips.
	@set -o pipefail; \
	firebase emulators:exec --only firestore,auth --project demo-cinema \
		"uv run pytest -q" 2>&1 | tee .pytest-all.out; \
	if grep -qE "[0-9]+ skipped" .pytest-all.out; then \
		echo "FAIL: tests skipped — an emulator did not come up."; \
		rm -f .pytest-all.out; exit 1; \
	fi; \
	rm -f .pytest-all.out

# Runs the rules files themselves. The Python suite goes through the admin SDK,
# which bypasses security rules entirely, so this is the only thing in the repo
# that executes them. It boots its own emulator because it loads both rules
# files by path — firebase-tools does not read them from the multi-database
# form in firebase.json, which is why the emulator otherwise runs wide open.
rules-test: ## Execute firestore.rules and firestore.orders.rules
	firebase emulators:exec --only firestore --project demo-cinema \
		"cd web && npm test"

check: lint types guard test-all rules-test ## Everything that must pass before a merge

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
