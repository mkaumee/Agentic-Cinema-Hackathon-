# Demo script

Four minutes, one screenplay, one real email round, and one command that proves
the claim everything else rests on.

The through-line: **this thing does a real job that a real person does slowly,
and it stops before it spends your money.** Everything below serves one of those
two halves. If you drop a beat for time, drop from the middle — never the
guardrail at the end.

---

## Before you stand up

Run these. A demo that discovers its own configuration on stage is not a demo.

```bash
make verify-deploy PROJECT_ID=encoded-phalanx-505503-v8
```

Section 7 must read **`main-agent`** for both the tick and the api, and
**`research key: configured`**. If it says `SCRIPTED`, the emails are written by
a regex and a word list — it will look fine on screen and fall apart the moment
a judge reads one. Redeploy with the brain on:

```bash
BRAIN_BACKEND=main-agent PARALLEL_API_KEY=<the key> \
  MAIL_BACKEND=gmail make deploy PROJECT_ID=encoded-phalanx-505503-v8
```

Also true before you start:

- **One production in the system, not nine.** Every production is walked on
  every tick; a backlog is what makes the loop look slow. `scripts/reset_deployment.py`
  empties it.
- **Gmail connected**, and the token is under seven days old — a consent screen
  in testing mode issues refresh tokens that die after a week. See
  `docs/oauth-runbook.md`.
- **A second inbox open** that you control. You are going to play the seller.
- **`samples/kopitiam-nights.txt` on the desktop**, ready to drag.
- The Cloud Shell tab open on the guardrail command, already typed, not run.

---

## 0:00 — The problem

*Say this. Do not show anything yet.*

> Before a film shoots, somebody sits down with the screenplay and reads it for
> objects. Not for story — for things. A scene says *he grabbed the cup and threw
> it at the mirror*, and they write down: cup, mirror. The mirror breaks, so make
> it several mirrors.
>
> That is a real job on a real production, and it takes days. Then somebody has
> to find each of those objects, price them, and email strangers about the ones
> you cannot just buy.
>
> This is Greenlit. It does that job, and then it keeps going — for days, over
> real email, while nobody is watching. And it never buys anything.

---

## 0:30 — Read the script

*Drag `kopitiam-nights.txt` onto the chat.*

> Two pages. A kopitiam in George Town.

*While it reads — this takes a few seconds, so fill it:*

> It is not keyword-matching. It is being asked what a scene needs in order to
> **exist** — which is a different question from what the prose happens to
> mention.

---

## 1:15 — The gate, and the receipts

*The confirmation card appears. Scroll it slowly.*

> Twenty items. And under each one, the line of the screenplay it came from.
>
> That quote is the point. A model reading a script can produce a prop that was
> never in it — so a producer is not being asked to trust this list. They are
> being asked to **check** it, and the quoted line is what makes checking
> possible.

*Now the strongest thirty seconds in the demo. Scroll back through the list:*

> Three things in this script are traps, and they are deliberate.
>
> Razak *remembers his father's watch* — remembered, never on screen. The room
> *smells of rain and old smoke* — atmosphere, nothing to buy. A motorcycle
> passes *somewhere off-screen, unseen*.
>
> None of the three are on the list.

*Then, pointing at the glasses:*

> And the glasses come back as **four**, because the line says *four glasses of
> teh tarik*. It took a count the script stated rather than defaulting to one.

*Untick something — the birdcage is a good one.*

> The birdcage comes back with the bird in it. That is arguably wrong, and it
> does not matter, because nothing has happened yet. Nothing is researched,
> nothing is emailed, nobody is contacted until a person presses this button.

*Press Confirm.*

---

## 1:45 — Two roads

*Point at the route toggle before it scrolls away, or at the research card.*

> Not every prop needs a conversation. A rattan birdcage is built to order. Six
> coffee mugs are not, and emailing a stranger about mugs is faintly absurd.
>
> So each item gets a road. **Negotiate** means there is a person to write to.
> **Buy** means the agent finds shop listings and hands you the cheapest one with
> its link — you buy it on that site.
>
> The agent proposes the road, the producer can flip it here, and research
> corrects it when the evidence disagrees.

*When the research results land — point at the source links.*

> Every price band carries the URLs it came from. If there are no sources, it
> says so. A number with nothing behind it is the failure this whole system is
> built to avoid.

---

## 2:15 — The email it will not send

*The openings card appears.*

> Here is the first thing the agent wants to say to a stranger, and it is **not
> sent**. It is written and held.
>
> This message goes out of a producer's mailbox, over their name, and a model
> wrote it. So they read it first. They can edit it, they can untick any of them,
> they can change who it goes to.

*Change the recipient to your own second inbox. Say so.*

> I am going to point this one at my own inbox, because I am about to play the
> seller — a real negotiation takes days, and you have four minutes.

*Press Send.*

> That is the only gate on the conversation. Everything after this — the
> counter-offer, the chase on day three, the reply that lands on day four — runs
> unattended. A gate on each of those would mean a five-day negotiation stalls
> on whether somebody is at their desk, and running while nobody watches is the
> thing this is for.

---

## 2:45 — A real round

*Switch to your inbox. The email is there. Reply as the seller — keep it short
and slightly awkward, the way a real supplier writes.*

> RM1,400 each, minimum order of six. Can do RM1,250 if you take eight.

*Switch back to the panel. Wait.*

> Cloud Scheduler calls the loop every minute. It reads the mailbox, files that
> reply against its negotiation by Gmail thread ID, and decides what to do.
>
> There is no process sitting in memory holding this conversation. The instance
> that sent that email is long gone. What is holding it is a document in
> Firestore, and a tick that picks it back up.

*When the reply appears on screen — and it appears on its own, nobody refreshed:*

> Nobody refreshed that. Writing the reply to the database is what pushes it to
> the screen.

---

## 3:15 — Where it stops

*Open the queue / the item that has gone to `READY_FOR_HUMAN`.*

> And here it stops.
>
> There is no config flag for this. No auto-approve under five hundred ringgit.
> Every purchase waits for a person. And if a supplier's reply is confusing or
> unparseable, it escalates here too — an agent that guesses at an ambiguous
> quote is worse than one that asks.

*Show the Savings screen if there is time.*

> What the negotiating was worth: the difference between what each seller first
> asked and what was actually accepted. Measured, not projected. If there is no
> accepted price yet, it is not counted.

---

## 3:45 — The closer

*This is the beat that separates this from a demo of a prompt. Do not skip it.*

> Every agent demo tells you it will not do the dangerous thing. I want to show
> you why ours **cannot**.
>
> Purchase orders live in their own Firestore database. The agent's service
> account has no permission on it at all.

*Run the command you already have typed in Cloud Shell:*

```bash
gcloud projects get-iam-policy $PROJECT_ID --flatten='bindings[].members' \
  --filter="bindings.members:serviceAccount:cinema-agent@$PROJECT_ID.iam.gserviceaccount.com" \
  --format='table(bindings.role, bindings.condition.expression)'
```

> That binding is conditioned to one database, and the orders database is not
> it. Not a rule in a file — Firestore security rules do not even apply to
> server SDKs, so a rule denying this would constrain a browser and constrain
> nothing about the agent. This is IAM.
>
> The approval endpoint is a separate service, under a separate account, and a
> test asserts the agent's service exposes no route matching `/approve`.
>
> And an order is written with `create()`, keyed by the item. `create()` fails
> if the document exists — so a duplicate purchase order is refused by the
> storage engine before any of our code runs. Because the **item** is the key,
> ordering the same thing from two suppliers is the same violation and is
> refused the same way.

*Land it:*

> The agent can read a script, price it, and negotiate for a week. It cannot
> spend a ringgit. That is not a promise about our prompt. It is a fact about
> the infrastructure, and you can check it in one command.

---

## When it is slow

It will be. Say the true thing rather than filling air:

- **Waiting on a tick** — "It runs every minute. This is the part where, in
  production, you close the laptop and come back on Thursday."
- **Research is taking a while** — "It is searching the live web for sellers
  right now. The URLs it comes back with are the ones it actually read."
- **A rate limit** — "That is Google throttling us, and the loop backs off and
  retries rather than dropping the negotiation. One project, one mailbox,
  hackathon quotas."

Never say "it usually works." If a beat fails, name what failed, move to the
guardrail, and finish strong.

---

## What judges will ask

**"How do I know it didn't make the prop list up?"**
Every item carries the screenplay line it came from. Scroll to any one and read
the quote. The three traps in this script are the same test.

**"What if it emails the same seller twice?"**
Cloud Scheduler does not wait for one tick to finish before firing the next, so
two ticks routinely read the same due row. Each one claims the row with a
conditional write on Firestore's version stamp before it sends anything —
Firestore admits exactly one. The other is refused. Without that, a supplier's
inbox reads like an agent that pesters.

**"What happens if it crashes mid-negotiation?"**
Nothing is held in memory between requests. A Cloud Run instance lives for
minutes; a negotiation lives for days. Kill any handler halfway and the next
tick resumes from Firestore — not as a nicety, because it *will* happen,
repeatedly, over five days.

**"Could you turn the human approval off?"**
Not without redeploying a different system. The approval route is not in the
agent's service, and the account it runs under is the only one with a binding on
the orders database.

**"Is this actually sending real email?"**
Yes. That reply came from a real Gmail thread, and the agent filed it by thread
ID. `verify-deploy` reports whether a deployment is on real mail or a fake, so
we cannot demo one and claim the other.

**"How long does a real negotiation take?"**
Days. That is the whole design constraint — I compressed it here by playing the
seller myself, and I said so when I did it.

---

## Do not claim

Three things that are true of the plan and not of the build. Saying them turns a
demo into a lie a judge can catch:

- **There is no compressed replay yet.** The clock supports it; nothing uses it.
  If you want to show five days in sixty seconds, you cannot, today.
- **There is no supplier simulator.** The seller in this demo is you.
- **Do not say a scripted deployment is the real one.** If `verify-deploy`
  reported `SCRIPTED`, every email on screen is template text. Fix it or say it.
