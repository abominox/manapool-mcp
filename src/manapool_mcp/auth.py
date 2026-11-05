"""Optional ManaPool API credentials.

ManaPool's authenticated endpoints want *two* headers, not one bearer token:
the account e-mail and an access token (``mpat_...``). Both are required, so a
half-configured environment is reported as a problem instead of being silently
ignored -- otherwise the failure looks like a bad key rather than a missing
variable.

Nothing here is needed for the anonymous tools. This module only answers "are
credentials configured, and which one is missing".
"""

from __future__ import annotations

import os
from dataclasses import dataclass

EMAIL_ENV = "MANAPOOL_EMAIL"
TOKEN_ENV = "MANAPOOL_ACCESS_TOKEN"
TOKEN_ALIASES = (TOKEN_ENV, "MANAPOOL_API_KEY")

CONFIG_HINT = f"Set {EMAIL_ENV} and {TOKEN_ENV} (the token starts with 'mpat_')."


@dataclass(frozen=True)
class Credential:
    """A ManaPool API identity. Kept out of reprs and error messages."""

    email: str
    token: str

    def headers(self) -> dict[str, str]:
        return {
            "X-ManaPool-Email": self.email,
            "X-ManaPool-Access-Token": self.token,
        }

    def __repr__(self) -> str:  # pragma: no cover - defensive, never log the token
        return f"Credential(email={self.email!r}, token=...)"


def _read() -> tuple[str, str]:
    email = (os.environ.get(EMAIL_ENV) or "").strip()
    token = ""
    for name in TOKEN_ALIASES:
        token = (os.environ.get(name) or "").strip()
        if token:
            break
    return email, token


def load_credentials() -> Credential | None:
    """The configured credential pair, or None when nothing is set."""
    email, token = _read()
    if email and token:
        return Credential(email=email, token=token)
    return None


def configured() -> bool:
    return load_credentials() is not None


def credential_problem() -> str | None:
    """For the half-configured case: what is missing. None when fine."""
    email, token = _read()
    if not email and not token:
        return None
    if not email:
        return f"{EMAIL_ENV} is not set. Both it and {TOKEN_ENV} are required."
    if not token:
        return f"{TOKEN_ENV} is not set. Both it and {EMAIL_ENV} are required."
    return None
