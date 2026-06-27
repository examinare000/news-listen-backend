"""T5: session_service.issue_session の単体テスト。

auth.py の login からセッション発行ロジックを抽出した issue_session を検証する。
監査ログ呼び出しは issue_session に含まない（呼び出し側の責務）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from shared.models import Session, User


@pytest.fixture
def user():
    now = datetime.now(timezone.utc)
    return User(
        username="testuser",
        user_id="uid-123",
        password_hash="$2b$12$hashed",
        role="user",
        display_name="Test User",
        email=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.cookies = {}
    req.headers = {}
    return req


@pytest.fixture
def mock_response():
    return MagicMock()


def test_issue_session_creates_session_in_db(user, mock_db, mock_request, mock_response):
    """issue_session は db.create_session を呼ぶ。"""
    from api.session_service import issue_session

    with patch.dict("os.environ", {"SESSION_COOKIE_SECURE": "false", "SESSION_TTL_HOURS": "168"}):
        token, session = issue_session(mock_db, user, mock_request, mock_response, "127.0.0.1")

    mock_db.create_session.assert_called_once()
    created = mock_db.create_session.call_args[0][0]
    assert isinstance(created, Session)
    assert created.user_id == user.user_id
    assert created.username == user.username
    assert created.role == user.role


def test_issue_session_returns_token_and_session(user, mock_db, mock_request, mock_response):
    """issue_session は (str token, Session) を返す。"""
    from api.session_service import issue_session

    with patch.dict("os.environ", {"SESSION_COOKIE_SECURE": "false"}):
        result = issue_session(mock_db, user, mock_request, mock_response, "127.0.0.1")

    token, session = result
    assert isinstance(token, str)
    assert len(token) > 0
    assert isinstance(session, Session)
    assert session.user_id == user.user_id


def test_issue_session_sets_nl_session_cookie(user, mock_db, mock_request, mock_response):
    """issue_session は nl_session Cookie を response にセットする。"""
    from api.session_service import issue_session

    with patch.dict("os.environ", {"SESSION_COOKIE_SECURE": "false"}):
        token, _ = issue_session(mock_db, user, mock_request, mock_response, "127.0.0.1")

    # set_cookie が少なくとも nl_session で呼ばれていること（keyword argument "key" で確認）
    kw_keys = [call[1].get("key") for call in mock_response.set_cookie.call_args_list if "key" in call[1]]
    assert "nl_session" in kw_keys, f"nl_session not set, cookies: {mock_response.set_cookie.call_args_list}"


def test_issue_session_sets_csrf_cookie(user, mock_db, mock_request, mock_response):
    """issue_session は csrf_token Cookie も response にセットする。"""
    from api.session_service import issue_session

    with patch.dict("os.environ", {"SESSION_COOKIE_SECURE": "false"}):
        issue_session(mock_db, user, mock_request, mock_response, "127.0.0.1")

    kw_keys = [call[1].get("key") for call in mock_response.set_cookie.call_args_list if "key" in call[1]]
    assert "csrf_token" in kw_keys, f"csrf_token not set, cookies: {mock_response.set_cookie.call_args_list}"


def test_issue_session_rotates_old_session(user, mock_db, mock_response):
    """既存トークンがある場合、旧セッションを削除してから新規発行する。"""
    from api.session_service import issue_session

    # Request に既存 Bearer トークンを含める
    old_token = "old_token_value"
    mock_request = MagicMock()
    mock_request.cookies = {}
    mock_request.headers = {"Authorization": f"Bearer {old_token}"}

    with patch.dict("os.environ", {"SESSION_COOKIE_SECURE": "false"}):
        issue_session(mock_db, user, mock_request, mock_response, "127.0.0.1")

    # 旧セッション削除が呼ばれたこと
    mock_db.delete_session.assert_called_once()


def test_issue_session_no_old_token_no_delete(user, mock_db, mock_response):
    """既存トークンがない場合、セッション削除は呼ばない。"""
    from api.session_service import issue_session

    mock_request = MagicMock()
    mock_request.cookies = {}
    mock_request.headers = {}

    with patch.dict("os.environ", {"SESSION_COOKIE_SECURE": "false"}):
        issue_session(mock_db, user, mock_request, mock_response, "127.0.0.1")

    mock_db.delete_session.assert_not_called()
