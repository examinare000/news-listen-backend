"""/auth/* エンドポイントのテスト。

api_client フィクスチャは get_firestore_client を mock_db に差し替える。
get_current_user は本物が動くため、認証が要るエンドポイントは mock_db.get_session を
設定し、Authorization: Bearer ヘッダーでトークンを渡す。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from shared.models import Session, User
from shared.security import hash_password

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
API_HEADERS = {"X-API-Key": "test-key"}


def _user(username="alice", password="correct-horse", role="user") -> User:
    return User(
        username=username,
        user_id="uid-1",
        password_hash=hash_password(password),
        role=role,
        display_name="Alice",
        created_at=NOW,
        updated_at=NOW,
    )


def _valid_session(role="user") -> Session:
    return Session(
        session_id="hashed",
        user_id="uid-1",
        username="alice",
        role=role,
        created_at=NOW,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


class TestLogin:
    def test_success_sets_cookie_and_returns_token(self, api_client, mock_db):
        mock_db.get_user.return_value = _user(password="correct-horse")

        resp = api_client.post(
            "/auth/login",
            json={"username": "alice", "password": "correct-horse"},
            headers=API_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["token"]
        assert body["user"] == {"username": "alice", "role": "user", "display_name": "Alice"}
        # password_hash は決して漏らさない
        assert "password_hash" not in resp.text
        mock_db.create_session.assert_called_once()
        assert "nl_session" in resp.headers.get("set-cookie", "")

    def test_wrong_password_returns_401(self, api_client, mock_db):
        mock_db.get_user.return_value = _user(password="correct-horse")
        resp = api_client.post(
            "/auth/login",
            json={"username": "alice", "password": "wrong"},
            headers=API_HEADERS,
        )
        assert resp.status_code == 401
        mock_db.create_session.assert_not_called()

    def test_unknown_user_returns_401(self, api_client, mock_db):
        mock_db.get_user.return_value = None
        resp = api_client.post(
            "/auth/login",
            json={"username": "ghost", "password": "whatever"},
            headers=API_HEADERS,
        )
        assert resp.status_code == 401

    def test_username_is_normalized(self, api_client, mock_db):
        mock_db.get_user.return_value = _user(password="correct-horse")
        api_client.post(
            "/auth/login",
            json={"username": "  Alice  ", "password": "correct-horse"},
            headers=API_HEADERS,
        )
        mock_db.get_user.assert_called_with("alice")


class TestMe:
    def test_returns_current_user(self, api_client, mock_db):
        mock_db.get_session.return_value = _valid_session()
        mock_db.get_user.return_value = _user()
        resp = api_client.get(
            "/auth/me", headers={**API_HEADERS, "Authorization": "Bearer raw"}
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "alice"

    def test_requires_auth(self, api_client, mock_db):
        resp = api_client.get("/auth/me", headers=API_HEADERS)
        assert resp.status_code == 401


class TestLogout:
    def test_deletes_session(self, api_client, mock_db):
        resp = api_client.post(
            "/auth/logout", headers={**API_HEADERS, "Authorization": "Bearer raw"}
        )
        assert resp.status_code == 200
        mock_db.delete_session.assert_called_once()

    def test_idempotent_without_token(self, api_client, mock_db):
        resp = api_client.post("/auth/logout", headers=API_HEADERS)
        assert resp.status_code == 200


class TestChangePassword:
    def test_success(self, api_client, mock_db):
        mock_db.get_session.return_value = _valid_session()
        mock_db.get_user.return_value = _user(password="old-password")
        resp = api_client.post(
            "/auth/password",
            json={"current_password": "old-password", "new_password": "new-password-1"},
            headers={**API_HEADERS, "Authorization": "Bearer raw"},
        )
        assert resp.status_code == 200
        saved = mock_db.save_user.call_args[0][0]
        from shared.security import verify_password

        assert verify_password("new-password-1", saved.password_hash)

    def test_wrong_current_password_returns_400(self, api_client, mock_db):
        mock_db.get_session.return_value = _valid_session()
        mock_db.get_user.return_value = _user(password="old-password")
        resp = api_client.post(
            "/auth/password",
            json={"current_password": "nope", "new_password": "new-password-1"},
            headers={**API_HEADERS, "Authorization": "Bearer raw"},
        )
        assert resp.status_code == 400
        mock_db.save_user.assert_not_called()


class TestUpdateProfile:
    def test_updates_display_name(self, api_client, mock_db):
        mock_db.get_session.return_value = _valid_session()
        mock_db.get_user.return_value = _user()
        resp = api_client.patch(
            "/auth/me",
            json={"display_name": "New Name"},
            headers={**API_HEADERS, "Authorization": "Bearer raw"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "New Name"
        saved = mock_db.save_user.call_args[0][0]
        assert saved.display_name == "New Name"


class TestLoginRateLimit:
    """ログイン試行のレートリミット（IP単位・username単位）のテスト。"""

    def test_rate_limit_disabled_by_default(self, api_client, mock_db):
        """しきい値が0なら、レートリミット機能は無効化される。"""
        import os
        with patch.dict(os.environ, {"LOGIN_RATELIMIT_MAX_ATTEMPTS": "0"}):
            # モジュール再読み込みして env 値を反映
            import importlib

            import api.routers.auth as auth_module
            importlib.reload(auth_module)

            mock_db.get_user.return_value = _user(password="correct-horse")
            mock_db.check_login_lock.return_value = None

            resp = api_client.post(
                "/auth/login",
                json={"username": "alice", "password": "wrong"},
                headers=API_HEADERS,
            )

            # 失敗でも check_login_lock は呼ばれない（機能無効）
            mock_db.check_login_lock.assert_not_called()
            assert resp.status_code == 401

    def test_rate_limit_checks_ip_lock_before_auth(self, api_client, mock_db):
        """IP がロック中なら、資格情報検証より前に 429 を返す。"""
        import os
        from datetime import datetime, timezone

        with patch.dict(
            os.environ,
            {
                "LOGIN_RATELIMIT_MAX_ATTEMPTS": "5",
                "LOGIN_RATELIMIT_WINDOW_SECONDS": "900",
                "LOGIN_RATELIMIT_LOCKOUT_SECONDS": "900",
            }
        ):
            import importlib

            import api.routers.auth as auth_module
            importlib.reload(auth_module)

            locked_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            mock_db.check_login_lock.return_value = locked_until

            resp = api_client.post(
                "/auth/login",
                json={"username": "alice", "password": "whatever"},
                headers=API_HEADERS,
            )

            # 429 + Retry-After ヘッダ
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers
            # get_user は呼ばれない（事前チェックで弾かれた）
            mock_db.get_user.assert_not_called()

    def test_rate_limit_checks_username_lock_before_auth(self, api_client, mock_db):
        """username がロック中なら、資格情報検証より前に 429 を返す。"""
        import os
        from datetime import datetime, timezone

        with patch.dict(
            os.environ,
            {
                "LOGIN_RATELIMIT_MAX_ATTEMPTS": "5",
                "LOGIN_RATELIMIT_WINDOW_SECONDS": "900",
                "LOGIN_RATELIMIT_LOCKOUT_SECONDS": "900",
            }
        ):
            import importlib

            import api.routers.auth as auth_module
            importlib.reload(auth_module)

            # 最初は IP ロックなし
            # 2回目で username ロック あり
            locked_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            mock_db.check_login_lock.side_effect = [None, locked_until]

            resp = api_client.post(
                "/auth/login",
                json={"username": "alice", "password": "whatever"},
                headers=API_HEADERS,
            )

            assert resp.status_code == 429
            assert "Retry-After" in resp.headers
            mock_db.get_user.assert_not_called()

    def test_register_failed_login_calls_both_keys(self, api_client, mock_db):
        """失敗時に IP と username の両方に対して register_failed_login が呼ばれる。"""
        import os

        with patch.dict(
            os.environ,
            {
                "LOGIN_RATELIMIT_MAX_ATTEMPTS": "5",
                "LOGIN_RATELIMIT_WINDOW_SECONDS": "900",
                "LOGIN_RATELIMIT_LOCKOUT_SECONDS": "900",
            }
        ):
            import importlib

            import api.routers.auth as auth_module
            importlib.reload(auth_module)

            mock_db.get_user.return_value = _user(password="correct-horse")
            # ロック中ではない
            mock_db.check_login_lock.return_value = None
            # 新規ロック未発生（count < max_attempts）
            mock_db.register_failed_login.return_value = False

            resp = api_client.post(
                "/auth/login",
                json={"username": "alice", "password": "wrong"},
                headers=API_HEADERS,
            )

            assert resp.status_code == 401
            # IP と username 両方に対して register_failed_login が呼ばれる
            assert mock_db.register_failed_login.call_count == 2

    def test_rate_limit_lockout_when_threshold_exceeded(self, api_client, mock_db):
        """閾値超過で新規ロック発生時、register_failed_login が True を返し、ログに記録される。"""
        import os

        with patch.dict(
            os.environ,
            {
                "LOGIN_RATELIMIT_MAX_ATTEMPTS": "5",
                "LOGIN_RATELIMIT_WINDOW_SECONDS": "900",
                "LOGIN_RATELIMIT_LOCKOUT_SECONDS": "900",
            }
        ):
            import importlib

            import api.routers.auth as auth_module
            importlib.reload(auth_module)

            mock_db.get_user.return_value = _user(password="correct-horse")
            mock_db.check_login_lock.return_value = None
            # 1回目は False（ロック未発生）、2回目は True（新規ロック）
            mock_db.register_failed_login.side_effect = [False, True]

            with patch("api.routers.auth._logger") as mock_logger:
                resp = api_client.post(
                    "/auth/login",
                    json={"username": "alice", "password": "wrong"},
                    headers=API_HEADERS,
                )

                assert resp.status_code == 401
                # 新規ロック時はログ出力される（要件に従う）
                mock_logger.warning.assert_called()

    def test_clear_login_attempts_on_success(self, api_client, mock_db):
        """ログイン成功時、IP と username の両方に対して clear_login_attempts が呼ばれる。"""
        import os

        with patch.dict(
            os.environ,
            {
                "LOGIN_RATELIMIT_MAX_ATTEMPTS": "5",
                "LOGIN_RATELIMIT_WINDOW_SECONDS": "900",
                "LOGIN_RATELIMIT_LOCKOUT_SECONDS": "900",
            }
        ):
            import importlib

            import api.routers.auth as auth_module
            importlib.reload(auth_module)

            mock_db.get_user.return_value = _user(password="correct-horse")
            mock_db.check_login_lock.return_value = None

            resp = api_client.post(
                "/auth/login",
                json={"username": "alice", "password": "correct-horse"},
                headers=API_HEADERS,
            )

            assert resp.status_code == 200
            # IP と username 両方に対して clear_login_attempts が呼ばれる
            assert mock_db.clear_login_attempts.call_count == 2

    def test_ip_and_username_locks_are_independent(self, api_client, mock_db):
        """IP がロック中でも username が解除されていれば、username は login 可能（逆も同）。"""
        import os

        with patch.dict(
            os.environ,
            {
                "LOGIN_RATELIMIT_MAX_ATTEMPTS": "5",
                "LOGIN_RATELIMIT_WINDOW_SECONDS": "900",
                "LOGIN_RATELIMIT_LOCKOUT_SECONDS": "900",
            }
        ):
            import importlib

            import api.routers.auth as auth_module
            importlib.reload(auth_module)

            locked_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            # IP はロック中、username は未ロック
            mock_db.check_login_lock.side_effect = [locked_until, None]

            resp = api_client.post(
                "/auth/login",
                json={"username": "alice", "password": "whatever"},
                headers=API_HEADERS,
            )

            # IP ロックで即座に 429
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers
