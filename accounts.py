"""
Google accounts for Drive Sync.

Each signed-in account has its own OAuth token file: APP_DIR/accounts/<email>.json.
Folders in config.json say which account they sync to ("account": "<email>").
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from drive_sync import APP_DIR, SCOPES

ACCOUNTS_DIR = APP_DIR / "accounts"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
# Emails become file names, so refuse anything with path or Windows-reserved characters.
_EMAIL = re.compile(r'^[^@\s/\\:*?"<>|]+@[^@\s/\\:*?"<>|]+\.[^@\s/\\:*?"<>|]+$')

log = logging.getLogger("drive_sync")


class NeedsSignIn(Exception):
    """The account's login expired or was revoked - the user must sign in again."""


def token_path(email: str, base: Path | None = None) -> Path:
    base = base or ACCOUNTS_DIR
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise ValueError(f"Not a valid account email: {email!r}")
    return base / f"{email}.json"


def list_accounts(base: Path | None = None) -> list[str]:
    base = base or ACCOUNTS_DIR
    if not base.is_dir():
        return []
    return sorted(p.stem for p in base.glob("*.json") if _EMAIL.match(p.stem))


def save_account(email: str, token_json: str, base: Path | None = None) -> Path:
    base = base or ACCOUNTS_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = token_path(email, base)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token_json, encoding="utf-8")
    tmp.replace(path)
    return path


def has_required_scopes(path: Path) -> bool:
    """False for logins granted before Drive Sync needed full Drive access."""
    try:
        granted = json.loads(path.read_text(encoding="utf-8")).get("scopes") or []
    except (OSError, ValueError):
        return False
    if isinstance(granted, str):
        granted = granted.split()
    return all(s in granted for s in SCOPES)


def load_credentials(email: str, base: Path | None = None):
    """Return valid credentials for email, refreshing (and saving) them if needed."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(email, base)
    if not path.exists():
        raise NeedsSignIn(f"{email} is not signed in")
    if not has_required_scopes(path):
        raise NeedsSignIn(f"{email} needs to sign in again to give Drive Sync access to choose Drive folders")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise NeedsSignIn(f"The login for {email} has expired")
        try:
            creds.refresh(Request())
        except RefreshError as e:  # revoked, password changed, 7-day test-mode expiry, ...
            raise NeedsSignIn(f"The login for {email} has expired or was removed ({e})") from e
        save_account(email, creds.to_json(), base)
    return creds


def remove_account(email: str, base: Path | None = None, revoke: bool = True) -> None:
    """Forget the account on this PC and (best effort) revoke Drive Sync's access at Google."""
    path = token_path(email, base)
    if revoke and path.exists():
        try:
            import requests

            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get("refresh_token") or data.get("token")
            if token:
                requests.post(REVOKE_URL, data={"token": token}, timeout=10)
        except Exception as e:  # noqa: BLE001 - offline etc.; the local token is still deleted
            log.warning("Couldn't revoke Google access for %s: %s", email, e)
    path.unlink(missing_ok=True)


def sign_in(credentials_path: Path, login_hint: str | None = None, timeout: int = 300,
            browser_profile: str | None = None):
    """Open the browser so the user can pick a Google account. Returns new credentials.

    browser_profile: open Google's sign-in page in this browser profile (see browsers.py)
    instead of whichever profile the default browser used last.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    import browsers

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    # select_account: always show Google's account chooser (never silently reuse the
    # browser's current account). consent: guarantees a refresh token for every account.
    extra = {"prompt": "select_account consent"}
    if login_hint:
        extra["login_hint"] = login_hint
    return flow.run_local_server(
        port=0, timeout_seconds=timeout, authorization_prompt_message="",
        success_message="Signed in to Drive Sync. You can close this tab.",
        browser=browsers.register_controller(browser_profile), **extra)


def account_email(service) -> str:
    about = service.about().get(fields="user(emailAddress)").execute(num_retries=3)
    return about["user"]["emailAddress"].strip().lower()
