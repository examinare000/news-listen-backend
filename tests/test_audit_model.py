"""T1: AuditLog モデルのテスト"""
from datetime import datetime
from shared.models import AuditLog

def test_audit_log_model_required_fields():
    """AuditLog は action / timestamp / ip を必須フィールドとして持つ"""
    audit = AuditLog(
        action="login_success",
        timestamp=datetime.now(),
        actor_user_id="user123",
        actor_username="testuser",
        target_username=None,
        ip="192.168.1.1",
        details=None
    )
    assert audit.action == "login_success"
    assert audit.actor_user_id == "user123"
    assert audit.actor_username == "testuser"
    assert audit.ip == "192.168.1.1"

def test_audit_log_action_literal():
    """AuditLog.action は定義された Literal 値のみ受け入れる"""
    valid_actions = [
        "login_success", "login_failure", "logout", "login_lockout",
        "user_create", "user_update", "user_role_change", "user_password_reset",
        "user_delete", "session_revoke", "article_star", "article_dismiss", "article_mark_read",
        "rss_source_add", "rss_source_remove", "preferences_update", "onboarding_complete",
        "generation_limit_reached"
    ]
    for action in valid_actions:
        audit = AuditLog(
            action=action,
            timestamp=datetime.now(),
            actor_user_id=None,
            actor_username=None,
            target_username=None,
            ip=None,
            details=None
        )
        assert audit.action == action

def test_audit_log_optional_fields():
    """AuditLog の actor_user_id / actor_username / target_username / ip / details は optional"""
    audit = AuditLog(
        action="login_success",
        timestamp=datetime.now()
    )
    assert audit.actor_user_id is None
    assert audit.actor_username is None
    assert audit.target_username is None
    assert audit.ip is None
    assert audit.details is None
