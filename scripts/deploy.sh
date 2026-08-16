#!/usr/bin/env bash
#
# Deploy the two Cloud Run services and the Scheduler job. Run by a human,
# after scripts/gcp_setup.sh, from a machine with gcloud and Docker.
#
#   PROJECT_ID=your-project-id ./scripts/deploy.sh
#
# Idempotent, in the same style as gcp_setup.sh: every step checks before it
# creates, and re-running after a failure is safe.
#
# ---------------------------------------------------------------------------
# NOT YET RUN AGAINST A REAL PROJECT
# ---------------------------------------------------------------------------
#
# Written before billing was linked, on a machine with neither gcloud nor a
# Docker daemon. The image it deploys is built and smoke-tested by CI on every
# push, so that half is verified; the gcloud half below is not. Expect to fix
# something the first time. Treat a clean run as the beginning of the check,
# not the end of it — the verification block at the bottom is the real test.
#
# ---------------------------------------------------------------------------
# The thing this script must not get wrong
# ---------------------------------------------------------------------------
#
# Two services, one image, two service accounts:
#
#   tick       orchestrator.app:app        cinema-agent
#              roles/datastore.user on (default), CONDITIONED
#              no binding whatsoever on `orders`
#
#   approvals  orchestrator.approvals:app  cinema-approvals
#              roles/datastore.user on (default) AND on `orders`
#
# The approvals account needs both because approving writes the purchase order
# in one database and the negotiation transition in the other. The tick account
# needs exactly one, and the absence of the second *is* Hard Rule 5 — it is what
# makes "the agent cannot spend money" a fact about IAM rather than a promise
# about our code.
#
# Swapping those two accounts is the single deployment mistake that silently
# undoes all of Phase 4: every test still passes, the demo still works, and the
# guardrail is gone. That is why this script ends by printing the real IAM
# policy for both accounts instead of telling you it succeeded.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-cinema}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"

AGENT_SA="${AGENT_SA:-cinema-agent}"
APPROVALS_SA="${APPROVALS_SA:-cinema-approvals}"
SCHEDULER_SA="${SCHEDULER_SA:-cinema-scheduler}"
ORDERS_DB="${ORDERS_DB:-orders}"
TOKEN_SECRET="${TOKEN_SECRET:-gmail-agent-refresh-token}"

TICK_SERVICE="${TICK_SERVICE:-cinema-tick}"
APPROVALS_SERVICE="${APPROVALS_SERVICE:-cinema-approvals}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "PROJECT_ID is not set." >&2
  echo "  PROJECT_ID=your-project-id $0" >&2
  exit 2
fi

for tool in gcloud docker git; do
  command -v "$tool" >/dev/null || { echo "$tool is not installed." >&2; exit 2; }
done

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
skip() { printf '  \033[90m·\033[0m %s (already there)\n' "$*"; }

AGENT_EMAIL="${AGENT_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
APPROVALS_EMAIL="${APPROVALS_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_EMAIL="${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/orchestrator:${IMAGE_TAG}"

gcloud config set project "$PROJECT_ID" >/dev/null 2>&1

# ---------------------------------------------------------------------------
say "Preflight — billing"
# ---------------------------------------------------------------------------
#
# Cloud Run and Scheduler simply refuse to exist without it, unlike Firestore,
# which is why gcp_setup.sh can get most of the way on an unbilled project and
# this script cannot get anywhere at all.

billing=$(gcloud billing projects describe "$PROJECT_ID" \
  --format='value(billingEnabled)' 2>/dev/null || echo unknown)
if [[ "$billing" != "True" && "$billing" != "true" ]]; then
  cat >&2 <<EOF
  ✗ no billing account linked to $PROJECT_ID (read as: $billing)

  Everything below needs it. See the billing section of scripts/gcp_setup.sh:

    gcloud billing accounts list
    gcloud billing projects link $PROJECT_ID --billing-account=ACCOUNT_ID

  Nothing has been changed.
EOF
  exit 4
fi
ok "billing is linked"

# ---------------------------------------------------------------------------
say "APIs"
# ---------------------------------------------------------------------------

enabled=$(gcloud services list --enabled --format='value(config.name)')
for api in run.googleapis.com cloudscheduler.googleapis.com \
           artifactregistry.googleapis.com cloudbuild.googleapis.com \
           secretmanager.googleapis.com iamcredentials.googleapis.com; do
  if grep -qx "$api" <<<"$enabled"; then skip "$api"; else
    gcloud services enable "$api" >/dev/null && ok "$api"
  fi
done

# ---------------------------------------------------------------------------
say "Service accounts"
# ---------------------------------------------------------------------------

ensure_sa() {
  local name="$1" email="$2" desc="$3"
  if gcloud iam service-accounts describe "$email" >/dev/null 2>&1; then
    skip "$email"
  else
    gcloud iam service-accounts create "$name" --description="$desc" \
      --display-name="$name" >/dev/null
    ok "$email"
  fi
}

ensure_sa "$AGENT_SA" "$AGENT_EMAIL" \
  "Runs the tick loop. Deliberately cannot write purchase orders."
ensure_sa "$APPROVALS_SA" "$APPROVALS_EMAIL" \
  "Runs the approval service. The only identity that may write an order."
ensure_sa "$SCHEDULER_SA" "$SCHEDULER_EMAIL" \
  "Mints the OIDC token Cloud Scheduler calls /tick with."

# ---------------------------------------------------------------------------
say "IAM"
# ---------------------------------------------------------------------------
#
# The agent's conditioned binding is created by gcp_setup.sh; this only adds
# the approvals account, which is unconditioned because it genuinely needs both
# databases.

has_role() {
  gcloud projects get-iam-policy "$PROJECT_ID" \
    --flatten='bindings[].members' \
    --filter="bindings.members:serviceAccount:$1 AND bindings.role:$2" \
    --format='value(bindings.role)' | grep -q .
}

if has_role "$APPROVALS_EMAIL" "roles/datastore.user"; then
  skip "datastore.user for $APPROVALS_SA"
else
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${APPROVALS_EMAIL}" \
    --role="roles/datastore.user" >/dev/null
  ok "datastore.user for $APPROVALS_SA — both databases, deliberately"
fi

# Reading the token secret is the tick service's business only. The approvals
# service never sends mail.
if gcloud secrets describe "$TOKEN_SECRET" >/dev/null 2>&1; then
  if gcloud secrets get-iam-policy "$TOKEN_SECRET" --format=json \
       | grep -q "$AGENT_EMAIL"; then
    skip "secretAccessor on $TOKEN_SECRET"
  else
    gcloud secrets add-iam-policy-binding "$TOKEN_SECRET" \
      --member="serviceAccount:${AGENT_EMAIL}" \
      --role="roles/secretmanager.secretAccessor" >/dev/null
    ok "secretAccessor on $TOKEN_SECRET"
  fi
else
  printf '  \033[33m?\033[0m secret %s does not exist — run gcp_setup.sh\n' \
    "$TOKEN_SECRET"
fi

# ---------------------------------------------------------------------------
say "Image"
# ---------------------------------------------------------------------------

if gcloud artifacts repositories describe "$REPO" \
     --location="$REGION" >/dev/null 2>&1; then
  skip "artifact registry $REPO"
else
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker --location="$REGION" \
    --description="Agentic Cinema images" >/dev/null
  ok "artifact registry $REPO"
fi

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null

# Context is the repository root: `contracts` is a uv workspace path dependency
# and a context of orchestrator/ cannot see it. See the Dockerfile.
docker build -t "$IMAGE" "$(git rev-parse --show-toplevel)"
docker push "$IMAGE"
ok "$IMAGE"

# ---------------------------------------------------------------------------
say "Cloud Run"
# ---------------------------------------------------------------------------

# --timeout is under the one-minute schedule on purpose, so a wedged tick
# cannot still be running when the next one fires. That is only safe because
# the tick claims each row before working on it — a truncated tick leaves
# leased rows for the next pass rather than half-finished ones. Before the
# claiming landed, this setting would have caused the double-email it prevents.
gcloud run deploy "$TICK_SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$AGENT_EMAIL" \
  --no-allow-unauthenticated \
  --timeout=50s \
  --max-instances=2 \
  --min-instances=0 \
  --memory=512Mi \
  --set-env-vars="CINEMA_GCP_PROJECT=${PROJECT_ID},CINEMA_ORDERS_DATABASE=${ORDERS_DB},CINEMA_MAIL_BACKEND=gmail,CINEMA_TOKEN_BACKEND=secret-manager,CINEMA_LOG_FORMAT=json" \
  --quiet >/dev/null
ok "$TICK_SERVICE  (orchestrator.app:app, as $AGENT_SA)"

# Same image, different command and a different account. The command override
# is the entire difference between the service that cannot spend money and the
# service that can.
#
# --allow-unauthenticated is deliberate and is not a hole. Cloud Run IAM cannot
# validate a Firebase ID token, so the gate has to live in the app, and it does:
# orchestrator/auth.py rejects anything without a verified token and a
# `producer` custom claim, and firestore.orders.rules refuses the write a second
# time for a browser. Putting IAM in front instead would mean a producer's
# browser could not call it at all.
gcloud run deploy "$APPROVALS_SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$APPROVALS_EMAIL" \
  --allow-unauthenticated \
  --command=uvicorn \
  --args="orchestrator.approvals:app,--host,0.0.0.0,--port,8080" \
  --timeout=60s \
  --max-instances=2 \
  --memory=512Mi \
  --set-env-vars="CINEMA_GCP_PROJECT=${PROJECT_ID},CINEMA_ORDERS_DATABASE=${ORDERS_DB},CINEMA_LOG_FORMAT=json" \
  --quiet >/dev/null
ok "$APPROVALS_SERVICE  (orchestrator.approvals:app, as $APPROVALS_SA)"

TICK_URL=$(gcloud run services describe "$TICK_SERVICE" --region="$REGION" \
  --format='value(status.url)')
APPROVALS_URL=$(gcloud run services describe "$APPROVALS_SERVICE" \
  --region="$REGION" --format='value(status.url)')

# ---------------------------------------------------------------------------
say "Cloud Scheduler"
# ---------------------------------------------------------------------------

gcloud run services add-iam-policy-binding "$TICK_SERVICE" \
  --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_EMAIL}" \
  --role="roles/run.invoker" --quiet >/dev/null
ok "run.invoker for $SCHEDULER_SA on $TICK_SERVICE"

if gcloud scheduler jobs describe cinema-tick --location="$REGION" \
     >/dev/null 2>&1; then
  gcloud scheduler jobs update http cinema-tick \
    --location="$REGION" --schedule="* * * * *" \
    --uri="${TICK_URL}/tick" --http-method=POST \
    --oidc-service-account-email="$SCHEDULER_EMAIL" \
    --oidc-token-audience="$TICK_URL" \
    --attempt-deadline=50s --quiet >/dev/null
  ok "scheduler job cinema-tick (updated)"
else
  gcloud scheduler jobs create http cinema-tick \
    --location="$REGION" --schedule="* * * * *" \
    --uri="${TICK_URL}/tick" --http-method=POST \
    --oidc-service-account-email="$SCHEDULER_EMAIL" \
    --oidc-token-audience="$TICK_URL" \
    --attempt-deadline=50s --quiet >/dev/null
  ok "scheduler job cinema-tick — every minute"
fi

# Nothing schedules the approval service. It is called by a person, and an
# approval that could be triggered on a timer would not be an approval.

# ---------------------------------------------------------------------------
say "What the two accounts can actually do"
# ---------------------------------------------------------------------------
echo "  Read this. Do not trust the ticks above — this is the guardrail."
echo
echo "  $AGENT_SA (tick) — must show a CONDITION naming (default), and must"
echo "  NOT show an unconditioned datastore.user:"
gcloud projects get-iam-policy "$PROJECT_ID" \
  --flatten='bindings[].members' \
  --filter="bindings.members:serviceAccount:${AGENT_EMAIL}" \
  --format='table(bindings.role, bindings.condition.expression)'
echo
echo "  $APPROVALS_SA — datastore.user with no condition is correct here:"
gcloud projects get-iam-policy "$PROJECT_ID" \
  --flatten='bindings[].members' \
  --filter="bindings.members:serviceAccount:${APPROVALS_EMAIL}" \
  --format='table(bindings.role, bindings.condition.expression)'

cat <<EOF

$(printf '\033[1mDeployed\033[0m')

  tick       $TICK_URL      (private; Scheduler only)
  approvals  $APPROVALS_URL

$(printf '\033[1mCheck it, in this order\033[0m')

  1. The tick is genuinely private — this must fail with 403:

       curl -s -o /dev/null -w '%{http_code}\\n' -XPOST $TICK_URL/tick

  2. It answers a real caller:

       curl -H "Authorization: Bearer \$(gcloud auth print-identity-token)" \\
         $TICK_URL/healthz

  3. The approval service is up and says which databases it holds:

       curl $APPROVALS_URL/healthz

  4. THE ONE THAT MATTERS. The tick account must be unable to reach the orders
     database at all. Impersonate it and try — a PERMISSION_DENIED here is the
     whole of Hard Rule 5, and a success means the deploy silently undid Phase 4:

       gcloud firestore documents list --database=$ORDERS_DB \\
         --collection-ids=purchase_orders \\
         --impersonate-service-account=$AGENT_EMAIL

  5. Leave it alone overnight. Come back to a negotiation that advanced with
     nobody touching it, and one JSON line per minute in Cloud Logging:

       gcloud logging read \\
         'resource.labels.service_name="$TICK_SERVICE" jsonPayload.message="tick"' \\
         --limit=20 --format='value(jsonPayload)'

EOF
