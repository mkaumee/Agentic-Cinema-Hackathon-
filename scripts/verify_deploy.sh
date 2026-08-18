#!/usr/bin/env bash
#
# Check a deployment the way you would check someone else's claim.
#
#   PROJECT_ID=your-project-id ./scripts/verify_deploy.sh
#
# Read-only. Changes nothing, so run it as often as you like — after a deploy,
# after a config change, or on the morning of the demo.
#
# ---------------------------------------------------------------------------
# Why this is a separate script from deploy.sh
# ---------------------------------------------------------------------------
#
# deploy.sh exiting 0 means its gcloud calls succeeded. That is a much weaker
# statement than "the deployment is correct", and the gap between the two is
# exactly where Hard Rule 5 lives: the guardrail is an *absence* — no IAM
# binding on the orders database — and nothing about a successful deploy tests
# an absence.
#
# ---------------------------------------------------------------------------
# The trap this script exists to avoid
# ---------------------------------------------------------------------------
#
# The obvious check is "impersonate the tick account, try to read the orders
# database, expect PERMISSION_DENIED". Written that way it proves nothing,
# because impersonation *itself* fails with PERMISSION_DENIED when the caller
# lacks roles/iam.serviceAccountTokenCreator on the target account. Both
# failures print the same thing, and the wrong one reads as a pass.
#
# So the guardrail check is a pair:
#
#   positive  impersonate the tick account, read (default)  -> must SUCCEED
#   negative  impersonate the tick account, read orders     -> must FAIL
#
# The positive is what proves impersonation works at all. Without it a denial on
# the negative is uninformative, and this script says INCONCLUSIVE rather than
# PASS. An honest "I could not tell" is worth more than a green tick that means
# nothing.

set -uo pipefail

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
AGENT_SA="${AGENT_SA:-cinema-agent}"
APPROVALS_SA="${APPROVALS_SA:-cinema-approvals}"
ORDERS_DB="${ORDERS_DB:-orders}"
TICK_SERVICE="${TICK_SERVICE:-cinema-tick}"
APPROVALS_SERVICE="${APPROVALS_SERVICE:-cinema-approvals}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "PROJECT_ID is not set." >&2
  echo "  PROJECT_ID=your-project-id $0" >&2
  exit 2
fi
command -v gcloud >/dev/null || { echo "gcloud is not installed." >&2; exit 2; }

AGENT_EMAIL="${AGENT_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
APPROVALS_EMAIL="${APPROVALS_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

PASSED=0
FAILED=0
UNKNOWN=0

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$*"; PASSED=$((PASSED + 1)); }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILED=$((FAILED + 1)); }
huh()  { printf '  \033[33m????\033[0m  %s\n' "$*"; UNKNOWN=$((UNKNOWN + 1)); }
note() { printf '        \033[90m%s\033[0m\n' "$*"; }

gcloud config set project "$PROJECT_ID" >/dev/null 2>&1

# ---------------------------------------------------------------------------
say "1. The services are up"
# ---------------------------------------------------------------------------

TICK_URL=$(gcloud run services describe "$TICK_SERVICE" --region="$REGION" \
  --format='value(status.url)' 2>/dev/null || true)
APPROVALS_URL=$(gcloud run services describe "$APPROVALS_SERVICE" \
  --region="$REGION" --format='value(status.url)' 2>/dev/null || true)

if [[ -n "$TICK_URL" ]]; then pass "tick: $TICK_URL"
else fail "tick service not found"; fi

if [[ -n "$APPROVALS_URL" ]]; then pass "approvals: $APPROVALS_URL"
else fail "approvals service not found"; fi

# ---------------------------------------------------------------------------
say "2. The tick is private, and answers an authorised caller"
# ---------------------------------------------------------------------------
#
# Public would mean anyone on the internet can drive the agent's mailbox.

if [[ -n "$TICK_URL" ]]; then
  anon=$(curl -s -o /dev/null -w '%{http_code}' -XPOST "$TICK_URL/tick" || echo 000)
  case "$anon" in
    401|403) pass "an anonymous POST /tick is refused ($anon)" ;;
    000)     huh "could not reach $TICK_URL at all" ;;
    *)       fail "anonymous POST /tick returned $anon — the tick is PUBLIC" ;;
  esac

  authed=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $(gcloud auth print-identity-token 2>/dev/null)" \
    "$TICK_URL/healthz" || echo 000)
  if [[ "$authed" == "200" ]]; then
    pass "an authorised GET /healthz is served (200)"
  else
    fail "authorised GET /healthz returned $authed"
  fi
fi

if [[ -n "$APPROVALS_URL" ]]; then
  # Public on purpose: Cloud Run IAM cannot validate a Firebase ID token, so
  # the gate is auth.py. What must be true is that it refuses an anonymous
  # approval — a 401, not a 200.
  health=$(curl -s -o /dev/null -w '%{http_code}' "$APPROVALS_URL/healthz" \
    || echo 000)
  if [[ "$health" == "200" ]]; then
    pass "approvals /healthz is served (200)"
  else
    fail "approvals /healthz returned $health"
  fi

  approve=$(curl -s -o /dev/null -w '%{http_code}' -XPOST \
    -H 'Content-Type: application/json' \
    -d '{"project_id":"none","negotiation_id":"none"}' \
    "$APPROVALS_URL/items/none/approve" || echo 000)
  if [[ "$approve" == "401" ]]; then
    pass "an unauthenticated approval is refused (401)"
  else
    fail "unauthenticated approval returned $approve — expected 401"
  fi
fi

# ---------------------------------------------------------------------------
say "3. THE GUARDRAIL — can the tick account reach the orders database?"
# ---------------------------------------------------------------------------
#
# The pair described at the top of this file. Read both results together or
# neither of them means anything.

impersonate_read() {
  # Returns 0 when the read succeeded. Stderr is captured, not shown, because
  # the interesting part is which of the two calls failed, not the wording.
  local database="$1"
  gcloud firestore documents list \
    --database="$database" \
    --collection-ids=purchase_orders \
    --impersonate-service-account="$AGENT_EMAIL" \
    --limit=1 >/dev/null 2>&1
}

if impersonate_read "(default)"; then
  note "impersonation works — a denial below is Firestore's, not IAM's"

  if impersonate_read "$ORDERS_DB"; then
    fail "the tick account CAN read the '$ORDERS_DB' database"
    note "Hard Rule 5 is not true in this deployment. The agent has a binding"
    note "it must not have. Check which service account deploy.sh used, and"
    note "look for an unconditioned roles/datastore.user on $AGENT_SA."
  else
    pass "the tick account cannot touch '$ORDERS_DB' — Hard Rule 5 holds"
  fi
else
  huh "$AGENT_SA could not read '(default)', so the guardrail is untested"
  note "This is NOT a pass. Two different causes look identical here:"
  note ""
  note "  a) the account has no datastore.user binding at all — check with"
  note "     gcloud projects get-iam-policy $PROJECT_ID \\"
  note "       --flatten='bindings[].members' \\"
  note "       --filter='bindings.members:serviceAccount:$AGENT_EMAIL' \\"
  note "       --format='table(bindings.role, bindings.condition.expression)'"
  note "     An empty table means gcp_setup.sh has not run. Run it."
  note ""
  note "  b) you may not impersonate it — grant yourself the right:"
  note "     gcloud iam service-accounts add-iam-policy-binding $AGENT_EMAIL \\"
  note "       --member=\"user:\$(gcloud config get-value account)\" \\"
  note "       --role=roles/iam.serviceAccountTokenCreator"
  note ""
  note "Either way the agent cannot reach 'orders' because it cannot reach"
  note "anything, which is a broken deploy that happens to look safe."
fi

# The other half of the split: the approvals account must reach both.
if impersonate_read_approvals=$(gcloud firestore documents list \
     --database="$ORDERS_DB" --collection-ids=purchase_orders \
     --impersonate-service-account="$APPROVALS_EMAIL" --limit=1 2>&1); then
  pass "the approvals account can reach '$ORDERS_DB' — approval will work"
else
  case "$impersonate_read_approvals" in
    *serviceAccountTokenCreator*|*iam.serviceAccounts.getAccessToken*)
      huh "could not impersonate $APPROVALS_SA — untested" ;;
    *)
      fail "the approvals account CANNOT reach '$ORDERS_DB'"
      note "Approving will fail at the point of writing the order. It needs an" 
      note "unconditioned roles/datastore.user." ;;
  esac
fi

# ---------------------------------------------------------------------------
say "4. Rules and indexes reached the real project"
# ---------------------------------------------------------------------------
#
# firebase.json uses the multi-database array form, and firebase-tools does not
# read that form for the *emulator* — which is why the emulator runs open. That
# makes "does `firebase deploy` honour it for a real project" an open question,
# and an unanswered one means the browser-side rules may not be deployed at all.

for db in "(default)" "$ORDERS_DB"; do
  if gcloud firestore databases describe --database="$db" \
       >/dev/null 2>&1; then
    pass "database '$db' exists"
  else
    fail "database '$db' does not exist — run scripts/gcp_setup.sh"
  fi
done

# The collection-group index is what due_negotiations() queries on. Without it
# every tick fails FAILED_PRECONDITION, and the failure is per-project so the
# service still looks healthy.
idx=$(gcloud firestore indexes fields list --database='(default)' \
  --format='value(name)' 2>/dev/null | grep -c "next_action_due_at" || true)
if [[ "${idx:-0}" -ge 1 ]]; then
  pass "next_action_due_at field index present ($idx)"
else
  fail "no next_action_due_at index — every tick will fail FAILED_PRECONDITION"
  note "  make deploy-rules PROJECT_ID=$PROJECT_ID"
fi

note "Rules themselves cannot be read back by gcloud. Check by eye, once:"
note "  console.cloud.google.com/firestore/databases/-default-/rules"
note "  console.cloud.google.com/firestore/databases/$ORDERS_DB/rules"

# ---------------------------------------------------------------------------
say "5. Something has actually ticked"
# ---------------------------------------------------------------------------

ticks=$(gcloud logging read \
  "resource.labels.service_name=\"$TICK_SERVICE\" jsonPayload.message=\"tick\"" \
  --limit=5 --format='value(timestamp)' --freshness=1h 2>/dev/null | wc -l \
  | tr -d ' ')

if [[ "${ticks:-0}" -ge 1 ]]; then
  pass "$ticks tick(s) logged in the last hour — Scheduler is driving it"
else
  huh "no tick logged in the last hour"
  note "Fine if you deployed less than a minute ago. If it persists:"
  note "  gcloud scheduler jobs describe cinema-tick --location=$REGION"
  note "  gcloud logging read 'resource.labels.service_name=\"$TICK_SERVICE\"' --limit=20"
fi

# ---------------------------------------------------------------------------
printf '\n\033[1m%s\033[0m\n' "Result"
printf '  %d passed, %d failed, %d could not be determined\n' \
  "$PASSED" "$FAILED" "$UNKNOWN"

if (( FAILED > 0 )); then
  printf '\n\033[31mThe deployment is not correct.\033[0m Fix the FAILs above.\n\n'
  exit 1
fi
if (( UNKNOWN > 0 )); then
  printf '\n\033[33mInconclusive.\033[0m Nothing failed, but something could not be\n'
  printf 'checked — and an unchecked guardrail is not a working guardrail.\n\n'
  exit 3
fi
printf '\n\033[32mVerified.\033[0m\n\n'
