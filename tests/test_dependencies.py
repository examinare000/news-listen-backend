"""api.dependencies のユニットテスト。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from shared.models import Session
from shared.security import hash_token

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def _session(role: str = "user") -> Session:
    return Session(
        session_id="hashed",
        user_id="uid-1",
        username="alice",
        role=role,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def _request(*, authorization: str | None = None, cookie: str | None = None):
    """get_current_user に渡す最小限の擬似 Request。"""
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    cookies = {}
    if cookie is not None:
        cookies["nl_session"] = cookie
    return SimpleNamespace(headers=headers, cookies=cookies)


def test_get_firestore_client_returns_same_instance():
    """get_firestore_client() は同一インスタンスを返すこと（キャッシュ動作）。"""
    with patch("shared.firestore_client.firestore.Client"):
        import importlib

        import api.dependencies as deps
        importlib.reload(deps)  # キャッシュリセット

        assert deps.get_firestore_client() is deps.get_firestore_client()


class TestExtractToken:
    def test_bearer_takes_precedence(self):
        from api.dependencies import _extract_session_token
        req = _request(authorization="Bearer abc123", cookie="cookieval")
        assert _extract_session_token(req) == "abc123"

    def test_cookie_fallback(self):
        from api.dependencies import _extract_session_token
        req = _request(cookie="cookieval")
        assert _extract_session_token(req) == "cookieval"

    def test_none_when_absent(self):
        from api.dependencies import _extract_session_token
        assert _extract_session_token(_request()) is None


class TestGetCurrentUser:
    def test_returns_session_for_valid_token(self):
        from api.dependencies import get_current_user
        db = MagicMock()
        db.get_session.return_value = _session()
        result = get_current_user(_request(authorization="Bearer raw-token"), db=db)
        assert result.user_id == "uid-1"
        # 生トークンではなくハッシュで DB を引いている
        db.get_session.assert_called_once_with(hash_token("raw-token"))

    def test_401_when_no_token(self):
        from api.dependencies import get_current_user
        with pytest.raises(HTTPException) as exc:
            get_current_user(_request(), db=MagicMock())
        assert exc.value.status_code == 401

    def test_401_when_session_missing_or_expired(self):
        from api.dependencies import get_current_user
        db = MagicMock()
        db.get_session.return_value = None  # 期限切れ含む
        with pytest.raises(HTTPException) as exc:
            get_current_user(_request(cookie="x"), db=db)
        assert exc.value.status_code == 401

    def test_touches_last_used_when_stale(self):
        """issue #84: last_used_at が未設定（古い）なら last_used_at を更新する。"""
        from api.dependencies import get_current_user
        db = MagicMock()
        session = _session()
        session.last_used_at = None  # 一度も更新されていない
        db.get_session.return_value = session

        get_current_user(_request(authorization="Bearer raw-token"), db=db)

        db.update_session_last_used.assert_called_once()
        assert db.update_session_last_used.call_args[0][0] == hash_token("raw-token")

    def test_does_not_touch_last_used_when_fresh(self):
        """直近に更新済みなら書き込みを抑制する（スロットル）。"""
        from api.dependencies import get_current_user
        db = MagicMock()
        session = _session()
        session.last_used_at = datetime.now(timezone.utc)  # ごく最近
        db.get_session.return_value = session

        get_current_user(_request(authorization="Bearer raw-token"), db=db)

        db.update_session_last_used.assert_not_called()


def test_get_user_id_returns_session_user_id():
    from api.dependencies import get_user_id
    assert get_user_id(current=_session()) == "uid-1"


class TestRequireAdmin:
    def test_allows_admin(self):
        from api.dependencies import require_admin
        s = _session(role="admin")
        assert require_admin(current=s) is s

    def test_rejects_non_admin(self):
        from api.dependencies import require_admin
        with pytest.raises(HTTPException) as exc:
            require_admin(current=_session(role="user"))
        assert exc.value.status_code == 403
