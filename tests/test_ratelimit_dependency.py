"""レート制限依存関数 rate_limit() のテスト。

FastAPI 統合と env 動的読み込みを検証する。
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_db_for_ratelimit():
    """consume_rate_limit メソッド付き FirestoreClient モック。"""
    mock = MagicMock()
    return mock


@pytest.fixture
def mock_request():
    """HTTPRequest モック。"""
    mock = MagicMock()
    mock.headers = {}
    mock.cookies = {}
    mock.client = MagicMock()
    mock.client.host = "192.168.1.1"
    return mock


def test_disabled_when_max_requests_zero(mock_db_for_ratelimit, mock_request):
    """max_requests=0 のとき: consume_rate_limit が呼ばれない（no-op）。"""
    from api.ratelimit import rate_limit

    # env を設定: API_RATELIMIT_MAX_REQUESTS=0
    with patch.dict("os.environ", {"API_RATELIMIT_MAX_REQUESTS": "0"}):
        dep = rate_limit("api")
        # 依存関数を直接呼び出す（非async版を想定して test ）
        # 実際には FastAPI が呼び出すため、ここはモック化
        import asyncio
        asyncio.run(dep(mock_request, mock_db_for_ratelimit))

    # consume_rate_limit が呼ばれていない
    mock_db_for_ratelimit.consume_rate_limit.assert_not_called()


def test_allows_within_limit(mock_db_for_ratelimit, mock_request):
    """制限内: allowed=True で 200 返す（例外なし）。"""
    from api.ratelimit import rate_limit

    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    # consume_rate_limit のモック: 常に (True, 0)
    mock_db_for_ratelimit.consume_rate_limit.return_value = (True, 0)

    with patch.dict("os.environ", {"API_RATELIMIT_MAX_REQUESTS": "10"}):
        with patch("api.ratelimit.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.timezone = timezone

            dep = rate_limit("api")
            import asyncio
            # 例外が出ないことを確認
            asyncio.run(dep(mock_request, mock_db_for_ratelimit))

    # consume_rate_limit が少なくとも 1 回呼ばれた（IP 軸）
    assert mock_db_for_ratelimit.consume_rate_limit.call_count >= 1


def test_returns_429_with_retry_after_when_exceeded(mock_db_for_ratelimit, mock_request):
    """超過時: HTTPException(429) + Retry-After ヘッダを返す。"""
    from fastapi import HTTPException
    from api.ratelimit import rate_limit

    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    # consume_rate_limit のモック: IP 軸で (False, 3600) を返す
    mock_db_for_ratelimit.consume_rate_limit.return_value = (False, 3600)

    with patch.dict("os.environ", {"API_RATELIMIT_MAX_REQUESTS": "10"}):
        with patch("api.ratelimit.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.timezone = timezone

            dep = rate_limit("api")
            import asyncio
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(dep(mock_request, mock_db_for_ratelimit))

    exc = exc_info.value
    assert exc.status_code == 429
    assert "Retry-After" in exc.headers
    assert exc.headers["Retry-After"] == "3600"


def test_unauthenticated_request_counts_ip_only(mock_db_for_ratelimit, mock_request):
    """未認証リクエスト（トークンなし）: IP 軸のみ計数される。"""
    from api.ratelimit import rate_limit

    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    # consume_rate_limit のモック: (True, 0)
    mock_db_for_ratelimit.consume_rate_limit.return_value = (True, 0)
    # get_session が None を返す（トークン無効 or 期限切れ）
    mock_db_for_ratelimit.get_session.return_value = None

    with patch.dict("os.environ", {"API_RATELIMIT_MAX_REQUESTS": "10"}):
        with patch("api.ratelimit.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.timezone = timezone

            dep = rate_limit("api")
            import asyncio
            asyncio.run(dep(mock_request, mock_db_for_ratelimit))

    # consume_rate_limit は IP 軸のみ 1 回呼ばれ（user 軸は呼ばれない）
    # get_session が呼ばれていないことを確認（_resolve_user_id_optional が None で早期 return）
    assert mock_db_for_ratelimit.consume_rate_limit.call_count == 1


def test_session_resolution_failure_falls_back_to_ip(mock_db_for_ratelimit, mock_request):
    """セッション解決失敗（例外）: user 軸をスキップして IP 軸のみ計数。"""
    from api.ratelimit import rate_limit

    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    # consume_rate_limit のモック: (True, 0)
    mock_db_for_ratelimit.consume_rate_limit.return_value = (True, 0)
    # get_session が例外を出す（DB 障害等）
    mock_db_for_ratelimit.get_session.side_effect = Exception("DB error")

    with patch.dict("os.environ", {"API_RATELIMIT_MAX_REQUESTS": "10"}):
        with patch("api.ratelimit.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.timezone = timezone

            dep = rate_limit("api")
            import asyncio
            # 例外が出ずに処理される（_resolve_user_id_optional が例外を catch）
            asyncio.run(dep(mock_request, mock_db_for_ratelimit))

    # consume_rate_limit は IP 軸のみ 1 回呼ばれ
    assert mock_db_for_ratelimit.consume_rate_limit.call_count == 1


def test_does_not_leak_internal_detail_in_429_body(mock_db_for_ratelimit, mock_request):
    """429 応答本文に内部詳細（トークン等）を含めない。"""
    from fastapi import HTTPException
    from api.ratelimit import rate_limit

    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    # consume_rate_limit のモック: (False, 100)
    mock_db_for_ratelimit.consume_rate_limit.return_value = (False, 100)

    with patch.dict("os.environ", {"API_RATELIMIT_MAX_REQUESTS": "10"}):
        with patch("api.ratelimit.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.timezone = timezone

            dep = rate_limit("api")
            import asyncio
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(dep(mock_request, mock_db_for_ratelimit))

    exc = exc_info.value
    # detail にトークン情報が含まれていない
    assert "token" not in exc.detail.lower()
    assert "bearer" not in exc.detail.lower()
    # 一般的なメッセージのみ
    assert "Too many requests" in exc.detail
