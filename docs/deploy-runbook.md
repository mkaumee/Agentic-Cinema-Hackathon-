# Deploy runbook

Getting the loop onto Cloud Run and ticking on its own. Run by a human, in
**Cloud Shell** — gcloud is already there and authenticated, and the image is
built by Cloud Build so no Docker daemon is needed.

Budget half an hour for the first run, most of it waiting for the first build.

## Before you start

- `scripts/gcp_setup.sh` has been run against the project.
- Billing is linked. Cloud Run and Scheduler simply refuse to exist without it,
  unlike Firestore — which is what lets an unbilled project look half-working.

## The order matters

Two steps are easy to do too late, and both fail in ways that point somewhere
else.

### 1. Rules and indexes go first

```bash
make deploy-rules PROJECT_ID=your-project-id
```

`firestore.indexes.json` carries the collection-group entries for
`next_action_due_at` as **field overrides**. Firestore auto-creates ordinary
single-field indexes but not collection-group ones, and `due_negotiations()` is
a collection-group query.

Skip this and every tick fails with `FAILED_PRECONDITION`. The failure is caught
per project, so `/tick` returns 200 with an error inside the body and the
service looks perfectly healthy. You would be debugging the wrong thing.

### 2. Deploy with mail off

```bash
make deploy PROJECT_ID=your-project-id
```

`MAIL_BACKEND` defaults to `memory` on purpose. `build_services()` constructs
the mail transport at startup, so deploying with `gmail` before a refresh token
exists in Secret Manager gives you a container that raises during startup, a
revision that never goes ready, and a deploy that fails at the last and most
expensive step.

A deploy with mail off is not a half-deploy. The loop ticks, researches, opens
negotiations and drives the state machine — it just posts into an in-memory
mailbox. Everything except the sending is exercised.

### 3. Verify — this is what "deployed" means

```bash
make verify-deploy PROJECT_ID=your-project-id
```

`make deploy` exiting 0 means its gcloud calls succeeded. It says nothing about
whether the tick account can still reach the orders database, because the
guardrail is an **absence** and nothing about a successful deploy tests an
absence.

The verifier reports `PASS`, `FAIL` or `????`, and exits non-zero on either of
the last two. `????` is deliberate: the guardrail check needs to impersonate the
tick service account, and impersonation *itself* fails with `PERMISSION_DENIED`
when you lack `roles/iam.serviceAccountTokenCreator`. That looks identical to
the guardrail working. If you see `????` on the guardrail, grant yourself the
right and run it again:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  cinema-agent@your-project-id.iam.gserviceaccount.com \
  --member="user:$(gcloud config get-value account)" \
  --role=roles/iam.serviceAccountTokenCreator
```

Do not read `????` as a pass. An unchecked guardrail is not a working guardrail.

### 4. Turn real email on, once the token exists

Only after `docs/oauth-runbook.md` is done and the token is in Secret Manager:

```bash
CINEMA_TOKEN_BACKEND=secret-manager CINEMA_GCP_PROJECT=your-project-id \
  uv run python scripts/oauth_bootstrap.py
```

That backend variable is not optional. Without it the token lands in a local
gitignored file that the deployed service cannot read, the bootstrap reports
success, and the flip below fails with a confusing `NotFound`.

Then:

```bash
CINEMA_OAUTH_CLIENT_ID=...apps.googleusercontent.com \
CINEMA_OAUTH_CLIENT_SECRET=... \
CINEMA_AGENT_EMAIL=producer-agent@example.com \
MAIL_BACKEND=gmail make deploy PROJECT_ID=your-project-id
```

The client id and secret matter as much as the token. Without them
`build_credentials()` builds a credential with an empty client, the service
starts happily, and the first send fails with `invalid_client` — a failure that
looks like a Gmail problem and is not.

`deploy.sh` preflights all three before changing anything, so a missing piece is
one line of English rather than a failed revision.

## What it costs

The loop itself is not what will spend the credit.

| | |
| --- | --- |
| Cloud Run | `--min-instances=0`, so nothing runs between ticks. 43k invocations a month against a 2M free tier. |
| Scheduler | One job. Three are free. |
| Firestore | A few reads per tick, ~7k/day against a 50k/day free tier. |
| Artifact Registry | One image, ~200MB, against 0.5GB free. |

What *will* spend it is Role A's model calls once the real brain is wired. Keep
`--max-instances=2` — it is there so a wedged tick cannot fan out.

## When it is working

```bash
gcloud logging read \
  'resource.labels.service_name="cinema-tick" jsonPayload.message="tick"' \
  --limit=20 --format='value(jsonPayload)'
```

One JSON object per minute, each with the tick's counters as fields. That is the
thing to look at the morning after — `messages_sent`, `claims_lost`,
`error_count` — rather than whether the service is up.

## When something is wrong

| Symptom | Cause |
| --- | --- |
| Revision never goes ready | Almost always mail. Check `MAIL_BACKEND` and whether the secret has a version. |
| `/tick` returns 200 but every project has an error | Indexes. Run `make deploy-rules`. |
| `FAILED_PRECONDITION` with an index-creation link | Same, and the link builds it by hand if you are in a hurry. |
| Cloud Build fails on push permissions | The build account needs `artifactregistry.writer`. `deploy.sh` grants it; if you built by hand, it did not. |
| `invalid_grant` after about a week | The testing-mode token expired. Expected. Re-run the bootstrap. |
| `invalid_client` on the first send | `CINEMA_OAUTH_CLIENT_ID` / `_SECRET` are not set on the service. |
| Anonymous `POST /tick` returns 200 | The tick is public. It should be `--no-allow-unauthenticated`; redeploy. |
| Verifier says the tick account can read `orders` | The service accounts are the wrong way round. This is the one that silently undoes Phase 4 — fix before anything else. |
