"""
Google Sign-In and Calendar linkage.

Focus is on the security-relevant behaviour: refresh tokens must not be
readable from the database, the OAuth state must be unforgeable, and a
Google-only account must not be able to lock itself out.
"""
import pytest

from app.models import User


# --- Refresh-token encryption ---------------------------------------------

def test_refresh_token_is_encrypted_at_rest():
    from app.services.google import decrypt_token, encrypt_token

    secret = "1//0abcdefgSECRET-refresh-token"
    blob = encrypt_token(secret)
    assert secret not in blob, "the raw token must not appear in the stored value"
    assert decrypt_token(blob) == secret


def test_decrypt_returns_none_for_garbage():
    from app.services.google import decrypt_token

    assert decrypt_token("not-a-valid-fernet-token") is None
    assert decrypt_token(None) is None


# --- OAuth state -----------------------------------------------------------

def test_oauth_state_roundtrips_and_rejects_tampering():
    from fastapi import HTTPException

    from app.routers.google_auth import _issue_state, _read_state

    state = _issue_state(link_user_id="user-123")
    assert _read_state(state)["link"] == "user-123"

    with pytest.raises(HTTPException):
        _read_state(state + "tampered")


def test_callback_rejects_forged_state(client):
    resp = client.get("/auth/google/callback?code=abc&state=forged", follow_redirects=False)
    assert resp.status_code == 400


def test_callback_handles_user_cancelling(client):
    resp = client.get("/auth/google/callback?error=access_denied", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "google_error=cancelled" in resp.headers["location"]


# --- Availability ----------------------------------------------------------

def test_status_reports_disabled_without_credentials(client):
    assert client.get("/auth/google/status").json()["enabled"] is False


def test_login_route_503s_when_not_configured(client):
    assert client.get("/auth/google/login", follow_redirects=False).status_code == 503


# --- Account safety --------------------------------------------------------

def test_google_only_account_cannot_disconnect_itself_into_lockout(auth_client, db_session):
    """Removing Google from an account with no password would strand the user."""
    user = db_session.query(User).filter(User.email == "test@example.com").first()
    user.password_hash = None
    user.google_id = "google-abc"
    db_session.commit()

    resp = auth_client.post("/api/google/disconnect")
    assert resp.status_code == 400
    assert "locked out" in resp.json()["detail"].lower()


def test_password_account_can_disconnect_google(auth_client, db_session):
    user = db_session.query(User).filter(User.email == "test@example.com").first()
    user.google_id = "google-xyz"
    user.google_sync_enabled = True
    db_session.commit()

    assert auth_client.post("/api/google/disconnect").status_code == 200
    db_session.refresh(user)
    assert user.google_id is None and user.google_sync_enabled is False


def test_sync_requires_a_connected_account(auth_client):
    assert auth_client.post("/api/google/sync-now").status_code == 400


def test_toggle_sync_requires_a_refresh_token(auth_client):
    resp = auth_client.post("/api/google/toggle-sync", json={"enabled": True})
    assert resp.status_code == 400


def test_google_endpoints_require_auth(client):
    for path in ("/api/google/sync-now", "/api/google/disconnect"):
        assert client.post(path).status_code == 401
