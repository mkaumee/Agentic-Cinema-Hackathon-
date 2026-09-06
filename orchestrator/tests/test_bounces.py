"""Telling a mail system apart from a supplier.

The near-misses are the point. A false negative costs one confused move on a
dead address; a false positive kills a live negotiation with a real seller,
silently, because a bounce is deliberately never escalated to anyone. So the
tests that matter most are the ones asserting a human still counts as a human.
"""

import pytest
from orchestrator.bounces import bounce_note, looks_like_a_bounce


@pytest.mark.parametrize(
    "sender",
    [
        "mailer-daemon@googlemail.com",
        "MAILER-DAEMON@example.invalid",
        "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
        "postmaster@outlook.com",
    ],
)
def test_the_usual_senders_are_machines(sender: str) -> None:
    assert looks_like_a_bounce(from_email=sender, subject="Re: Hire enquiry")


@pytest.mark.parametrize(
    "subject",
    [
        "Delivery Status Notification (Failure)",
        "Undelivered Mail Returned to Sender",
        "Undeliverable: Hire enquiry — wall mirror",
        "Returned mail: see transcript for details",
        "Mail delivery failed: returning message to sender",
        "Failure notice",
        "Address not found",
    ],
)
def test_the_standard_subjects_are_recognised(subject: str) -> None:
    assert looks_like_a_bounce(from_email="noreply@example.invalid", subject=subject)


def test_a_delivery_status_report_is_unambiguous() -> None:
    assert looks_like_a_bounce(
        from_email="someone@example.invalid",
        subject="anything at all",
        content_type='multipart/report; report-type=delivery-status; boundary="x"',
    )


@pytest.mark.parametrize(
    ("sender", "subject"),
    [
        # The one that would hurt: a real seller, using the word ordinarily.
        ("ahseng@example.invalid", "Re: Hire enquiry — we could not deliver last week"),
        ("sales@example.invalid", "Re: mirror — delivery is RM50 extra"),
        ("hire@example.invalid", "Delivery and collection times"),
        ("ops@example.invalid", "Re: Hire enquiry — address for delivery?"),
        ("info@example.invalid", "Failure to agree on price"),
    ],
)
def test_a_supplier_talking_about_delivery_is_still_a_supplier(
    sender: str, subject: str
) -> None:
    """The expensive mistake. Killing this negotiation would be silent, because
    a bounce is never escalated — the producer would simply never hear from a
    seller who was answering them."""
    assert not looks_like_a_bounce(from_email=sender, subject=subject)


def test_a_plain_reply_is_not_a_bounce() -> None:
    assert not looks_like_a_bounce(
        from_email="ahseng@example.invalid", subject="Re: Hire enquiry — wall mirror"
    )


def test_the_note_says_what_happened_and_what_was_done() -> None:
    """Written for a producer reading it days later, because they are never
    interrupted about this and can only find out by asking."""
    note = bounce_note("mailer-daemon@googlemail.com", "Address not found")

    assert "mailer-daemon@googlemail.com" in note
    assert "Address not found" in note
    assert "Stopped writing" in note


def test_the_note_survives_an_empty_bounce() -> None:
    assert bounce_note("", "") != ""
