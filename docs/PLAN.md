# Role B build plan

The remaining work, in dependency order. Read `CLAUDE.md` first — it says what
the system is and which rules are load-bearing. This says what to build next.

## How to use this

Each phase has a **Done when** that is checkable by running something, not by
looking at code. If a phase cannot be closed that way, the phase is wrong.

Phases are ordered by dependency and by calendar, not by interest. Do not start
a later phase because an earlier one is boring; the ordering below exists
because of the constraint in the next section.

Update this file when reality disagrees with it. A plan nobody edits is a plan
nobody is following.

---

## The constraint that sets the order

**The loop is real, so calendar time is a dependency.**

A negotiation takes days because people take days to answer email. That is the
product working correctly, not a delay to engineer around. It also means a
negotiation started late simply does not finish.

Everything follows from that:

- Real Gmail and a deployed ticker come **early** (Phases 1 and 3), before the
  UI, because until they exist no simulated day is passing anywhere.
- The front half (Phase 2) sits between them, because a deployed ticker with no
  negotiations in the database has nothing to do.
- The UI comes after, because it renders state that must already be moving. A
  beautiful screen showing three hand-seeded rows proves nothing.

The one-line version: **get real negotiations in flight as early as possible,
then build the screens that watch them.**

---

## Where we are

Verified, not assumed — every claim below is covered by a test in the repo.

**Done**

| Area | State |
| --- | --- |
| Repo, uv workspace, ruff + basedpyright, `make check` | Green |
| `contracts/` — 4 signatures, 5 data shapes, `ScriptedBrain` | Done, needs Role A sign-off |
| `orchestrator/clock.py` — sim clock, live/demo/frozen | Done |
| `scripts/check_no_wallclock.py` — AST guard + its own tests | Done |
| `orchestrator/state_machine.py` | Done, `ORDERED` proven unreachable by agent |
| `orchestrator/records.py`, `repository.py` | Done, emulator-tested |
| `firestore.rules`, `firestore.orders.rules`, indexes | **Written, rules untested** |
| GCP setup script, two-database split | Done; script not yet run against a real project |
| `orchestrator/mail.py` — transport seam + in-memory impl | Done |
| `orchestrator/tick.py` — the loop | Done, kill-mid-run tested |
| `scripts/run_e2e.py` / `make e2e` | Green, ends with 0 purchase orders |
| **Phase 1** — settings, Gmail transport, HTTP service, OAuth bootstrap, runbook, CI | Done |

156 tests. 46 of them need the Firestore emulator and skip without it; CI fails
the build if they skip there.

**Not started**

Script upload → items → research → negotiation creation (Phase 2) · deploy
config, no Dockerfile (Phase 3) · auth and the approval endpoint (Phase 4) ·
`web/`, still empty (Phases 5–6) · `supplier-sim/`, scaffolding only (later).

**Known debts, carried deliberately**

- `firestore.rules` has no tests. The `create()` uniqueness guarantee is proven
  in Python, but the admin SDK bypasses rules, so the rules themselves are
  unverified → Phase 4. Note this is not a gap in the *agent* guardrail: rules
  never constrained the agent at all, which is why orders moved to their own
  database. Rules only govern the producer's browser path.
- firebase-tools 15 does not load rules from the multi-database array form in
  `firebase.json`, so the emulator now runs open. Harmless today — our Python
  tests use the admin SDK and bypass rules regardless — and Phase 4's rules
  tests load each file explicitly rather than through `firebase.json`.
- Nothing protects against two ticks overlapping → Phase 3.
- `/tick` is unauthenticated. It gets Scheduler OIDC and private ingress in
  Phase 3; a home-grown shared secret in the meantime would look like
  protection without being any.
- The live email round-trip is unproven — it needs two mailboxes and a consent
  screen that do not exist yet. The transport is covered offline; the runbook
  is the checklist.

---

## Invariants

These hold at the end of every phase. If a phase would break one, the phase is
wrong, not the invariant.

1. `make check` and `make e2e` both pass before any merge.
2. No wall-clock reads outside `orchestrator/clock.py`.
3. Every handler is safe to kill mid-run.
4. `purchase_orders` is only ever written by `create()` keyed by `item_id`,
   from a human-authenticated request.
5. The agent's service account cannot write `purchase_orders` at all.
6. The brain composes all email text; the orchestrator only addresses and sends.

---

## Phase 1 — Make it reachable, and make the mail real — DONE

**Goal.** A deployable HTTP service that sends and receives real Gmail.

**Shipped.** `settings.py`, `gmail.py` (transport + file/Secret-Manager token
stores), `app.py` (`GET /healthz`, `POST /tick`), `scripts/oauth_bootstrap.py`,
`docs/oauth-runbook.md`, and CI running the full gate on push.

Also fixed a latent bug found while building it: threading was keyed on Gmail's
API message id rather than the RFC-822 `Message-ID` header. Those are different
strings, and only the header threads — so every reply would have forked a new
thread in the supplier's inbox, invisibly, because our own routing uses Gmail's
thread id and would have kept working.

**Outstanding.** The live round-trip, which needs the mailboxes.

**Why here.** Nothing can run on a schedule until there is something to call,
and no simulated day passes until real email moves.

**Build**

- `orchestrator/settings.py` — `pydantic-settings`: GCP project, mailbox
  addresses, Secret Manager names, tick limit, clock mode.
- `orchestrator/app.py` — FastAPI. `GET /healthz`, `POST /tick` (returns the
  `TickReport`). Wire `FirestoreRepository`, `SimClock`, `GmailTransport` and
  the brain in one composition root; no globals.
- `orchestrator/gmail.py` — `GmailTransport` satisfying `MailTransport`:
  - `send()` builds RFC-2822, base64url-encodes it, sets `In-Reply-To` and
    `References` from `last_msg_id`, passes `threadId` when continuing.
  - `poll()` lists unread, reads `threadId` and `id`, detects attachments from
    the payload parts, then removes the `UNREAD` label so the next poll does
    not re-read it.
  - Returns `RawInbound` with **no timestamp** — the tick stamps it with
    `clock.now()`. Do not let the transport read a clock.
- `scripts/oauth_bootstrap.py` — run locally by a human; performs the consent
  flow for both mailboxes and writes refresh tokens to Secret Manager.
- `docs/oauth-runbook.md` — the file `CLAUDE.md` already points at.
- `.github/workflows/check.yml` — `make check` plus `make e2e` on every push.

**Done when.** A real email leaves the agent mailbox, a human replies from the
supplier mailbox, and a `POST /tick` files that reply against the right
negotiation by thread ID. `make e2e` still green.

**Watch out for**

- **Seven-day refresh tokens.** A consent screen in testing mode issues tokens
  that expire inside a negotiation's lifetime. Publish the consent screen, or
  put a calendar reminder on the re-auth. This is the single most likely way a
  live negotiation dies silently.
- Route inbound by `threadId` only. Suppliers rewrite subject lines.
- Poll, do not use Pub/Sub push — push needs a verified domain and a watch
  subscription renewed weekly, and buys nothing at one tick per minute.
- Unread-query polling is simple but re-reads anything a human opens in the
  mailbox. If that bites, move to `historyId` — but not before it bites.

**Blocked on.** Nothing from Role A. Needs a human to run the consent flow.

---

## Phase 2 — Close the front half: script to negotiations

**Goal.** An uploaded screenplay becomes live negotiations with no hand-seeding.

**Why here.** This is the biggest hole in the system right now. `run_e2e.py`
seeds items, suppliers and negotiations by hand because **nothing turns a
script into them.** Until this exists, the deployed ticker has no work.

**Build**

- `POST /projects` — create a project with an initial clock.
- `POST /projects/{pid}/script` — accept a screenplay, call
  `brain.extract_props()`, persist each `PropDraft` as an `ItemRecord` with
  `status=DRAFT`, carrying `mentions` and `consumable`.
- `POST /projects/{pid}/items/confirm` — the producer confirms the list and
  sets quantities. **Consumable props need a human here**: only they know how
  many takes the schedule allows. Nothing is researched or negotiated before
  this call.
- Research step — for each confirmed item, call `brain.research_item()`, store
  the `reference_band` and write a `SupplierRecord` per candidate.
- Negotiation creation — for each supplier with a usable address, write a
  `NegotiationRecord` in `DRAFTED`, due now. The existing tick loop takes it
  from there with no changes.

**Design decision to make here.** Research and negotiation-creation are
long-running and LLM-backed, so they must be killable like everything else. Give
`items` its own `next_action_due_at` and a second collection-group query in the
tick, mirroring negotiations exactly. One pattern, one index shape, one
recovery story — rather than a bespoke background job with its own failure mode.

**Done when.** Upload a script, confirm the props, run one tick, and opening
emails go out to researched suppliers. No hand-seeded documents anywhere.

**Blocked on.** Role A's real `extract_props` and `research_item`.
`ScriptedBrain` covers both until they land, so this phase is not gated.

---

## Phase 3 — Deploy, and start the clock for real

**Goal.** Cloud Scheduler ticking every minute against a real project, with a
real negotiation in flight.

**Why here.** See the constraint at the top. The day this ships is the day
elapsed time starts counting for us. Every day it slips is a day of negotiation
we do not get back.

**Build**

- `Dockerfile` for `orchestrator` — build from repo root so the `contracts`
  path dependency resolves.
- Cloud Run service. Min instances 0 is fine; the loop is killable by design.
- Cloud Scheduler → `POST /tick` every minute with OIDC auth.
- Secret Manager for refresh tokens; service account with **no** `producer`
  claim.
- Deploy `firestore.rules` and `firestore.indexes.json`.
- Structured JSON logging of each `TickReport`.

**Watch out for**

- **Overlapping ticks.** Scheduler does not guarantee a run finishes before the
  next fires. Two ticks can pick up the same due negotiation and both email the
  supplier — which looks exactly like the pestering bug already fixed in the
  loop, but from a different cause. Fix by claiming: write
  `next_action_due_at` forward *before* calling the brain, so a concurrent tick
  no longer sees the row. Cheap, and it uses the index already there.
- Tick timeout shorter than the Scheduler interval, so a wedged tick cannot
  pile up.
- Cold-start latency is irrelevant here — nothing is waiting on a response.

**Done when.** You can leave it alone overnight and come back to a negotiation
that advanced without anyone touching it.

---

## Phase 4 — The money path

**Goal.** A human can approve a purchase, and nothing else can.

**Build**

- Firebase Auth, plus a small admin script that sets the `producer` custom
  claim on a user.
- `POST /items/{item_id}/approve` — verifies the ID token and the `producer`
  claim, creates the purchase order via `create()`, transitions the negotiation
  `READY_FOR_HUMAN → ORDERED`.
  **This cannot live in the tick service.** That service's account has no IAM
  binding on the orders database, which is the whole guardrail. Approval runs
  as a separate service account, or straight from the producer's browser where
  rules apply. Bolting it onto `app.py` would quietly undo the split.
- `POST /negotiations/{nid}/floor` — the producer sets a floor and hands the
  negotiation back (`HUMAN_RETURNED_WITH_FLOOR → NEGOTIATING`).
- `POST /negotiations/{nid}/cancel` → `DEAD`.
- **Rules tests** in `web/` using `@firebase/rules-unit-testing`:
  - an agent identity (no `producer` claim) cannot create a purchase order
  - a producer can, exactly once
  - update and delete are refused for everyone, including producers
  - both rules files are loaded explicitly; `firebase.json` is not consulted
  - a payload whose `item_id` disagrees with the document key is refused
  - negotiation messages cannot be rewritten

**Done when.** The rules tests pass, and an approval attempt made with the
agent's own credentials is refused by Firestore rather than by our code.

**Watch out for.** This is the phase where the strongest claim in the project
stops being "we wrote it carefully" and becomes "here is the test". Do not let
it slide to demo week.

---

## Phase 5 — Instrument panel (UI pass 1)

**Goal.** See the loop run, without reading the Firestore console.

**Build.** React + Vite + TS, Firebase JS SDK, `onSnapshot` from the first
line. One read-only screen: negotiations with state, next due time, latest
quote, round count, message count, escalation reason. No design work at all.

**Done when.** A tick changes the screen with no refresh and no polling code.

**Why it earns its place.** It is the debugging surface for Phases 2–4, it
proves listeners, auth and hosting early, and it becomes the timeline screen in
Phase 6 rather than being thrown away. Budget three hours; do not gold-plate it.

---

## Phase 6 — The product (UI pass 2)

**Goal.** The 25% of the score that is not engineering.

| Screen | Content |
| --- | --- |
| Breakdown | Item list with per-item status. Upload lives here. |
| Item detail | Quotes side by side, recommendation, the brain's reasoning, the reference band, **and the script lines the prop came from** |
| Timeline | Every message in and out with simulated timestamps — the proof that days passed |
| Approve | Accept, or set a floor and hand it back |
| Savings | First quote vs final accepted, summed. Measured, not claimed. |

**The design problem worth the most thought.** The floor-price handoff. A
producer is authorising an agent to spend days negotiating on their behalf,
against their money. Make the ceiling visible, the stop condition visible, and
make it obvious the agent comes back before anything is bought. This is the
interaction that proves it is an agent rather than a form, and it is a design
job rather than an engineering one.

**Watch out for.** Pick the stack and a design direction *before* generating
screens. Generated UI converges on one recognisable look, and "looks like every
other submission" is exactly what loses a criterion asking whether this feels
like a finished product.

---

## Phase 7 — Demo surface

- **Guardrail moment.** A control that attempts a duplicate order and is
  refused by the database, with the error visible on screen. Make it a beat,
  not a footnote — it is the most defensible thing the project does.
- **Escalation path.** When `extract_quote` returns an escalation or the brain
  stops, a human resolves it in the UI.
- **Judge mode.** A seeded account with negotiations already mid-flight, plus a
  "run five days in sixty seconds" control that needs no Gmail OAuth from the
  visitor.
- **Cold test.** Open the hosted URL in a browser that has never signed in, on
  a phone, on someone else's machine. This catches the credential that only
  exists on your laptop — the most common way a hackathon project dies on
  submission day.

---

## Later — explicitly parked

Nothing in the product may depend on any of these.

- **`supplier-sim/`.** Separate service, own mailbox, Gemini writing every
  reply, contact only over email. Five personas over `latency_hours`,
  `anchor_multiplier`, `floor_multiplier`, `writing_style`, `ghost_probability`
  — including one who buries the price in a PDF, which is the case
  `extract_quote` must escalate rather than guess at.
- **Compressed replay harness.** The clock already supports it; nothing uses it.
- **Buying direct from online shops.** When it lands, a listing is just another
  quote and it funnels into the same approval gate. Do not add a second path to
  money.

---

## The daily habit

Run `make e2e` and push, every day, however broken. Ten minutes.

With no stubs in the plan, this is the only thing keeping the two halves of the
codebase from diverging. Skip it and the first real integration happens late,
and the remaining days go on finding out whose code was wrong instead of
building.

---

## Open questions

- **Role A sign-off on `contracts/`.** The four signatures and the five shapes
  are written and tested, but BlueGecko has not agreed to them. Every day he
  builds against something else is a day of rework. This is the highest-value
  conversation available right now.
- **Who publishes the OAuth consent screen**, and when. Blocks Phase 1 closing
  properly; testing-mode tokens will not survive a real negotiation.
- **Project access.** You were added to the project as a service admin, which
  grants `iam.serviceAccounts.create` and little else that the setup needs.
  `scripts/gcp_setup.sh` runs a preflight and names the exact roles to request;
  expect four:

      roles/serviceusage.serviceUsageAdmin
      roles/datastore.owner
      roles/secretmanager.admin
      roles/resourcemanager.projectIamAdmin

  The last one is not optional cosmetics. It is what lets the agent's service
  account be granted `(default)` and denied `orders`. Without it the database
  split is a convention in our code rather than something Google enforces, and
  the strongest claim the project makes stops being true. `roles/editor` does
  not include it.

  If broad roles are a hard sell, the cheaper ask is for the project owner to
  run `scripts/gcp_setup.sh` once themselves — it is idempotent, self-contained
  and changes nothing else.
