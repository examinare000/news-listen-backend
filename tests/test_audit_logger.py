"""T3: AuditLogger のテスト"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from shared.models import Session
from api.audit import AuditLogger


@pytest.fixture
def mock_clock():
    """時刻を注入可能な clock フィクスチャ"""
    def get_now():
        return datetime(2026, 6, 24, 10, 0, 0, tzinfo=timezone.utc)
    return get_now


@pytest.fixture
def mock_firestore_client():
    """FirestoreClient のモック"""
    return MagicMock()


@pytest.fixture
def audit_logger(mock_firestore_client, mock_clock):
    """AuditLogger フィクスチャ"""
    return AuditLogger(firestore_client=mock_firestore_client, clock=mock_clock)


def test_audit_logger_record_success(audit_logger, mock_firestore_client):
    """record は append_audit_log を呼び出す"""
    actor = Session(
        session_id="sess123",
        user_id="user1",
        username="testuser",
        role="admin",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc)
    )
    
    audit_logger.record(
        action="login_success",
        actor=actor,
        target_username=None,
        ip="192.168.1.1"
    )
    
    # append_audit_log が呼ばれたことを確認
    assert mock_firestore_client.append_audit_log.called


def test_audit_logger_record_with_details(audit_logger, mock_firestore_client):
    """record は details を含められる"""
    actor = Session(
        session_id="sess123",
        user_id="user1",
        username="testuser",
        role="admin",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc)
    )
    
    audit_logger.record(
        action="user_update",
        actor=actor,
        target_username="targetuser",
        ip="192.168.1.1",
        details={"field": "role", "old_value": "user", "new_value": "admin"}
    )
    
    # append_audit_log が呼ばれ、audit に details が含まれることを確認
    call_args = mock_firestore_client.append_audit_log.call_args
    audit_arg = call_args[0][0]
    assert audit_arg.details is not None


def test_audit_logger_record_handles_exception(audit_logger, mock_firestore_client):
    """record が append_audit_log 例外を吸収する（ベストエフォート）"""
    # append_audit_log が例外を投げるようにモック設定
    mock_firestore_client.append_audit_log.side_effect = Exception("Firestore error")
    
    actor = Session(
        session_id="sess123",
        user_id="user1",
        username="testuser",
        role="admin",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc)
    )
    
    # record は例外を投げずに成功する
    audit_logger.record(
        action="login_success",
        actor=actor,
        ip="192.168.1.1"
    )
    # 例外なく実行完了


def test_audit_logger_record_logs_error_on_failure(audit_logger, mock_firestore_client):
    """record が append_audit_log 失敗時に error ログを出す"""
    mock_firestore_client.append_audit_log.side_effect = Exception("Firestore error")
    
    actor = Session(
        session_id="sess123",
        user_id="user1",
        username="testuser",
        role="admin",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc)
    )
    
    with patch("api.audit.logger") as mock_logger:
        audit_logger.record(
            action="login_success",
            actor=actor,
            ip="192.168.1.1"
        )
        # error ログが呼ばれたことを確認
        assert mock_logger.error.called
