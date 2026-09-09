# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportMissingTypeStubs=false, reportAny=false
# pyright: reportUnknownArgumentType=false
"""Exercise account creation and real password authentication on the emulator."""

import pytest
from conftest import PROJECT_ID, TokenMinter
from create_judge_account import main
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions
from orchestrator.auth import init_firebase, require_producer
from orchestrator.settings import Settings

EMAIL = "judge@example.invalid"


@pytest.fixture
def judge(tokens: TokenMinter, monkeypatch: pytest.MonkeyPatch) -> TokenMinter:
    init_firebase(Settings(gcp_project=PROJECT_ID))

    def password(_prompt: str) -> str:
        return tokens.PASSWORD

    monkeypatch.setattr("create_judge_account.getpass.getpass", password)
    return tokens


def test_created_account_can_log_in_as_producer(
    judge: TokenMinter,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(["--project", PROJECT_ID, EMAIL]) == 0
    claims = firebase_auth.verify_id_token(judge.sign_in(EMAIL))
    producer = require_producer(claims)
    assert producer.email == EMAIL
    assert producer.uid == firebase_auth.get_user_by_email(EMAIL).uid
    assert judge.PASSWORD not in capsys.readouterr().out

    # A duplicate must not reset the password or replace existing claims.
    firebase_auth.set_custom_user_claims(
        producer.uid, {"role": "producer", "other": True}
    )

    def different_password(_prompt: str) -> str:
        return "a-different-password"

    monkeypatch.setattr("create_judge_account.getpass.getpass", different_password)
    assert main(["--project", PROJECT_ID, EMAIL]) == 1
    assert firebase_auth.verify_id_token(judge.sign_in(EMAIL))["other"] is True
    assert firebase_auth.get_user_by_email(EMAIL).uid == producer.uid


def test_mismatched_passwords_do_not_create_an_account(
    judge: TokenMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter([judge.PASSWORD, "different-password"])

    def password(_prompt: str) -> str:
        return next(answers)

    monkeypatch.setattr("create_judge_account.getpass.getpass", password)
    assert main(["--project", PROJECT_ID, EMAIL]) == 2
    with pytest.raises(firebase_auth.UserNotFoundError):
        _ = firebase_auth.get_user_by_email(EMAIL)


def test_claim_failure_reports_partial_creation_without_exposing_password(
    judge: TokenMinter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refused(*_args: object, **_kwargs: object) -> None:
        raise exceptions.PermissionDeniedError(judge.PASSWORD)

    monkeypatch.setattr(
        "create_judge_account.firebase_auth.set_custom_user_claims", refused
    )
    assert main(["--project", PROJECT_ID, EMAIL]) == 1
    user = firebase_auth.get_user_by_email(EMAIL)
    assert not user.custom_claims
    shown = capsys.readouterr().out
    assert user.uid in shown
    assert "grant_producer.py" in shown
    assert judge.PASSWORD not in shown


def test_live_demo_project_is_refused_before_password_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    assert main(["--project", "demo-cinema", EMAIL]) == 2
