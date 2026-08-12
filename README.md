# Agentic Cinema

An agentic procurement system for film production. A producer uploads a shooting
breakdown; the agent researches what each item should cost, finds suppliers, and
negotiates with them over real email across simulated days.

It stops before spending money. Every purchase order is created by a human, and
that limit is enforced by database rules rather than by prompt design.

## Layout

```
contracts/       The Role A / Role B interface. Both sides import it.
main-agent/      Role A. The brain: four LLM-backed functions, no side effects.
orchestrator/    Role B. Clock, Firestore, Gmail, state machine, tick loop.
supplier-sim/    Role B. Adversary simulator. Own mailbox, email only.
web/             Role B. React + Vite + TypeScript, live on Firestore snapshots.
scripts/         OAuth flow, seeding, the wall-clock guard.
docs/            Runbooks.
```

## Getting started

```bash
uv sync                      # Python 3.14 workspace, all members
uv run pytest                # unit tests, no cloud access needed
uv run ruff check .          # lint
uv run basedpyright          # types
```

Everything above runs offline. Nothing in the test suite touches a live GCP
project — Firestore tests run against the emulator.

## How the pieces fit

The producer's agent and the supplier simulator are separate services that know
nothing about each other except an email address. The simulator never reads our
Firestore; if it did, this would be a simulation rather than a test, and it
would stop being evidence the moment a real supplier was swapped in.

Time is simulated. `clock.now()` is the only source of it, so a five-day
negotiation can be replayed in sixty seconds without any code path behaving
differently. See `CLAUDE.md` for why that is a hard rule rather than a
preference.

## The rules that shape the code

`CLAUDE.md` holds five constraints that are load-bearing for what the demo
claims. The short version:

- One LLM provider, `google-genai` / `google-adk`.
- No wall-clock time, anywhere. `clock.now()` only.
- No in-memory state between requests. Any handler is safe to kill mid-run.
- `purchase_orders` is created with `create()` keyed by `item_id`, never
  updated, never deleted — so a duplicate order is refused by the storage
  engine before application code runs.
- The agent's service account cannot write to `purchase_orders` at all.

## Licence

MIT. See `LICENSE`.
