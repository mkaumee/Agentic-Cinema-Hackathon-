"""Telling a rate limit apart from a real refusal.

The strings below are the ones the deployment actually produced, pasted rather
than invented — a detector tested against text somebody imagined is a detector
that recognises imagination.
"""

from orchestrator.quota import looks_like_a_quota_error

VERTEX_429 = (
    "\nOn how to mitigate this issue, please refer to:\n"
    "https://google.github.io/adk-docs/agents/models/google-gemini/"
    "#error-code-429-resource_exhausted\n\n\n"
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'Resource "
    "exhausted. Please try again later.', 'status': 'RESOURCE_EXHAUSTED'}}"
)

GMAIL_403 = (
    "<HttpError 403 when requesting https://gmail.googleapis.com/gmail/v1/"
    'users/me/messages/send?alt=json returned "Quota exceeded for quota '
    "metric 'Total Query Cost' and limit 'Units per minute per user' of "
    "service 'gmail.googleapis.com' for consumer "
    "'project_number:678371873554'.\". Details: \"[{'message': 'Quota "
    "exceeded', 'domain': 'usageLimits', 'reason': 'rateLimitExceeded'}]\">"
)


def test_vertex_being_busy_is_a_pause() -> None:
    assert looks_like_a_quota_error(RuntimeError(VERTEX_429))


def test_gmail_being_busy_is_a_pause() -> None:
    assert looks_like_a_quota_error(RuntimeError(GMAIL_403))


def test_it_reads_through_a_wrapper() -> None:
    """Both SDKs wrap, and the sentence naming the limit is often on the cause.

    googleapiclient raises HttpError from inside its own retry, and google-genai
    re-raises through the ADK — so the exception a caller catches is rarely the
    one carrying the text.
    """
    try:
        try:
            raise RuntimeError(VERTEX_429)
        except RuntimeError as cause:
            raise ValueError("next_move failed") from cause
    except ValueError as wrapped:
        assert looks_like_a_quota_error(wrapped)


# --------------------------------------------------------------------------- #
# The near-misses, which are the half worth testing
# --------------------------------------------------------------------------- #


def test_a_mailbox_that_is_not_ours_is_not_a_pause() -> None:
    """Gmail returns 403 for this too, and it needs the opposite handling.

    Retried every minute it would burn a reasoning call forever on something
    that cannot succeed. A bare status code is not enough to tell these apart —
    only the reason is.
    """
    refusal = (
        "<HttpError 403 when requesting https://gmail.googleapis.com/gmail/v1/"
        'users/me/messages/send?alt=json returned "Delegation denied for '
        "user@example.invalid\". Details: \"[{'message': 'Delegation denied', "
        "'domain': 'global', 'reason': 'forbidden'}]\">"
    )
    assert not looks_like_a_quota_error(RuntimeError(refusal))


def test_an_ordinary_failure_is_not_a_pause() -> None:
    assert not looks_like_a_quota_error(RuntimeError("supplier record vanished"))


def test_a_missing_document_is_not_a_pause() -> None:
    assert not looks_like_a_quota_error(LookupError("404 no such negotiation"))


def test_a_seller_writing_about_limits_is_not_a_pause() -> None:
    """Nothing here should ever read message bodies, but if a body were ever
    folded into an error, a supplier's own words must not park the loop."""
    assert not looks_like_a_quota_error(
        RuntimeError("Supplier wrote: we have a limit of 4 per order, exceeded")
    )


def test_a_cycle_in_the_chain_terminates() -> None:
    """Contrived, and cheap insurance: a __context__ loop would hang the tick."""
    first = RuntimeError("one")
    second = RuntimeError("two")
    first.__cause__ = second
    second.__cause__ = first

    assert not looks_like_a_quota_error(first)
