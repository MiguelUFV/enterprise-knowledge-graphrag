import importlib

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def _load_auth(monkeypatch, environment: str, auth_required: str = ""):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("AUTH_REQUIRED", auth_required)
    monkeypatch.setenv("JWT_SECRET_KEY", "k" * 64)
    monkeypatch.setenv("WEBHOOK_API_KEY", "w" * 40)
    import app.core.secrets as secrets_mod
    import app.core.auth as auth_mod
    importlib.reload(secrets_mod)
    return importlib.reload(auth_mod)


def _client(auth):
    api = FastAPI()

    @api.get("/admin")
    def admin(user=Depends(auth.require_role(auth.Role.ADMIN))):
        return {"user": user.user_id, "role": user.role}

    return TestClient(api)


@pytest.fixture(autouse=True)
def restore_dev_auth(monkeypatch):
    yield
    monkeypatch.undo()
    _load_auth(monkeypatch, "development")


def test_production_rejects_spoofed_headers(monkeypatch):
    auth = _load_auth(monkeypatch, "production")
    res = _client(auth).get("/admin", headers={"X-User-ID": "attacker", "X-Access-Level": "admin"})
    assert res.status_code == 401


def test_production_cannot_disable_auth(monkeypatch):
    auth = _load_auth(monkeypatch, "production", auth_required="false")
    assert auth.AUTH_REQUIRED is True


def test_valid_jwt_grants_its_role(monkeypatch):
    auth = _load_auth(monkeypatch, "production")
    token = auth.create_access_token(user_id="ana", role="admin")
    res = _client(auth).get("/admin", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == {"user": "ana", "role": "admin"}


def test_insufficient_role_is_forbidden(monkeypatch):
    auth = _load_auth(monkeypatch, "production")
    token = auth.create_access_token(user_id="bob", role="standard")
    res = _client(auth).get("/admin", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_tampered_jwt_is_rejected(monkeypatch):
    auth = _load_auth(monkeypatch, "production")
    token = auth.create_access_token(user_id="ana", role="standard")
    res = _client(auth).get("/admin", headers={"Authorization": f"Bearer {token[:-2]}xx"})
    assert res.status_code == 401


def test_development_headers_allowed_for_local_testing(monkeypatch):
    auth = _load_auth(monkeypatch, "development")
    res = _client(auth).get("/admin", headers={"X-User-ID": "dev", "X-Access-Level": "standard"})
    assert res.status_code == 403
