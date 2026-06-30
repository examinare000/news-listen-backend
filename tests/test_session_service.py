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


# ── issue #84: セッションメタ情報（device_label / ip_hash / last_used_at） ──────


def test_issue_session_populates_metadata(user, mock_db, mock_response):
    """issue_session は ip_hash（生IPを保存しない）・device_label・last_used_at を埋める。"""
    from api.session_service import issue_session
    from shared.security import hash_token

    mock_request = MagicMock()
    mock_request.cookies = {}
    mock_request.headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0"}

    with patch.dict("os.environ", {"SESSION_COOKIE_SECURE": "false"}):
        _, session = issue_session(mock_db, user, mock_request, mock_response, "203.0.113.7")

    created = mock_db.create_session.call_args[0][0]
    # 生IPは保存せずハッシュで保持する
    assert created.ip_hash == hash_token("203.0.113.7")
    assert "203.0.113.7" not in (created.ip_hash or "")
    # User-Agent からデバイス名を導出している
    assert created.device_label == "Chrome on macOS"
    # 作成時は last_used_at が created_at と同値
    assert created.last_used_at == created.created_at


# ── issue #84: User-Agent からのデバイス名導出 ───────────────────────────────


@pytest.mark.parametrize(
    "ua,expected",
    [
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari", "Safari on iOS"),
        ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Safari", "Safari on iPadOS"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0", "Chrome on macOS"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/121.0", "Firefox on Windows"),
        ("Mozilla/5.0 (Linux; Android 14) Chrome/120.0 Mobile", "Chrome on Android"),
        ("Mozilla/5.0 (Macintosh) Version/17.0 Safari/605.1", "Safari on macOS"),
        ("Mozilla/5.0 (Windows NT 10.0) Edg/120.0", "Edge on Windows"),
        ("NewsListen-iOS/1.0", "Unknown device"),
        ("", None),
        (None, None),
    ],
)
def test_device_label_from_user_agent(ua, expected):
    from api.session_service import device_label_from_user_agent

    assert device_label_from_user_agent(ua) == expected
