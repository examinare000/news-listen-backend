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
            json={"username": "Carol", "password": "Str0ng-Pass!23", "role": "user"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        saved = mock_db.save_user.call_args[0][0]
        assert saved.username == "carol"  # 正規化される
        assert saved.user_id  # 採番される
        assert verify_password("Str0ng-Pass!23", saved.password_hash)
        assert "Str0ng-Pass!23" not in resp.text

    def test_weak_password_422_does_not_echo_plaintext(self, api_client, mock_db):
        """強度検証 422 のレスポンスに送信した平文パスワードが含まれないこと。

        FastAPI 既定の検証エラーは input（送信値）を本文に載せるため、
        機微フィールドは伏せる必要がある（資格情報の漏洩防止）。
        """
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = None
        resp = api_client.post(
            "/admin/users",
            json={"username": "dave", "password": "weakpw-secret", "role": "user"},
            headers=AUTH,
        )
        assert resp.status_code == 422
        assert "weakpw-secret" not in resp.text
        mock_db.save_user.assert_not_called()

    def test_duplicate_returns_409(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("carol")
        resp = api_client.post(
            "/admin/users",
            json={"username": "carol", "password": "Str0ng-Pass!23"},
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
            json={"role": "admin", "new_password": "Str0ng-Pass!23"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        saved = mock_db.save_user.call_args[0][0]
        assert saved.role == "admin"
        assert verify_password("Str0ng-Pass!23", saved.password_hash)

    def test_password_reset_revokes_sessions(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob", role="user")
        resp = api_client.patch(
            "/admin/users/bob", json={"new_password": "Str0ng-Pass!23"}, headers=AUTH
        )
        assert resp.status_code == 200
        # 旧資格情報での継続アクセスを断つためセッションを失効させる。
        mock_db.delete_sessions_for_user.assert_called_once_with("uid-bob")

    def test_cannot_demote_last_admin(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob", role="admin")
        mock_db.list_users.return_value = [_user("bob", role="admin")]  # 唯一の admin
        resp = api_client.patch("/admin/users/bob", json={"role": "user"}, headers=AUTH)
        assert resp.status_code == 409
        mock_db.save_user.assert_not_called()

    def test_demote_admin_when_other_admin_exists_revokes_sessions(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob", role="admin")
        mock_db.list_users.return_value = [_user("bob", role="admin"), _user("alice", role="admin")]
        resp = api_client.patch("/admin/users/bob", json={"role": "user"}, headers=AUTH)
        assert resp.status_code == 200
        mock_db.delete_sessions_for_user.assert_called_once_with("uid-bob")

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
        # 削除済みユーザーが TTL 満了まで叩けないようセッションも失効。
        mock_db.delete_sessions_for_user.assert_called_once_with("uid-bob")

    def test_cannot_delete_last_admin(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob", role="admin")
        mock_db.list_users.return_value = [_user("bob", role="admin")]  # 唯一の admin
        resp = api_client.delete("/admin/users/bob", headers=AUTH)
        assert resp.status_code == 409
        mock_db.delete_user.assert_not_called()

    def test_delete_admin_allowed_when_other_admin_exists(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob", role="admin")
        mock_db.list_users.return_value = [_user("bob", role="admin"), _user("alice", role="admin")]
        resp = api_client.delete("/admin/users/bob", headers=AUTH)
        assert resp.status_code == 200
        mock_db.delete_user.assert_called_once_with("bob")

    def test_missing_user_returns_404(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = None
        resp = api_client.delete("/admin/users/ghost", headers=AUTH)
        assert resp.status_code == 404


class TestAuditInstrumentation:
    """admin ルーターが AuditLogger.record を適切に呼ぶことを検証する（計装）。"""

    def _actions(self, mock_audit) -> list[str]:
        return [c.kwargs["action"] for c in mock_audit.record.call_args_list]

    def test_create_user_records_audit(self, api_client, mock_db, mock_audit):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = None
        api_client.post(
            "/admin/users",
            json={"username": "carol", "password": "Str0ng-Pass!23", "role": "user"},
            headers=AUTH,
        )
        assert "user_create" in self._actions(mock_audit)
        call = mock_audit.record.call_args_list[0]
        assert call.kwargs["target_username"] == "carol"
        assert call.kwargs["actor"].username == "admin"

    def test_role_change_records_audit(self, api_client, mock_db, mock_audit):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob", role="user")
        api_client.patch("/admin/users/bob", json={"role": "admin"}, headers=AUTH)
        assert "user_role_change" in self._actions(mock_audit)

    def test_password_reset_records_audit_and_session_revoke(
        self, api_client, mock_db, mock_audit
    ):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob", role="user")
        api_client.patch(
            "/admin/users/bob", json={"new_password": "Str0ng-Pass!23"}, headers=AUTH
        )
        actions = self._actions(mock_audit)
        assert "user_password_reset" in actions
        assert "session_revoke" in actions

    def test_display_name_only_records_user_update(
        self, api_client, mock_db, mock_audit
    ):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob", role="user")
        api_client.patch(
            "/admin/users/bob", json={"display_name": "Bobby"}, headers=AUTH
        )
        assert self._actions(mock_audit) == ["user_update"]

    def test_delete_user_records_audit(self, api_client, mock_db, mock_audit):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = _user("bob")
        api_client.delete("/admin/users/bob", headers=AUTH)
        delete_call = next(
            c for c in mock_audit.record.call_args_list
            if c.kwargs["action"] == "user_delete"
        )
        assert delete_call.kwargs["target_username"] == "bob"

    def test_create_audit_does_not_contain_plaintext_password(
        self, api_client, mock_db, mock_audit
    ):
        mock_db.get_session.return_value = _session("admin")
        mock_db.get_user.return_value = None
        api_client.post(
            "/admin/users",
            json={"username": "carol", "password": "Str0ng-Pass!23", "role": "user"},
            headers=AUTH,
        )
        assert "Str0ng-Pass!23" not in str(mock_audit.record.call_args_list)
