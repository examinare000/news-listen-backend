"""/admin/users CRUD のテスト（admin ロール必須）。"""
from datetime import datetime, timedelta, timezone

from shared.models import Session, User
from shared.security import hash_password, verify_password

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
API_HEADERS = {"X-API-Key": "test-key"}
AUTH = {**API_HEADERS, "Authorization": "Bearer raw"}


def _user(username="bob", role="user") -> User:
    return User(
        username=username,
        user_id="uid-bob",
        password_hash=hash_password("pw"),
        role=role,
        display_name="Bob",
        created_at=NOW,
        updated_at=NOW,
    )


def _session(role) -> Session:
    return Session(
        session_id="hashed",
        user_id="uid-admin",
        username="admin",
        role=role,
        created_at=NOW,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


class TestAuthorization:
    def test_non_admin_gets_403(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("user")
        resp = api_client.get("/admin/users", headers=AUTH)
        assert resp.status_code == 403

    def test_unauthenticated_gets_401(self, api_client, mock_db):
        resp = api_client.get("/admin/users", headers=API_HEADERS)
        assert resp.status_code == 401


class TestListUsers:
    def test_lists_users(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.list_users.return_value = [_user("alice"), _user("bob")]
        resp = api_client.get("/admin/users", headers=AUTH)
        assert resp.status_code == 200
        assert [u["username"] for u in resp.json()["users"]] == ["alice", "bob"]
        assert "password_hash" not in resp.text


class TestCreateUser:
    def test_creates_user(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = None
        resp = api_client.post(
            "/admin/users",
            json={"username": "Carol", "password": "carol-pass", "role": "user"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        saved = mock_db.save_user.call_args[0][0]
        assert saved.username == "carol"  # 正規化される
        assert saved.user_id  # 採番される
        assert verify_password("carol-pass", saved.password_hash)
        assert "carol-pass" not in resp.text

    def test_duplicate_returns_409(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("carol")
        resp = api_client.post(
            "/admin/users",
            json={"username": "carol", "password": "carol-pass"},
            headers=AUTH,
        )
        assert resp.status_code == 409
        mock_db.save_user.assert_not_called()


class TestUpdateUser:
    def test_updates_role_and_password(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob", role="user")
        resp = api_client.patch(
            "/admin/users/bob",
            json={"role": "admin", "new_password": "reset-pass"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        saved = mock_db.save_user.call_args[0][0]
        assert saved.role == "admin"
        assert verify_password("reset-pass", saved.password_hash)

    def test_missing_user_returns_404(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = None
        resp = api_client.patch(
            "/admin/users/ghost", json={"role": "admin"}, headers=AUTH
        )
        assert resp.status_code == 404


class TestDeleteUser:
    def test_deletes_user(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob")
        resp = api_client.delete("/admin/users/bob", headers=AUTH)
        assert resp.status_code == 200
        mock_db.delete_user.assert_called_once_with("bob")

    def test_missing_user_returns_404(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = None
        resp = api_client.delete("/admin/users/ghost", headers=AUTH)
        assert resp.status_code == 404
