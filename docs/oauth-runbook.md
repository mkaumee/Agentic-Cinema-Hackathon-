# Gmail OAuth runbook

How the agent gets permission to send and read email, and how to keep it.

Everything here is done by a person, once per mailbox. The service never runs
any of it — a consent screen needs a human, and this is the only place in the
repository that touches an OAuth client secret.

## Read this part first

**A consent screen in testing mode issues refresh tokens that expire after
seven days.**

Under a compressed demo that is a nuisance. With a real loop it is a bug that
kills live work: a negotiation runs for days, the token dies mid-conversation,
and the agent silently stops replying to a seller who is waiting. Nothing
crashes. The negotiation just goes quiet, which looks exactly like a supplier
who lost interest.

Two ways out, in order of preference:

1. **Publish the consent screen.** Refresh tokens then last until they are
   revoked. Publishing an app that only requests Gmail scopes for your own
   accounts does not require Google's verification review as long as you keep
   the user cap low — you will see an "unverified app" interstitial when
   consenting, which you can click through.
2. **Re-run the bootstrap every few days**, and put a calendar reminder on it.
   Workable, but it means someone has to remember during the week that matters.

## The mailboxes

Two Google accounts, neither of them your personal one:

| Mailbox | Purpose |
| --- | --- |
| producer agent | What the agent sends from and polls. Suppliers see this address. |
| supplier test | Stands in for a seller while testing, so you can reply by hand. |

The agent's poll **marks mail as read**, which is why a personal account is a
poor choice for it.

## One-time Google Cloud setup

1. Create or pick a project in [console.cloud.google.com](https://console.cloud.google.com).
2. **APIs & Services → Library →** enable **Gmail API**.
3. **APIs & Services → OAuth consent screen**
   - User type: External.
   - Add both mailboxes under **Test users** while it is in testing mode.
   - Add scopes `gmail.send` and `gmail.modify`. Nothing wider — the agent has
     no business deleting mail, so `mail.google.com` is not requested.
   - Publish it when you are ready to stop re-authing every seven days.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   - Application type: **Desktop app**.
   - Download the JSON and save it as `.secrets/client_secret.json`.
     That directory is gitignored; keep it that way.

## Minting a token

```bash
uv run python scripts/oauth_bootstrap.py
```

A browser opens. Sign in **as the mailbox you are setting up**, not as
yourself out of habit.

The script requests `access_type=offline` with `prompt=consent`, which together
are what actually produce a refresh token. Without `prompt=consent`, a mailbox
that has already authorised the app hands back an access token only — the run
looks like it worked, right up until the first expiry an hour later.

If it reports no refresh token came back, revoke the app at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions)
and run it again. Google only issues one on first consent.

## Where the token lives

Selected by `CINEMA_TOKEN_BACKEND`:

| Value | Where | When |
| --- | --- | --- |
| `file` (default) | `.secrets/gmail_refresh_token.json`, mode `0600` | Now, while there is no GCP project |
| `secret-manager` | Secret Manager, named by `CINEMA_REFRESH_TOKEN_SECRET` | Once the project and billing exist |

The bootstrap script writes through whichever is configured, so the same
command works before and after the migration. Switching is one environment
variable; no code changes.

## Turning real email on

Sending is off by default. `CINEMA_MAIL_BACKEND` is `memory` unless you say
otherwise, so the loop cannot start emailing real sellers just because a token
happens to be sitting on disk.

```bash
export CINEMA_MAIL_BACKEND=gmail
export CINEMA_AGENT_EMAIL="producer-agent@example.com"
export CINEMA_OAUTH_CLIENT_ID="...apps.googleusercontent.com"
export CINEMA_OAUTH_CLIENT_SECRET="..."
```

Confirm what is wired before trusting it:

```bash
curl -s localhost:8000/healthz
# {"status":"ok","mail_backend":"gmail","token_backend":"file",...}
```

## Proving the round trip

This is the check that closes Phase 1.

1. Start the emulator and the service, with `CINEMA_MAIL_BACKEND=gmail`.
2. Seed a project whose supplier email is the **supplier test** mailbox.
3. `curl -XPOST localhost:8000/tick` — an opening email should arrive.
4. Reply from the supplier mailbox with a price, in plain text: *"We can do
   RM1,250 per day."*
5. `curl -XPOST localhost:8000/tick` again.

Expected: `replies_filed: 1`, and the negotiation now holds a quote of
`{"amount": 1250, "currency": "MYR"}`.

Check the supplier mailbox too. The reply and the agent's follow-up should be
**one thread**, not two. If they have split, threading is broken — the headers
are built from RFC-822 `Message-ID` values, and nothing inside the system
notices when that goes wrong because our own routing uses Gmail's thread ID.

## When something is wrong

| Symptom | Cause |
| --- | --- |
| `No refresh token at .secrets/...` | Bootstrap has not been run on this machine. |
| `invalid_grant` on a tick | Token expired (testing mode, seven days) or was revoked. Re-run the bootstrap. |
| Every tick refiles the same replies | The `gmail.modify` scope is missing, so `UNREAD` is never cleared. Re-consent with both scopes. |
| The agent replies to itself | `CINEMA_AGENT_EMAIL` does not match the authorised mailbox, so `-from:me` no longer excludes our own sent mail. |
| Replies land in a fresh thread each time | Threading headers. See step 5 above. |
| Nothing arrives, no error | `mail_backend` is still `memory`. Check `/healthz`. |
