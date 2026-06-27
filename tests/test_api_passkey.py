"""T6-T10: Passkey (WebAuthn) ルーターのテスト。

すべてのテストは暗号演算・Firestore・ネットワークを実行しない。
webauthn ラッパ関数は patch でモック化し、mock_db で DB 操作をスタブする。

fixtures:
  passkey_client         - 未認証 (get_current_user は本物 → mock_db.get_session 次第)
  passkey_auth_client    - get_current_user を current_session で override 済み
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from shared.models import Session, User, WebAuthnChallenge, WebAuthnCredential
from shared.webauthn_config import WebAuthnConfig

# ── テスト用定数 ─────────────────────────────────────────────────────────────

_ENV = {
    "API_KEY": "test-key",
    "WEBAUTHN_RP_ID": "localhost",
    "WEBAUTHN_ORIGIN": "http://localhost",
    "WEBAUTHN_RP_NAME": "test",
    "CSRF_PROTECTION_ENABLED": "false",
    "API_RATELIMIT_MAX_REQUESTS": "0",
    "SESSION_COOKIE_SECURE": "false",
    "USER_ID": "user1",
}

_DUMMY_OPTIONS_JSON = json.dumps({"challenge": "dGVzdA", "timeout": 60000})

_TEST_CONFIG = WebAuthnConfig(
    rp_id="localhost",
    rp_name="test",
    origins={"http://localhost"},
    timeout_ms=60000,
)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_audit():
    return MagicMock()


@pytest.fixture
def current_session():
    now = datetime.now(timezone.utc)
    return Session(
        session_id="sess-001",
        user_id="user1",
        username="testuser",
        role="user",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )


def _setup_passkey_app(mock_db, mock_audit, current_session=None, webauthn_config=_TEST_CONFIG):
    """passkey ルーターを含む app を構成して (app_module, overrides_dict) を返す。

    呼び出し元は yield の前後で dependency_overrides.clear() を行うこと。
    """
    import api.ratelimit
    importlib.reload(api.ratelimit)

    import api.routers.passkey as passkey_mod
    importlib.reload(passkey_mod)

    import api.main as m
    importlib.reload(m)

    from fastapi import Depends, Security
    from api.main import verify_api_key
    from api.ratelimit import rate_limit

    m.app.include_router(
        passkey_mod.router,
        prefix="",
        dependencies=[Security(verify_api_key), Depends(rate_limit("api"))],
    )

    from api.dependencies import (
        get_audit_logger,
        get_current_user,
        get_email_sender,
        get_firestore_client,
        get_storage_client,
        get_user_id,
        get_webauthn_config,
    )
    get_firestore_client.cache_clear()
    get_audit_logger.cache_clear()
    get_email_sender.cache_clear()
    get_storage_client.cache_clear()

    m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
    m.app.dependency_overrides[get_audit_logger] = lambda: mock_audit
    m.app.dependency_overrides[get_user_id] = lambda: "user1"
    # get_webauthn_config を直接 override して lru_cache・環境変数依存を排除
    cfg = webauthn_config
    m.app.dependency_overrides[get_webauthn_config] = lambda: cfg
    if current_session is not None:
        m.app.dependency_overrides[get_current_user] = lambda: current_session

    return m


@pytest.fixture
def passkey_auth_client(mock_db, mock_audit, current_session):
    """認証済み passkey テストクライアント。"""
    with patch.dict("os.environ", _ENV):
        m = _setup_passkey_app(mock_db, mock_audit, current_session)
        yield TestClient(m.app, headers={"X-API-Key": "test-key"})
        m.app.dependency_overrides.clear()


@pytest.fixture
def passkey_client(mock_db, mock_audit):
    """未認証 passkey テストクライアント。"""
    with patch.dict("os.environ", _ENV):
        m = _setup_passkey_app(mock_db, mock_audit, current_session=None)
        yield TestClient(m.app, headers={"X-API-Key": "test-key"})
        m.app.dependency_overrides.clear()


# ── T6: POST /auth/passkey/register/options ──────────────────────────────────


class TestRegisterOptions:
    def test_requires_auth(self, passkey_client):
        """未認証は 401。"""
        resp = passkey_client.post("/auth/passkey/register/options")
        assert resp.status_code == 401

    def test_503_when_webauthn_not_configured(self, mock_db, mock_audit, current_session):
        """WEBAUTHN_RP_ID 未設定なら 503。"""
        env_no_rp = {**_ENV, "WEBAUTHN_RP_ID": ""}
        with patch.dict("os.environ", env_no_rp):
            importlib.reload(importlib.import_module("api.routers.passkey"))
            import api.routers.passkey as passkey_mod
            importlib.reload(passkey_mod)
            import api.main as m
            importlib.reload(m)

            from fastapi import Depends, Security
            from api.main import verify_api_key
            from api.ratelimit import rate_limit
            from api.dependencies import (
                get_audit_logger, get_current_user, get_firestore_client,
                get_webauthn_config, get_user_id,
            )

            m.app.include_router(
                passkey_mod.router, prefix="",
                dependencies=[Security(verify_api_key), Depends(rate_limit("api"))],
            )
            get_webauthn_config.cache_clear()
            m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
            m.app.dependency_overrides[get_audit_logger] = lambda: mock_audit
            m.app.dependency_overrides[get_current_user] = lambda: current_session
            m.app.dependency_overrides[get_user_id] = lambda: "user1"

            client = TestClient(m.app, headers={"X-API-Key": "test-key"})
            resp = client.post("/auth/passkey/register/options")
            m.app.dependency_overrides.clear()

        assert resp.status_code == 503

    def test_returns_challenge_id_and_options(self, passkey_auth_client, mock_db):
        """正常系: challenge_id と options JSON を返す。"""
        mock_db.get_credentials_by_user.return_value = []

        with patch(
            "api.routers.passkey.generate_registration_options_wrapper",
            return_value=_DUMMY_OPTIONS_JSON,
        ):
            resp = passkey_auth_client.post("/auth/passkey/register/options")

        assert resp.status_code == 200
        body = resp.json()
        assert "challenge_id" in body
        assert "options" in body
        assert isinstance(body["challenge_id"], str)

    def test_saves_challenge_to_db(self, passkey_auth_client, mock_db):
        """challenge が DB に保存される。"""
        mock_db.get_credentials_by_user.return_value = []

        with patch(
            "api.routers.passkey.generate_registration_options_wrapper",
            return_value=_DUMMY_OPTIONS_JSON,
        ):
            passkey_auth_client.post("/auth/passkey/register/options")

        mock_db.save_challenge.assert_called_once()
        saved = mock_db.save_challenge.call_args[0][0]
        assert saved.type == "registration"
        assert saved.user_id == "user1"

    def test_excludes_existing_credentials(self, passkey_auth_client, mock_db, current_session):
        """既存クレデンシャルが exclude_credentials に渡される。"""
        now = datetime.now(timezone.utc)
        existing_cred = WebAuthnCredential(
            credential_id="existing-cred-id",
            user_id=current_session.user_id,
            username=current_session.username,
            public_key="pk",
            sign_count=0,
            transports=[],
            created_at=now,
        )
        mock_db.get_credentials_by_user.return_value = [existing_cred]

        with patch(
            "api.routers.passkey.generate_registration_options_wrapper",
            return_value=_DUMMY_OPTIONS_JSON,
        ) as mock_gen:
            passkey_auth_client.post("/auth/passkey/register/options")

        call_kwargs = mock_gen.call_args[1]
        assert "existing-cred-id" in call_kwargs.get("exclude_credential_ids", [])


# ── T7: POST /auth/passkey/register/verify ──────────────────────────────────


class TestRegisterVerify:
    def _make_challenge(self, user_id="user1", expired=False):
        now = datetime.now(timezone.utc)
        expires_at = now - timedelta(seconds=1) if expired else now + timedelta(minutes=5)
        return WebAuthnChallenge(
            challenge_id="chal-123",
            challenge="dGVzdA",
            user_id=user_id,
            type="registration",
            expires_at=expires_at,
            created_at=now,
        )

    def test_requires_auth(self, passkey_client):
        """未認証は 401。"""
        resp = passkey_client.post("/auth/passkey/register/verify", json={
            "challenge_id": "chal-123",
            "credential": {"id": "cred-id", "rawId": "cred-id", "response": {}, "type": "public-key"},
        })
        assert resp.status_code == 401

    def test_valid_registration_saves_credential(self, passkey_auth_client, mock_db, current_session):
        """正常系: クレデンシャルを保存して 200。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        mock_db.get_credential_by_id.return_value = None  # 重複なし

        verified = {
            "credential_id": "new-cred-id",
            "public_key": "pubkey-b64",
            "sign_count": 0,
            "aaguid": None,
            "transports": ["internal"],
        }
        with patch("api.routers.passkey.verify_registration_response_wrapper", return_value=verified):
            resp = passkey_auth_client.post("/auth/passkey/register/verify", json={
                "challenge_id": "chal-123",
                "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
            })

        assert resp.status_code == 200
        mock_db.save_credential.assert_called_once()

    def test_expired_challenge_returns_400(self, passkey_auth_client, mock_db):
        """消費済み/期限切れ challenge は 400。"""
        mock_db.consume_challenge.return_value = None  # consume returns None → expired/missing

        with patch("api.routers.passkey.verify_registration_response_wrapper", return_value={}):
            resp = passkey_auth_client.post("/auth/passkey/register/verify", json={
                "challenge_id": "chal-expired",
                "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
            })

        assert resp.status_code == 400

    def test_user_id_mismatch_returns_400(self, passkey_auth_client, mock_db, current_session):
        """challenge の user_id がセッションと異なる場合 400。"""
        wrong_user_challenge = self._make_challenge(user_id="other-user")
        mock_db.consume_challenge.return_value = wrong_user_challenge

        with patch("api.routers.passkey.verify_registration_response_wrapper", return_value={}):
            resp = passkey_auth_client.post("/auth/passkey/register/verify", json={
                "challenge_id": "chal-123",
                "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
            })

        assert resp.status_code == 400

    def test_duplicate_credential_returns_409(self, passkey_auth_client, mock_db):
        """同じ credential_id が既存なら 409。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        now = datetime.now(timezone.utc)
        existing = WebAuthnCredential(
            credential_id="dup-cred-id", user_id="user1", username="testuser",
            public_key="pk", sign_count=0, transports=[], created_at=now,
        )
        mock_db.get_credential_by_id.return_value = existing  # already exists

        verified = {
            "credential_id": "dup-cred-id",
            "public_key": "pubkey-b64",
            "sign_count": 0,
            "aaguid": None,
            "transports": [],
        }
        with patch("api.routers.passkey.verify_registration_response_wrapper", return_value=verified):
            resp = passkey_auth_client.post("/auth/passkey/register/verify", json={
                "challenge_id": "chal-123",
                "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
            })

        assert resp.status_code == 409

    def test_verification_failure_returns_400(self, passkey_auth_client, mock_db):
        """webauthn 検証失敗 (例外) は 400。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        mock_db.get_credential_by_id.return_value = None

        with patch(
            "api.routers.passkey.verify_registration_response_wrapper",
            side_effect=Exception("invalid"),
        ):
            resp = passkey_auth_client.post("/auth/passkey/register/verify", json={
                "challenge_id": "chal-123",
                "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
            })

        assert resp.status_code == 400
        mock_db.save_credential.assert_not_called()

    def test_audit_logged_on_success(self, passkey_auth_client, mock_db, mock_audit):
        """成功時に passkey_register 監査ログが記録される。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        mock_db.get_credential_by_id.return_value = None
        verified = {
            "credential_id": "cred-id", "public_key": "pk",
            "sign_count": 0, "aaguid": None, "transports": [],
        }
        with patch("api.routers.passkey.verify_registration_response_wrapper", return_value=verified):
            passkey_auth_client.post("/auth/passkey/register/verify", json={
                "challenge_id": "chal-123",
                "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
            })

        mock_audit.record.assert_called_once()
        call_kwargs = mock_audit.record.call_args[1]
        assert call_kwargs.get("action") == "passkey_register"

    def test_authentication_challenge_in_register_verify_returns_400(
        self, passkey_auth_client, mock_db, current_session
    ):
        """authentication タイプの challenge を register/verify に渡すと 400（多層防御）。

        login/options で発行した challenge を register/verify に横流しする攻撃を防ぐ。
        webauthn 検証が成功するとしても challenge.type チェックで弾かれるべきである。
        """
        now = datetime.now(timezone.utc)
        wrong_type_challenge = WebAuthnChallenge(
            challenge_id="chal-wrong",
            challenge="dGVzdA",
            user_id=current_session.user_id,
            type="authentication",  # registration ではなく authentication
            expires_at=now + timedelta(minutes=5),
            created_at=now,
        )
        mock_db.consume_challenge.return_value = wrong_type_challenge
        mock_db.get_credential_by_id.return_value = None  # 重複なし

        # webauthn 検証が成功してもクレデンシャルは保存されない（type チェックが先）
        verified = {
            "credential_id": "new-cred-id",
            "public_key": "pubkey-b64",
            "sign_count": 0,
            "aaguid": None,
            "transports": [],
        }
        with patch("api.routers.passkey.verify_registration_response_wrapper", return_value=verified):
            resp = passkey_auth_client.post("/auth/passkey/register/verify", json={
                "challenge_id": "chal-wrong",
                "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
            })

        assert resp.status_code == 400
        mock_db.save_credential.assert_not_called()


# ── T8: POST /auth/passkey/login/options ────────────────────────────────────


class TestLoginOptions:
    def test_returns_challenge_id_and_options(self, passkey_auth_client, mock_db):
        """usernameless: challenge_id と options を返す。"""
        with patch(
            "api.routers.passkey.generate_authentication_options_wrapper",
            return_value=_DUMMY_OPTIONS_JSON,
        ):
            resp = passkey_auth_client.post("/auth/passkey/login/options", json={})

        assert resp.status_code == 200
        body = resp.json()
        assert "challenge_id" in body
        assert "options" in body

    def test_saves_challenge_with_no_user_id(self, passkey_auth_client, mock_db):
        """usernameless の場合 challenge.user_id は None。"""
        with patch(
            "api.routers.passkey.generate_authentication_options_wrapper",
            return_value=_DUMMY_OPTIONS_JSON,
        ):
            passkey_auth_client.post("/auth/passkey/login/options", json={})

        saved = mock_db.save_challenge.call_args[0][0]
        assert saved.user_id is None
        assert saved.type == "authentication"

    def test_unknown_username_still_200(self, passkey_auth_client, mock_db):
        """未知の username でも 200（列挙対策）。"""
        mock_db.get_user.return_value = None  # unknown user
        with patch(
            "api.routers.passkey.generate_authentication_options_wrapper",
            return_value=_DUMMY_OPTIONS_JSON,
        ):
            resp = passkey_auth_client.post(
                "/auth/passkey/login/options", json={"username": "ghost"}
            )

        assert resp.status_code == 200

    def test_known_username_allow_credentials_is_always_empty(
        self, passkey_auth_client, mock_db
    ):
        """既知 username でも allowCredentials は常に空（ユーザー列挙・credential 漏洩防止）。

        discoverable credential フローを前提とするため、username があっても
        generate_authentication_options_wrapper に渡す allow_credential_ids は [] にする。
        """
        now = datetime.now(timezone.utc)
        user = User(
            username="alice",
            user_id="user-alice",
            password_hash="$2b$12$x",
            role="user",
            display_name="Alice",
            created_at=now,
            updated_at=now,
        )
        mock_db.get_user.return_value = user
        mock_db.get_credentials_by_user.return_value = [
            WebAuthnCredential(
                credential_id="alice-cred-id",
                user_id="user-alice",
                username="alice",
                public_key="pk",
                sign_count=0,
                transports=[],
                created_at=now,
            )
        ]

        with patch(
            "api.routers.passkey.generate_authentication_options_wrapper",
            return_value=_DUMMY_OPTIONS_JSON,
        ) as mock_gen:
            resp = passkey_auth_client.post(
                "/auth/passkey/login/options", json={"username": "alice"}
            )

        assert resp.status_code == 200
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("allow_credential_ids") == [], (
            "allowCredentials must be empty regardless of username to prevent user enumeration"
        )

    def test_known_and_unknown_username_produce_identical_response_shape(
        self, passkey_auth_client, mock_db
    ):
        """既知/未知 username でレスポンス形状が不可識別（challenge_id と options のみ）。"""
        now = datetime.now(timezone.utc)
        user = User(
            username="known",
            user_id="user-known",
            password_hash="$2b$12$x",
            role="user",
            display_name="Known User",
            created_at=now,
            updated_at=now,
        )
        mock_db.get_user.side_effect = [user, None]
        mock_db.get_credentials_by_user.return_value = []

        with patch(
            "api.routers.passkey.generate_authentication_options_wrapper",
            return_value=_DUMMY_OPTIONS_JSON,
        ):
            resp_known = passkey_auth_client.post(
                "/auth/passkey/login/options", json={"username": "known"}
            )
            resp_unknown = passkey_auth_client.post(
                "/auth/passkey/login/options", json={"username": "ghost"}
            )

        assert resp_known.status_code == resp_unknown.status_code == 200
        assert set(resp_known.json().keys()) == set(resp_unknown.json().keys()) == {
            "challenge_id", "options"
        }

    def test_503_when_not_configured(self, mock_db, mock_audit, current_session):
        """WebAuthn 未設定は 503。"""
        env_no_rp = {**_ENV, "WEBAUTHN_RP_ID": ""}
        with patch.dict("os.environ", env_no_rp):
            importlib.reload(importlib.import_module("api.routers.passkey"))
            import api.routers.passkey as passkey_mod
            importlib.reload(passkey_mod)
            import api.main as m
            importlib.reload(m)

            from fastapi import Depends, Security
            from api.main import verify_api_key
            from api.ratelimit import rate_limit
            from api.dependencies import (
                get_audit_logger, get_current_user, get_firestore_client,
                get_webauthn_config, get_user_id,
            )
            m.app.include_router(
                passkey_mod.router, prefix="",
                dependencies=[Security(verify_api_key), Depends(rate_limit("api"))],
            )
            get_webauthn_config.cache_clear()
            m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
            m.app.dependency_overrides[get_audit_logger] = lambda: mock_audit
            m.app.dependency_overrides[get_current_user] = lambda: current_session
            m.app.dependency_overrides[get_user_id] = lambda: "user1"

            client = TestClient(m.app, headers={"X-API-Key": "test-key"})
            resp = client.post("/auth/passkey/login/options", json={})
            m.app.dependency_overrides.clear()

        assert resp.status_code == 503


# ── T9: POST /auth/passkey/login/verify ─────────────────────────────────────


class TestLoginVerify:
    def _make_challenge(self):
        now = datetime.now(timezone.utc)
        return WebAuthnChallenge(
            challenge_id="auth-chal-1",
            challenge="dGVzdA",
            user_id=None,
            type="authentication",
            expires_at=now + timedelta(minutes=5),
            created_at=now,
        )

    def _make_credential(self, sign_count=0):
        now = datetime.now(timezone.utc)
        return WebAuthnCredential(
            credential_id="cred-b64url",
            user_id="user1",
            username="testuser",
            public_key="pubkey-b64",
            sign_count=sign_count,
            transports=[],
            created_at=now,
        )

    def _make_user(self):
        now = datetime.now(timezone.utc)
        return User(
            username="testuser",
            user_id="user1",
            password_hash="$2b$12$hashed",
            role="user",
            display_name="Test User",
            created_at=now,
            updated_at=now,
        )

    def test_valid_login_returns_token_and_user(self, passkey_auth_client, mock_db):
        """正常系: token と user を返し 200。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        mock_db.get_credential_by_id.return_value = self._make_credential(sign_count=0)
        mock_db.get_user.return_value = self._make_user()
        mock_db.create_session.return_value = None

        verified_auth = {"credential_id": "cred-b64url", "new_sign_count": 1}
        with patch("api.routers.passkey.verify_authentication_response_wrapper",
                   return_value=verified_auth):
            with patch("api.routers.passkey.is_sign_count_valid", return_value=True):
                resp = passkey_auth_client.post("/auth/passkey/login/verify", json={
                    "challenge_id": "auth-chal-1",
                    "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
                })

        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert "user" in body

    def test_unknown_credential_returns_401(self, passkey_auth_client, mock_db):
        """未知の credential は 401（原因秘匿）。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        mock_db.get_credential_by_id.return_value = None  # not found

        verified_auth = {"credential_id": "unknown-cred", "new_sign_count": 1}
        with patch("api.routers.passkey.verify_authentication_response_wrapper",
                   return_value=verified_auth):
            resp = passkey_auth_client.post("/auth/passkey/login/verify", json={
                "challenge_id": "auth-chal-1",
                "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
            })

        assert resp.status_code == 401

    def test_expired_challenge_returns_401(self, passkey_auth_client, mock_db):
        """期限切れ challenge は 401。"""
        mock_db.consume_challenge.return_value = None

        resp = passkey_auth_client.post("/auth/passkey/login/verify", json={
            "challenge_id": "expired",
            "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
        })

        assert resp.status_code == 401

    def test_sign_count_regression_returns_401(self, passkey_auth_client, mock_db):
        """sign_count 後退は 401（リプレイアタック防御）。update は呼ばない。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        mock_db.get_credential_by_id.return_value = self._make_credential(sign_count=10)
        mock_db.get_user.return_value = self._make_user()

        verified_auth = {"credential_id": "cred-b64url", "new_sign_count": 5}  # 後退
        with patch("api.routers.passkey.verify_authentication_response_wrapper",
                   return_value=verified_auth):
            with patch("api.routers.passkey.is_sign_count_valid", return_value=False):
                resp = passkey_auth_client.post("/auth/passkey/login/verify", json={
                    "challenge_id": "auth-chal-1",
                    "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
                })

        assert resp.status_code == 401
        mock_db.update_sign_count.assert_not_called()

    def test_verification_failure_returns_401(self, passkey_auth_client, mock_db):
        """webauthn 検証失敗は 401。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        mock_db.get_credential_by_id.return_value = self._make_credential()

        with patch("api.routers.passkey.verify_authentication_response_wrapper",
                   side_effect=Exception("bad sig")):
            resp = passkey_auth_client.post("/auth/passkey/login/verify", json={
                "challenge_id": "auth-chal-1",
                "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
            })

        assert resp.status_code == 401

    def test_audit_passkey_used_on_success(self, passkey_auth_client, mock_db, mock_audit):
        """成功時に passkey_used 監査ログが記録される。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        mock_db.get_credential_by_id.return_value = self._make_credential()
        mock_db.get_user.return_value = self._make_user()
        mock_db.create_session.return_value = None

        verified_auth = {"credential_id": "cred-b64url", "new_sign_count": 1}
        with patch("api.routers.passkey.verify_authentication_response_wrapper",
                   return_value=verified_auth):
            with patch("api.routers.passkey.is_sign_count_valid", return_value=True):
                passkey_auth_client.post("/auth/passkey/login/verify", json={
                    "challenge_id": "auth-chal-1",
                    "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
                })

        actions = [c[1].get("action") for c in mock_audit.record.call_args_list]
        assert "passkey_used" in actions

    def test_sign_count_updated_on_success(self, passkey_auth_client, mock_db):
        """成功時に sign_count が更新される。"""
        mock_db.consume_challenge.return_value = self._make_challenge()
        mock_db.get_credential_by_id.return_value = self._make_credential(sign_count=0)
        mock_db.get_user.return_value = self._make_user()
        mock_db.create_session.return_value = None

        verified_auth = {"credential_id": "cred-b64url", "new_sign_count": 1}
        with patch("api.routers.passkey.verify_authentication_response_wrapper",
                   return_value=verified_auth):
            with patch("api.routers.passkey.is_sign_count_valid", return_value=True):
                passkey_auth_client.post("/auth/passkey/login/verify", json={
                    "challenge_id": "auth-chal-1",
                    "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
                })

        mock_db.update_sign_count.assert_called_once()

    def test_registration_challenge_in_login_verify_returns_401(
        self, passkey_auth_client, mock_db
    ):
        """registration タイプの challenge を login/verify に渡すと 401（多層防御）。

        register/options で発行した challenge を login/verify に横流しする攻撃を防ぐ。
        webauthn 検証が成功するとしても challenge.type チェックで弾かれるべきである。
        """
        now = datetime.now(timezone.utc)
        wrong_type_challenge = WebAuthnChallenge(
            challenge_id="reg-chal-misuse",
            challenge="dGVzdA",
            user_id="user1",  # registration challenge は user_id を持つ
            type="registration",  # authentication ではなく registration
            expires_at=now + timedelta(minutes=5),
            created_at=now,
        )
        mock_db.consume_challenge.return_value = wrong_type_challenge
        mock_db.get_credential_by_id.return_value = self._make_credential()
        mock_db.get_user.return_value = self._make_user()
        mock_db.create_session.return_value = None

        # webauthn 検証が成功してもセッションは発行されない（type チェックが先）
        verified_auth = {"credential_id": "cred-b64url", "new_sign_count": 1}
        with patch("api.routers.passkey.verify_authentication_response_wrapper",
                   return_value=verified_auth):
            with patch("api.routers.passkey.is_sign_count_valid", return_value=True):
                resp = passkey_auth_client.post("/auth/passkey/login/verify", json={
                    "challenge_id": "reg-chal-misuse",
                    "credential": {"id": "c", "rawId": "c", "response": {}, "type": "public-key"},
                })

        assert resp.status_code == 401
        mock_db.update_sign_count.assert_not_called()


# ── T10: GET /auth/passkey/credentials ──────────────────────────────────────


class TestCredentialsList:
    def test_requires_auth(self, passkey_client):
        """未認証は 401。"""
        resp = passkey_client.get("/auth/passkey/credentials")
        assert resp.status_code == 401

    def test_returns_credential_list_without_public_key(self, passkey_auth_client, mock_db):
        """クレデンシャル一覧を返す。public_key は含まない。"""
        now = datetime.now(timezone.utc)
        creds = [
            WebAuthnCredential(
                credential_id="cred1", user_id="user1", username="testuser",
                public_key="SECRET", sign_count=5, transports=["internal"],
                name="Key1", created_at=now,
            ),
        ]
        mock_db.get_credentials_by_user.return_value = creds

        resp = passkey_auth_client.get("/auth/passkey/credentials")

        assert resp.status_code == 200
        body = resp.json()
        assert "credentials" in body
        assert len(body["credentials"]) == 1
        cred_resp = body["credentials"][0]
        assert "public_key" not in cred_resp
        assert cred_resp["credential_id"] == "cred1"

    def test_empty_list_when_no_credentials(self, passkey_auth_client, mock_db):
        """クレデンシャルがない場合は空リスト。"""
        mock_db.get_credentials_by_user.return_value = []

        resp = passkey_auth_client.get("/auth/passkey/credentials")

        assert resp.status_code == 200
        assert resp.json()["credentials"] == []


# ── T10: DELETE /auth/passkey/credentials/{credential_id} ───────────────────


class TestCredentialDelete:
    def test_requires_auth(self, passkey_client):
        """未認証は 401。"""
        resp = passkey_client.delete("/auth/passkey/credentials/cred-id")
        assert resp.status_code == 401

    def test_delete_own_credential_succeeds(self, passkey_auth_client, mock_db):
        """自分のクレデンシャルは削除できる。"""
        resp = passkey_auth_client.delete("/auth/passkey/credentials/cred-id")

        assert resp.status_code == 200
        mock_db.delete_credential.assert_called_once()

    def test_audit_passkey_removed_on_delete(self, passkey_auth_client, mock_db, mock_audit):
        """削除時に passkey_removed 監査ログが記録される。"""
        passkey_auth_client.delete("/auth/passkey/credentials/cred-id")

        actions = [c[1].get("action") for c in mock_audit.record.call_args_list]
        assert "passkey_removed" in actions

    def test_idempotent_delete(self, passkey_auth_client, mock_db):
        """存在しないクレデンシャルの削除も 200（冪等）。"""
        mock_db.delete_credential.return_value = None  # no-op

        resp = passkey_auth_client.delete("/auth/passkey/credentials/not-found")

        assert resp.status_code == 200
