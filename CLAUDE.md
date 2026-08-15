# CLAUDE.md

Operating rules for this repository. Read before writing code, and re-read
generated code against the Hard Rules before merging it. That is the review
that matters.

## What this is

An agentic film-production procurement system. A producer uploads a shooting
breakdown; the agent researches reference prices, finds suppliers, and
negotiates with them over real email across simulated days. It stops before
spending money. A human approves every purchase order.

## Ownership

| Path             | Owner   | Contents                                                     |
| ---------------- | ------- | ------------------------------------------------------------ |
| `contracts/`     | shared  | The A/B interface. Changes require both sides to agree.       |
| `main-agent/`    | Role A  | The brain. LLM reasoning only, as pure functions.             |
| `orchestrator/`  | Role B  | Clock, Firestore, Gmail, state machine, tick loop.            |
| `supplier-sim/`  | Role B  | Adversary simulator. Separate service, own mailbox.           |
| `web/`           | Role B  | React + Vite + TypeScript front end.                          |

Role A implements the four Protocol signatures in `contracts/`. Role A does not
touch Firestore, Gmail, or the clock. Role B does not make LLM calls except
inside `supplier-sim/`, which is a test fixture rather than product code.

## Hard Rules

These are not style preferences. Each one is load-bearing for a claim the demo
makes.

- All LLM calls go through `google-genai` or `google-adk`. Never any other provider.
- Never call `datetime.now()` or `time.time()`. Time comes from `clock.now()`.
- No in-memory state between requests. Everything persists to Firestore.
  Any handler must be safe to kill mid-run and resume on the next tick.
- `purchase_orders` is created only via `create()` keyed by `item_id`.
  Never `set()`, never update, never delete.
- The agent service account has no write access to `purchase_orders`.

### Why each one exists

**Single LLM provider.** A competition rule, and the Google Cloud story is
stronger when the whole stack is one vendor.

**`clock.now()` only.** The demo compresses five days of negotiation into sixty
seconds. If any code path reads wall-clock time, that path desynchronises from
every stored `due_at` and the compression silently breaks. Retrofitting this
means touching every file in the repo, so it goes in first and stays in. A CI
guard fails the build on `datetime.now()` / `time.time()` in application code.

**No in-memory state.** Cloud Run cold-starts and reaps instances whenever it
likes. A tick that holds state in a local variable works on a laptop and dies
during judging. Kill any handler halfway and the next tick must resume cleanly.

**`create()` keyed by `item_id`.** This is the guardrail, and it is enforced by
the storage engine rather than by prompt design. `create()` fails if the
document already exists, so a duplicate purchase order is refused before our
code runs. Because the *item* is the key, ordering the same item from two
suppliers is the same violation and is refused identically.

**No agent write access.** "The agent cannot spend money" has to be an IAM fact,
not a claim about how well we wrote the prompt. The agent service account has no
`producer` role; only a human-authenticated request can create an order.

## Money and units

Money is always `{"amount": 880, "currency": "MYR"}`. Never a formatted string
like `"RM880"`, never a bare number. `amount` is an integer in the currency's
minor-unit-free major form (ringgit, not sen) unless the field name says
otherwise. Mixing currencies in an arithmetic operation raises.

## Time

Every timestamp written to Firestore is simulation time, from `clock.now()`.
Live mode runs 1:1. Demo mode advances six simulated hours per real second.
Same code path, one field different.

## The stop condition

The agent stops at `READY_FOR_HUMAN`. Always. No exceptions, no config flag, no
"auto-approve under RM500." `ORDERED` is the only state that writes a purchase
order, and it is reachable only through the human-authenticated endpoint.

Confusing or unparseable supplier replies escalate to `READY_FOR_HUMAN` too. An
agent that guesses at an ambiguous quote is worse than one that asks.

## Conventions

- Python 3.14, `uv` workspace. `ruff` and `basedpyright` configured at the root
  and inherited by every member package.
- Double quotes, 88 columns, LF endings.
- `pydantic` v2 models at every boundary, so bad data fails loudly and early.
- Tests run against the Firestore emulator, never a live project.

## The daily habit

Run the loop end to end every day, however broken, and push to your branch. Ten
minutes. With no stubs in the plan, this is the only thing keeping the two
halves from diverging. `make e2e` is that check; it must pass before any merge.
