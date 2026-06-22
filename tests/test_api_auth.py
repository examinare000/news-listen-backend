"""/auth/* エンドポイントのテスト。

api_client フィクスチャは get_firestore_client を mock_db に差し替える。
get_current_user は本物が動くため、認証が要るエンドポイントは mock_db.get_session を
設定し、Authorization: Bearer ヘッダーでトークンを渡す。
"""
from datetime import datetime, timedelta, timezone

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
