"""T5: GET /admin/audit-logs API エンドポイントのテスト"""
from datetime import datetime, timedelta, timezone

from shared.models import Session, AuditLog

API_HEADERS = {"X-API-Key": "test-key"}
AUTH = {**API_HEADERS, "Authorization": "Bearer raw"}


def _admin_session() -> Session:
    """admin ロールのセッション"""
    return Session(
        session_id="hashed",
        user_id="admin-id",
        username="admin",
        role="admin",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _user_session() -> Session:
    """user ロールのセッション"""
    return Session(
        session_id="hashed",
        user_id="user-id",
        username="testuser",
        role="user",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def test_audit_logs_endpoint_requires_admin(api_client, mock_db):
    """GET /admin/audit-logs は admin ロールを要求する"""
    # セッションなし（未認証）でアクセス
    mock_db.get_session.return_value = None
    response = api_client.get("/admin/audit-logs", headers=API_HEADERS)
    assert response.status_code == 401


def test_audit_logs_endpoint_admin_only(api_client, mock_db):
    """GET /admin/audit-logs は admin ロールのセッションが必要"""
    # user ロールのセッションを設定
    mock_db.get_session.return_value = _user_session()
    response = api_client.get("/admin/audit-logs", headers=AUTH)
    assert response.status_code == 403


def test_audit_logs_endpoint_returns_logs(api_client, mock_db):
    """GET /admin/audit-logs は監査ログ一覧を返す"""
    # admin セッション
    mock_db.get_session.return_value = _admin_session()

    # 監査ログをモック
    mock_logs = [
        AuditLog(
            action="login_success",
            timestamp=datetime.now(timezone.utc),
            actor_username="testuser",
            ip="192.168.1.1",
        ),
        AuditLog(
            action="user_create",
            timestamp=datetime.now(timezone.utc),
            actor_username="admin",
            target_username="newuser",
        ),
    ]
    mock_db.list_audit_logs.return_value = mock_logs

    response = api_client.get("/admin/audit-logs", headers=AUTH)

    assert response.status_code == 200
    data = response.json()
    assert "logs" in data
    assert len(data["logs"]) == 2
    assert data["logs"][0]["action"] == "login_success"
    assert data["logs"][0]["actor_username"] == "testuser"
    assert data["logs"][0]["ip"] == "192.168.1.1"
    # actor_user_id は返さない（セキュリティ）
    assert "actor_user_id" not in data["logs"][0]
    # password_hash 等の機密情報が含まれていないことを確認
    assert "password_hash" not in str(data)


def test_audit_logs_endpoint_supports_action_filter(api_client, mock_db):
    """GET /admin/audit-logs は action クエリパラメータでフィルタ可能"""
    mock_db.get_session.return_value = _admin_session()
    mock_db.list_audit_logs.return_value = []

    response = api_client.get("/admin/audit-logs?action=login_success", headers=AUTH)

    assert response.status_code == 200
    # list_audit_logs が action パラメータで呼ばれたことを確認
    mock_db.list_audit_logs.assert_called_with(action="login_success", limit=50)


def test_audit_logs_endpoint_supports_limit(api_client, mock_db):
    """GET /admin/audit-logs は limit クエリパラメータをサポート"""
    mock_db.get_session.return_value = _admin_session()
    mock_db.list_audit_logs.return_value = []

    response = api_client.get("/admin/audit-logs?limit=100", headers=AUTH)

    assert response.status_code == 200
    # list_audit_logs が limit パラメータで呼ばれたことを確認
    mock_db.list_audit_logs.assert_called_with(action=None, limit=100)
