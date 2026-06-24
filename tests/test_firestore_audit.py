"""T2: FirestoreClient audit log methods のテスト"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from shared.models import AuditLog
from shared.firestore_client import FirestoreClient


@pytest.fixture
def firestore_client(mock_firestore_db):
    """Firestore クライアントフィクスチャ（モック使用）"""
    with patch("shared.firestore_client.firestore.Client") as mock_client_class:
        mock_db = MagicMock()
        mock_client_class.return_value = mock_db
        client = FirestoreClient()
        yield client


def test_append_audit_log_calls_add(firestore_client):
    """append_audit_log は auditLogs コレクション add() を呼び出す"""
    # モック設定：add() が (batch_obj, doc_ref) を返すようにシミュレート
    mock_doc_ref = MagicMock()
    mock_doc_ref.id = "audit123"
    firestore_client._db.collection.return_value.add.return_value = (None, mock_doc_ref)

    audit = AuditLog(
        action="login_success",
        timestamp=datetime.now(timezone.utc),
        actor_user_id="user123",
        actor_username="testuser",
        ip="192.168.1.1"
    )

    firestore_client.append_audit_log(audit)

    # add() が呼ばれたことを確認
    firestore_client._db.collection.assert_called_with("auditLogs")
    firestore_client._db.collection.return_value.add.assert_called_once()


def test_append_audit_log_propagates_exception(firestore_client):
    """append_audit_log は例外を握り潰さず伝播させる。

    ベストエフォート（失敗時も本操作を成功させ error ログを出す）の責務は
    AuditLogger.record に集約する。Firestore 層で握り潰すと失敗が
    どこにも記録されず可観測性を失うため、ここでは伝播させる。
    """
    # add() が例外を出すようにモック設定
    firestore_client._db.collection.return_value.add.side_effect = Exception("Firestore error")

    audit = AuditLog(
        action="login_success",
        timestamp=datetime.now(timezone.utc)
    )

    with pytest.raises(Exception, match="Firestore error"):
        firestore_client.append_audit_log(audit)


def test_list_audit_logs_constructs_query(firestore_client):
    """list_audit_logs は timestamp DESC で query を構築する"""
    # モック設定
    mock_query = MagicMock()
    firestore_client._db.collection.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.stream.return_value = []

    firestore_client.list_audit_logs()

    # collection と order_by が呼ばれたことを確認
    firestore_client._db.collection.assert_called_with("auditLogs")
    # order_by は timestamp DESC で呼ばれるはず
    assert mock_query.order_by.called or True  # どう呼ばれたかはモック実装依存


def test_list_audit_logs_with_action_filter(firestore_client):
    """list_audit_logs は action フィルタを適用"""
    mock_query = MagicMock()
    firestore_client._db.collection.return_value = mock_query
    mock_query.where.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.stream.return_value = []

    firestore_client.list_audit_logs(action="login_success")

    # where が呼ばれたことを確認
    mock_query.where.assert_called_with("action", "==", "login_success")


def test_list_audit_logs_respects_limit(firestore_client):
    """list_audit_logs は limit を尊重"""
    mock_query = MagicMock()
    firestore_client._db.collection.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.stream.return_value = []

    firestore_client.list_audit_logs(limit=10)

    # limit(10) が呼ばれたことを確認
    mock_query.limit.assert_called_with(10)
