"""issue #82: FirestoreClient.consume_daily_generation のテスト。

ユーザー別・1日あたりの Podcast 生成回数カウンタ（日次バケット）。
トランザクション内での計数・上限判定・翌 UTC 0 時までの retry_after を検証する。
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_fs():
    with patch("shared.firestore_client.firestore.Client") as mock_client_class:
        mock_db = MagicMock()
        mock_client_class.return_value = mock_db
        yield mock_db


def _setup(mock_fs, *, exists, data=None):
    """transaction と snapshot をセットアップして doc_ref を返す。"""
    mock_transaction = MagicMock()
    mock_fs.transaction.return_value = mock_transaction
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data or {}
    doc_ref = mock_fs.collection.return_value.document.return_value
    doc_ref.get.return_value = snap
    return mock_transaction, doc_ref


def test_disabled_when_max_zero_skips_db(mock_fs):
    """max_per_day<=0 は DB アクセスなしで (True, 0) を返す（無効化）。"""
    from shared.firestore_client import FirestoreClient

    allowed, retry = FirestoreClient().consume_daily_generation("user1", max_per_day=0)

    assert (allowed, retry) == (True, 0)
    mock_fs.transaction.assert_not_called()


def test_first_generation_of_day_initializes_and_allows(mock_fs):
    """その日の初回: ドキュメント未存在 → count=1・allowed=True。doc-id は user_day。"""
    from shared.firestore_client import FirestoreClient

    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)
    txn, doc_ref = _setup(mock_fs, exists=False)

    allowed, retry = FirestoreClient().consume_daily_generation("user1", now=now, max_per_day=5)

    assert allowed is True
    assert retry == 0
    mock_fs.collection.assert_called_with("dailyGenerationCounts")
    mock_fs.collection.return_value.document.assert_called_with("user1_2026-06-24")
    payload = txn.set.call_args[0][1]
    assert payload["count"] == 1
    assert payload["user_id"] == "user1"
    assert payload["day"] == "2026-06-24"


def test_within_limit_increments(mock_fs):
    """上限未満: count を +1 して allowed=True。"""
    from shared.firestore_client import FirestoreClient

    now = datetime(2026, 6, 24, 9, 0, 0, tzinfo=timezone.utc)
    txn, _ = _setup(mock_fs, exists=True, data={"user_id": "user1", "day": "2026-06-24", "count": 3})

    allowed, retry = FirestoreClient().consume_daily_generation("user1", now=now, max_per_day=5)

    assert allowed is True
    assert retry == 0
    assert txn.set.call_args[0][1]["count"] == 4


def test_at_limit_rejects_with_retry_until_midnight_and_no_count(mock_fs):
    """上限到達: allowed=False・retry_after=翌UTC0時までの秒数・カウントは更新しない。"""
    from shared.firestore_client import FirestoreClient

    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)  # 翌0時まで12h=43200s
    txn, _ = _setup(mock_fs, exists=True, data={"user_id": "user1", "day": "2026-06-24", "count": 5})

    allowed, retry = FirestoreClient().consume_daily_generation("user1", now=now, max_per_day=5)

    assert allowed is False
    assert retry == 43200
    txn.set.assert_not_called()  # 超過試行は非カウント


def test_retry_after_is_at_least_one_second(mock_fs):
    """0 時直前でも retry_after は最小 1 秒。"""
    from shared.firestore_client import FirestoreClient

    now = datetime(2026, 6, 24, 23, 59, 59, 500000, tzinfo=timezone.utc)
    _setup(mock_fs, exists=True, data={"user_id": "user1", "day": "2026-06-24", "count": 9})

    allowed, retry = FirestoreClient().consume_daily_generation("user1", now=now, max_per_day=9)

    assert allowed is False
    assert retry >= 1


def test_new_day_uses_new_doc_id(mock_fs):
    """日付が変わると doc-id が変わり（暗黙リセット）、前日のカウントは参照されない。"""
    from shared.firestore_client import FirestoreClient

    now = datetime(2026, 6, 25, 0, 0, 1, tzinfo=timezone.utc)
    _setup(mock_fs, exists=False)

    FirestoreClient().consume_daily_generation("user1", now=now, max_per_day=5)

    mock_fs.collection.return_value.document.assert_called_with("user1_2026-06-25")
