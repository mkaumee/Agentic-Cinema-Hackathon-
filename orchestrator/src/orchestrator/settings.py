"""Runtime configuration, read once from the environment.

Everything cloud-shaped is built so it runs locally today and switches over
later by changing configuration rather than code. There is no GCP project yet,
so ``token_backend`` defaults to ``file`` and no Google Cloud client is ever
constructed. When the project exists, set ``CINEMA_TOKEN_BACKEND=secret-manager``
and nothing else changes.

Read from the environment with the ``CINEMA_`` prefix, or from a local ``.env``
that `.gitignore` already excludes.
"""

from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

GMAIL_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
)
"""Send, and modify so we can clear the UNREAD label after reading.

Deliberately not ``gmail.readonly`` — polling has to mark mail read or every
tick re-reads the same replies. Deliberately not full ``mail.google.com``
either; the agent has no business deleting anything.
"""


class MailBackend(StrEnum):
    MEMORY = "memory"
    """No network. The default, so nothing accidentally emails a real supplier."""

    GMAIL = "gmail"
    """The real thing. Requires a refresh token to have been bootstrapped."""


class TokenBackend(StrEnum):
    FILE = "file"
    """A gitignored directory on disk. The default while there is no GCP project."""

    SECRET_MANAGER = "secret-manager"
    """Google Secret Manager. Flip to this when the project exists."""


class Settings(BaseSettings):
    """One object, built once at startup, passed down explicitly.

    Not a global and not read at point of use — a module that reaches for
    configuration on its own is a module that cannot be tested without setting
    environment variables.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="CINEMA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- identity ---------------------------------------------------------- #

    gcp_project: str = "demo-cinema"
    agent_email: str = "agent@example.invalid"
    agent_display_name: str = "Agentic Cinema"

    # -- transports -------------------------------------------------------- #

    mail_backend: MailBackend = MailBackend.MEMORY
    """Defaults to memory on purpose.

    Real email to a real seller is not something to fall into because a token
    happened to be present. Sending for real is an explicit choice:
    ``CINEMA_MAIL_BACKEND=gmail``.
    """

    oauth_client_id: str = ""
    oauth_client_secret: str = ""

    # -- credentials ------------------------------------------------------- #

    token_backend: TokenBackend = TokenBackend.FILE
    token_dir: Path = Path(".secrets")
    """Where FILE-backed refresh tokens live. Gitignored; never committed."""

    oauth_client_secrets: Path = Path(".secrets/client_secret.json")
    """The downloaded OAuth client. Only ``scripts/oauth_bootstrap.py`` reads it."""

    refresh_token_secret: str = "gmail-agent-refresh-token"
    """Secret Manager secret name, used only when token_backend is secret-manager."""

    # -- loop -------------------------------------------------------------- #

    tick_limit: int = 50
    """How many due negotiations one tick may take. Bounded so a tick that gets
    killed has done a predictable amount of work."""

    poll_query: str = "is:unread -from:me"
    """Gmail search for the inbound poll. Excludes our own sent mail."""

    @property
    def refresh_token_path(self) -> Path:
        return self.token_dir / "gmail_refresh_token.json"
