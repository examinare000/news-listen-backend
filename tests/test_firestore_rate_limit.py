"""Firestore レート制限の consume_rate_limit メソッドのテスト。

トランザクション内での固定ウィンドウ計数をテストする。
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_firestore_db_for_rate_limit():
    """consume_rate_limit テスト用の Firestore モック。

    transaction の初期化とドキュメント操作を模擬する。
    """
    with patch("shared.firestore_client.firestore.Client") as mock_client_class:
        mock_db = MagicMock()
        mock_client_class.return_value = mock_db
        yield mock_db


def test_first_request_initializes_window_and_allows(mock_firestore_db_for_rate_limit):
    """初回リクエスト: ドキュメント未存在 → window_start=now, count=1, allowed=True を返す。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    # モックの設定: transaction 内で snapshot.exists == False
    mock_transaction = MagicMock()
    mock_db_instance = mock_firestore_db_for_rate_limit
    mock_db_instance.transaction.return_value = mock_transaction

    mock_snapshot = MagicMock()
    mock_snapshot.exists = False
    mock_collection_ref = mock_firestore_db_for_rate_limit.collection.return_value
    mock_doc_ref = mock_collection_ref.document.return_value
    mock_doc_ref.get.return_value = mock_snapshot

    allowed, retry_after = client.consume_rate_limit("api:ip:hash123", now=now, max_requests=10, window_seconds=60)

    assert allowed is True
    assert retry_after == 0
    # transaction.set が呼ばれたことを検証（write-through）
    mock_transaction.set.assert_called()
    call_args = mock_transaction.set.call_args[0][1]
    assert call_args["count"] == 1
    assert call_args["window_start"] == now


def test_within_window_increments_and_allows_up_to_max(mock_firestore_db_for_rate_limit):
    """ウィンドウ内、max 未超過: count をインクリメントして allowed=True を返す。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    now = datetime(2026, 6, 24, 12, 0, 30, tzinfo=timezone.utc)
    window_start = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    mock_transaction = MagicMock()
    mock_db_instance = mock_firestore_db_for_rate_limit
    mock_db_instance.transaction.return_value = mock_transaction

    # 既存: count=5, window_start=window_start（ウィンドウ内）
    mock_snapshot = MagicMock()
    mock_snapshot.exists = True
    mock_snapshot.to_dict.return_value = {
        "key": "api:ip:hash123",
        "count": 5,
        "window_start": window_start,
    }

    mock_collection_ref = mock_firestore_db_for_rate_limit.collection.return_value
    mock_doc_ref = mock_collection_ref.document.return_value
    mock_doc_ref.get.return_value = mock_snapshot

    allowed, retry_after = client.consume_rate_limit(
        "api:ip:hash123", now=now, max_requests=10, window_seconds=60
    )

    assert allowed is True
    assert retry_after == 0
    mock_transaction.set.assert_called()
    call_args = mock_transaction.set.call_args[0][1]
    assert call_args["count"] == 6  # 5 + 1


def test_exceeding_max_returns_not_allowed_with_retry_after(mock_firestore_db_for_rate_limit):
    """max 超過: allowed=False, retry_after=残り秒数（最小1秒）を返す。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    now = datetime(2026, 6, 24, 12, 0, 50, tzinfo=timezone.utc)
    window_start = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    mock_transaction = MagicMock()
    mock_db_instance = mock_firestore_db_for_rate_limit
    mock_db_instance.transaction.return_value = mock_transaction

    # 既存: count=10（既に max）, window_start=window_start（ウィンドウ内）
    mock_snapshot = MagicMock()
    mock_snapshot.exists = True
    mock_snapshot.to_dict.return_value = {
        "key": "api:ip:hash123",
        "count": 10,
        "window_start": window_start,
    }

    mock_collection_ref = mock_firestore_db_for_rate_limit.collection.return_value
    mock_doc_ref = mock_collection_ref.document.return_value
    mock_doc_ref.get.return_value = mock_snapshot

    allowed, retry_after = client.consume_rate_limit(
        "api:ip:hash123", now=now, max_requests=10, window_seconds=60
    )

    assert allowed is False
    # now=12:00:50, window_start=12:00:00 → 経過=50秒 → 残り=60-50=10秒
    assert retry_after == 10


def test_exceeding_request_is_not_counted(mock_firestore_db_for_rate_limit):
    """超過試行: allowed=False 返却、ただし doc は更新しない（count 据え置き）。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    now = datetime(2026, 6, 24, 12, 0, 50, tzinfo=timezone.utc)
    window_start = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    mock_transaction = MagicMock()
    mock_db_instance = mock_firestore_db_for_rate_limit
    mock_db_instance.transaction.return_value = mock_transaction

    # 既存: count=10（既に max）
    mock_snapshot = MagicMock()
    mock_snapshot.exists = True
    mock_snapshot.to_dict.return_value = {
        "key": "api:ip:hash123",
        "count": 10,
        "window_start": window_start,
    }

    mock_collection_ref = mock_firestore_db_for_rate_limit.collection.return_value
    mock_doc_ref = mock_collection_ref.document.return_value
    mock_doc_ref.get.return_value = mock_snapshot

    allowed, retry_after = client.consume_rate_limit(
        "api:ip:hash123", now=now, max_requests=10, window_seconds=60
    )

    assert allowed is False
    # set が呼ばれていない（超過試行は非カウント）
    mock_doc_ref.set.assert_not_called()


def test_window_expiry_resets_count_to_one(mock_firestore_db_for_rate_limit):
    """ウィンドウ失効: count=1, window_start=now にリセット、allowed=True。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    now = datetime(2026, 6, 24, 12, 2, 0, tzinfo=timezone.utc)  # 窓から2分後
    window_start = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    mock_transaction = MagicMock()
    mock_db_instance = mock_firestore_db_for_rate_limit
    mock_db_instance.transaction.return_value = mock_transaction

    # 既存: count=10, window_start=窓_start（失効: 経過120秒 > 60秒）
    mock_snapshot = MagicMock()
    mock_snapshot.exists = True
    mock_snapshot.to_dict.return_value = {
        "key": "api:ip:hash123",
        "count": 10,
        "window_start": window_start,
    }

    mock_collection_ref = mock_firestore_db_for_rate_limit.collection.return_value
    mock_doc_ref = mock_collection_ref.document.return_value
    mock_doc_ref.get.return_value = mock_snapshot

    allowed, retry_after = client.consume_rate_limit(
        "api:ip:hash123", now=now, max_requests=10, window_seconds=60
    )

    assert allowed is True
    assert retry_after == 0
    mock_transaction.set.assert_called()
    call_args = mock_transaction.set.call_args[0][1]
    assert call_args["count"] == 1
    assert call_args["window_start"] == now


def test_window_boundary_exactly_at_edge_resets(mock_firestore_db_for_rate_limit):
    """境界値: 経過秒数 == window_seconds のとき失効扱い（reset）。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    now = datetime(2026, 6, 24, 12, 1, 0, tzinfo=timezone.utc)  # 窓から1分ちょうど後
    window_start = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    mock_transaction = MagicMock()
    mock_db_instance = mock_firestore_db_for_rate_limit
    mock_db_instance.transaction.return_value = mock_transaction

    mock_snapshot = MagicMock()
    mock_snapshot.exists = True
    mock_snapshot.to_dict.return_value = {
        "key": "api:ip:hash123",
        "count": 10,
        "window_start": window_start,
    }

    mock_collection_ref = mock_firestore_db_for_rate_limit.collection.return_value
    mock_doc_ref = mock_collection_ref.document.return_value
    mock_doc_ref.get.return_value = mock_snapshot

    allowed, retry_after = client.consume_rate_limit(
        "api:ip:hash123", now=now, max_requests=10, window_seconds=60
    )

    # 経過 = 60秒、window_seconds = 60 → == なので失効扱い
    assert allowed is True
    call_args = mock_transaction.set.call_args[0][1]
    assert call_args["count"] == 1
    assert call_args["window_start"] == now


def test_retry_after_is_at_least_one_second(mock_firestore_db_for_rate_limit):
    """retry_after は最小1秒（0 にならない）。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    # window_start + 59秒後（残り1秒未満）
    now = datetime(2026, 6, 24, 12, 0, 59, 999999, tzinfo=timezone.utc)
    window_start = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    mock_transaction = MagicMock()
    mock_db_instance = mock_firestore_db_for_rate_limit
    mock_db_instance.transaction.return_value = mock_transaction

    mock_snapshot = MagicMock()
    mock_snapshot.exists = True
    mock_snapshot.to_dict.return_value = {
        "key": "api:ip:hash123",
        "count": 10,
        "window_start": window_start,
    }

    mock_collection_ref = mock_firestore_db_for_rate_limit.collection.return_value
    mock_doc_ref = mock_collection_ref.document.return_value
    mock_doc_ref.get.return_value = mock_snapshot

    allowed, retry_after = client.consume_rate_limit(
        "api:ip:hash123", now=now, max_requests=10, window_seconds=60
    )

    assert allowed is False
    # 経過 ≈ 59.999秒 → 残り ≈ 0.001秒 → max(1, int(...)) = 1
    assert retry_after >= 1


def test_separate_keys_are_counted_independently(mock_firestore_db_for_rate_limit):
    """異なるキー: 独立に計数される（count の干渉がない）。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    now = datetime(2026, 6, 24, 12, 0, 30, tzinfo=timezone.utc)
    window_start = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    mock_transaction = MagicMock()
    mock_db_instance = mock_firestore_db_for_rate_limit
    mock_db_instance.transaction.return_value = mock_transaction

    # key1 用と key2 用で異なるスナップショットを返す
    key1 = "api:user:user1"
    key2 = "api:user:user2"

    snap1 = MagicMock()
    snap1.exists = True
    snap1.to_dict.return_value = {
        "key": key1,
        "count": 5,
        "window_start": window_start,
    }

    snap2 = MagicMock()
    snap2.exists = True
    snap2.to_dict.return_value = {
        "key": key2,
        "count": 2,
        "window_start": window_start,
    }

    mock_collection_ref = mock_firestore_db_for_rate_limit.collection.return_value

    # ドキュメント参照の取得時に異なるスナップショットを返すため、
    # document().get() の呼び出しの度に異なる返り値を返す
    mock_doc_ref = MagicMock()
    mock_doc_ref.get.side_effect = [snap1, snap2]
    mock_collection_ref.document.return_value = mock_doc_ref

    allowed1, retry1 = client.consume_rate_limit(
        key1, now=now, max_requests=10, window_seconds=60
    )
    allowed2, retry2 = client.consume_rate_limit(
        key2, now=now, max_requests=10, window_seconds=60
    )

    assert allowed1 is True  # key1: 5+1=6 < 10 → allowed
    assert allowed2 is True  # key2: 2+1=3 < 10 → allowed
