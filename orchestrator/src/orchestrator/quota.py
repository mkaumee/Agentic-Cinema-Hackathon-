"""Telling "Google is busy" apart from "this is broken".

Both arrive as an exception out of a send or a reasoning call, and the tick
treats every exception the same way: record it and move on. That is right for a
negotiation whose supplier record has vanished. It is badly wrong for a rate
limit, because of what "move on" costs here.

`_advance_negotiation` claims a row before acting, pushing its due date out by
``CLAIM_LEASE_HOURS`` so a second tick cannot pick up the same work. When the
call then fails, the row keeps that lease — so a 429 that would have cleared in
seconds stops the negotiation for **fifteen minutes**, and the retry spends
another reasoning call and another send into the very quota that was full.

Seen on the deployment, both flavours:

    429 RESOURCE_EXHAUSTED   Vertex, gemini-3.7-flash
    403 rateLimitExceeded    Gmail, "Units per minute per user"

Neither means anything is wrong. The loop is bursty by construction — three
sellers per item all become due together — so meeting these is ordinary, and
the right response is to wait a moment rather than to stand down for a quarter
of an hour.

## Why this is conservative

The asymmetry runs the other way from `bounces.py`, and is worth stating.

* A **false negative** — a real quota error we fail to recognise — costs the
  fifteen minutes we have now. Today's behaviour, no worse.
* A **false positive** — a genuinely broken negotiation mistaken for a pause —
  retries every minute forever, burning a reasoning call each time on something
  that cannot succeed.

So a bare 403 is not enough: Gmail returns 403 for "this mailbox is not yours"
as readily as for a rate limit, and those need opposite handling. The reason
string has to say so.
"""

import re

_QUOTA_MARKERS = (
    re.compile(r"resource_exhausted", re.I),
    re.compile(r"ratelimitexceeded", re.I),
    re.compile(r"quota exceeded", re.I),
    re.compile(r"\busagelimits\b", re.I),
    re.compile(r"rate limit exceeded", re.I),
    re.compile(r"too many requests", re.I),
)
"""Phrases that only appear when a limit was hit.

Matched against the exception's text because that is all the two SDKs agree on:
Vertex raises through google-genai with a JSON body, Gmail through
googleapiclient with an ``HttpError`` whose ``str()`` carries the reason. Both
put one of these in it, and nothing else in this system does.

Deliberately not matching on a status code alone. 429 is unambiguous, but 403
is shared with real refusals — an unauthorised mailbox, a revoked token — and
treating those as "wait a moment" would retry them every minute forever.
"""


def looks_like_a_quota_error(error: BaseException) -> bool:
    """True when the right response is to wait rather than to stand down.

    Reads the whole exception chain: googleapiclient and google-genai both wrap,
    and the sentence that names the limit is often on a cause rather than on the
    exception a caller catches.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = f"{type(current).__name__}: {current}"
        if any(marker.search(text) for marker in _QUOTA_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False
