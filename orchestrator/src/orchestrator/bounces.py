"""Telling a delivery failure apart from a supplier.

A bounce arrives in the same Gmail thread as the message that bounced, so it
matches the negotiation by thread id exactly as a real reply would. Handed to
the brain it reads as a supplier writing *"Address not found"*, and the brain
does what it is for and produces a negotiating move — which is where a
counter-offer to a dead mailbox comes from. Not a hallucination in the usual
sense: the agent was given a bounce and told it was a reply.

So this runs *before* the brain is called, and it is deliberately hard to
trigger. The asymmetry matters:

* a **false negative** costs one confused move on a dead address, which the
  producer sees and which stops when the rounds run out;
* a **false positive** kills a live negotiation with a real seller, silently,
  because a bounce is not escalated to anyone.

The rule is therefore "recognisably a machine", not "mentions delivery". A
supplier writing *"sorry, we could not deliver last week"* is a live
negotiation and must stay one.

Pure and separate from the tick so the near-misses can be tested cheaply, which
is the half of this worth testing.
"""

import re

MAILERS = (
    "mailer-daemon@",
    "postmaster@",
)
"""Senders that are, by convention, never a person.

RFC 5321 reserves postmaster; mailer-daemon is universal in practice. Matched
on the local part so `mailer-daemon@googlemail.com` and any other host count.
"""

SUBJECTS = (
    re.compile(r"delivery status notification", re.I),
    re.compile(r"undeliver(ed|able)", re.I),
    re.compile(r"returned mail", re.I),
    re.compile(r"mail delivery (failed|subsystem)", re.I),
    re.compile(r"failure notice", re.I),
    re.compile(r"address not found", re.I),
)
"""The standard shapes, as the big providers actually word them.

Anchored to whole recognisable phrases rather than to the word "delivery" on
its own — that word is ordinary in a conversation about hiring props and
delivering them to a set.
"""

DSN_CONTENT_TYPE = re.compile(r"report-type=[\"']?delivery-status", re.I)
"""The unambiguous one, when we have the header: RFC 3464 says this is a DSN
and nothing else uses it."""


def looks_like_a_bounce(
    *, from_email: str, subject: str, content_type: str = ""
) -> bool:
    """True when this is a machine telling us the message did not arrive.

    Any one signal is enough, because each is on its own strong: a human does
    not write from ``mailer-daemon@``, and a supplier does not put
    ``report-type=delivery-status`` on a reply about a mirror.
    """
    sender = from_email.strip().lower()
    if any(sender.startswith(mailer) or f"<{mailer}" in sender for mailer in MAILERS):
        return True
    if DSN_CONTENT_TYPE.search(content_type):
        return True
    return any(pattern.search(subject) for pattern in SUBJECTS)


def bounce_note(from_email: str, subject: str) -> str:
    """One line for the record, written for a person reading it later.

    Kept because the producer is never interrupted about this: an address that
    silently stops being written to has to be explainable when they ask, or the
    agent is simply unaccountable.
    """
    who = from_email.strip() or "the mail system"
    return (
        f"The email did not reach them — {who} returned it "
        f"({subject.strip() or 'no subject'}). Stopped writing to this address."
    )
