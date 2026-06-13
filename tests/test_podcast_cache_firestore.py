"""FirestoreClient のキャッシュ操作メソッドのユニットテスト。

get_podcast_cache / save_podcast_cache / try_acquire_cache を検証する。
try_acquire_cache は try_acquire_job_lock（firestore_client.py:122）と同型の
read→条件付き write トランザクションパターンを使う。
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
CACHE_KEY = "art1abc123456789ab__toeic_900__ja-en"
ARTICLE_ID = "art1abc123456789ab"


def _build_firestore_doc_dict(status: str = "completed") -> dict:
    """get_podcast_cache が doc.to_dict() から受け取るデータ（cache_key は doc.id から復元するため含まない）。"""
    return {
        "article_id": ARTICLE_ID,
        "difficulty": "toeic_900",
        "language": "ja-en",
        "status": status,
        "audio_url": f"podcasts/cache/{CACHE_KEY}.mp3" if status == "completed" else None,
        "japanese_intro_text": "イントロ" if status == "completed" else None,
        "duration_seconds": 300 if status == "completed" else None,
        "created_at": "2026-06-14T12:00:00+00:00",
    }


# ===========================
# get_podcast_cache
# ===========================


def test_get_podcast_cache_returns_podcast_cache_when_exists(mock_firestore_db):
    """存在する podcastCache ドキュメントから PodcastCache を復元すること。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = CACHE_KEY
    mock_doc.to_dict.return_value = _build_firestore_doc_dict(status="completed")
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    result = client.get_podcast_cache(CACHE_KEY)

    assert result is not None
    assert result.cache_key == CACHE_KEY
    assert result.status == "completed"


def test_get_podcast_cache_uses_podcastcache_collection(mock_firestore_db):
    """podcastCache コレクションの {cache_key} ドキュメントを O(1) 直引きすること。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = CACHE_KEY
    mock_doc.to_dict.return_value = _build_firestore_doc_dict()
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    client.get_podcast_cache(CACHE_KEY)

    mock_firestore_db.collection.assert_called_with("podcastCache")
    mock_firestore_db.collection.return_value.document.assert_called_with(CACHE_KEY)


def test_get_podcast_cache_returns_none_when_not_found(mock_firestore_db):
    """存在しない cache_key は None を返すこと。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    result = client.get_podcast_cache(CACHE_KEY)

    assert result is None


def test_get_podcast_cache_raises_on_corrupt_document(mock_firestore_db):
    """必須フィールドが欠落した破損ドキュメントは ValidationError を送出すること。

    データ破損を早期検出するために隠蔽しない（Fail Fast 原則）。
    """
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = CACHE_KEY
    # article_id・difficulty・language などの必須フィールドを意図的に欠落させる
    mock_doc.to_dict.return_value = {"status": "completed"}
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    with pytest.raises(ValidationError):
        client.get_podcast_cache(CACHE_KEY)


def test_get_podcast_cache_restores_cache_key_from_doc_id(mock_firestore_db):
    """cache_key は doc.id から復元すること（Podcast.id / Article.id と同じ流儀）。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = CACHE_KEY
    mock_doc.to_dict.return_value = _build_firestore_doc_dict(status="processing")
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    result = client.get_podcast_cache(CACHE_KEY)

    assert result.cache_key == CACHE_KEY


def test_get_podcast_cache_returns_processing_status_with_none_fields(mock_firestore_db):
    """processing 状態の doc から、成果物フィールドが None の PodcastCache を返すこと。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = CACHE_KEY
    mock_doc.to_dict.return_value = _build_firestore_doc_dict(status="processing")
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    result = client.get_podcast_cache(CACHE_KEY)

    assert result.status == "processing"
    assert result.audio_url is None


def test_get_podcast_cache_returns_failed_status(mock_firestore_db):
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = CACHE_KEY
    mock_doc.to_dict.return_value = _build_firestore_doc_dict(status="failed")
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    result = client.get_podcast_cache(CACHE_KEY)

    assert result.status == "failed"


# ===========================
# save_podcast_cache
# ===========================


def test_save_podcast_cache_uses_podcastcache_collection(mock_firestore_db):
    """podcastCache/{cache_key} ドキュメントに set すること。"""
    from shared.firestore_client import FirestoreClient
    from shared.models import PodcastCache

    client = FirestoreClient()
    cache = PodcastCache(
        cache_key=CACHE_KEY,
        article_id=ARTICLE_ID,
        difficulty="toeic_900",
        language="ja-en",
        status="completed",
        audio_url=f"podcasts/cache/{CACHE_KEY}.mp3",
        japanese_intro_text="イントロ",
        duration_seconds=300,
        created_at=NOW,
    )
    mock_doc_ref = MagicMock()
    mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

    client.save_podcast_cache(cache)

    mock_firestore_db.collection.assert_called_with("podcastCache")
    mock_firestore_db.collection.return_value.document.assert_called_with(CACHE_KEY)
    mock_doc_ref.set.assert_called_once()


def test_save_podcast_cache_excludes_cache_key_from_payload(mock_firestore_db):
    """cache_key は doc-id として使うため、保存ペイロードから除外すること。

    Podcast.id / Article.id の pop 流儀と同じ実装パターン。
    """
    from shared.firestore_client import FirestoreClient
    from shared.models import PodcastCache

    client = FirestoreClient()
    cache = PodcastCache(
        cache_key=CACHE_KEY,
        article_id=ARTICLE_ID,
        difficulty="toeic_900",
        language="ja-en",
        status="processing",
        created_at=NOW,
    )
    mock_doc_ref = MagicMock()
    mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

    client.save_podcast_cache(cache)

    payload = mock_doc_ref.set.call_args[0][0]
    assert "cache_key" not in payload


def test_save_podcast_cache_serializes_datetime_as_iso_string(mock_firestore_db):
    """model_dump(mode='json') により datetime が ISO 文字列として保存されること。

    Firestore との型不整合を防ぐため、save_podcast / save_article と同じ
    mode='json' 流儀を用いる。
    """
    from shared.firestore_client import FirestoreClient
    from shared.models import PodcastCache

    client = FirestoreClient()
    cache = PodcastCache(
        cache_key=CACHE_KEY,
        article_id=ARTICLE_ID,
        difficulty="toeic_900",
        language="ja-en",
        status="processing",
        created_at=NOW,
    )
    mock_doc_ref = MagicMock()
    mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

    client.save_podcast_cache(cache)

    payload = mock_doc_ref.set.call_args[0][0]
    assert isinstance(payload["created_at"], str)
    assert payload["created_at"].startswith("2026-06-14")


def test_save_podcast_cache_includes_status_in_payload(mock_firestore_db):
    """ステータス遷移（completed / failed）を全置換で書き込めること。"""
    from shared.firestore_client import FirestoreClient
    from shared.models import PodcastCache

    client = FirestoreClient()
    cache = PodcastCache(
        cache_key=CACHE_KEY,
        article_id=ARTICLE_ID,
        difficulty="toeic_900",
        language="ja-en",
        status="failed",
        created_at=NOW,
    )
    mock_doc_ref = MagicMock()
    mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

    client.save_podcast_cache(cache)

    payload = mock_doc_ref.set.call_args[0][0]
    assert payload["status"] == "failed"


# ===========================
# try_acquire_cache
# ===========================


def _setup_cache_snapshot(mock_firestore_db, snapshot: MagicMock):
    """podcastCache ドキュメントの get がトランザクション経由で snapshot を返すよう構成する。"""
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.document.return_value = ref
    return ref


def test_try_acquire_cache_acquires_when_no_existing_cache(mock_firestore_db):
    """キャッシュが存在しない場合は processing を書き込んで True を返すこと。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    snapshot = MagicMock()
    snapshot.exists = False
    ref = _setup_cache_snapshot(mock_firestore_db, snapshot)

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        acquired = client.try_acquire_cache(CACHE_KEY, ARTICLE_ID, "toeic_900", "ja-en")

    assert acquired is True
    transaction = mock_firestore_db.transaction.return_value
    transaction.set.assert_called_once()
    set_ref, set_data = transaction.set.call_args[0]
    assert set_ref is ref
    assert set_data["status"] == "processing"


def test_try_acquire_cache_acquires_when_status_is_failed(mock_firestore_db):
    """status=failed のキャッシュは再確保して True を返すこと（自己修復）。

    failed を再確保可能にすることで、前回失敗した記事を次回トリガーで再生成できる。
    """
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": "failed"}
    ref = _setup_cache_snapshot(mock_firestore_db, snapshot)

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        acquired = client.try_acquire_cache(CACHE_KEY, ARTICLE_ID, "toeic_900", "ja-en")

    assert acquired is True
    transaction = mock_firestore_db.transaction.return_value
    transaction.set.assert_called_once()
    _, set_data = transaction.set.call_args[0]
    assert set_data["status"] == "processing"


def test_try_acquire_cache_returns_false_when_status_is_processing(mock_firestore_db):
    """status=processing のキャッシュは取得できない（False を返す、書き込みもしない）。

    方式 B: 他ジョブが生成中なら今回はスキップし、次回トリガーで補完する。
    """
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": "processing"}
    _setup_cache_snapshot(mock_firestore_db, snapshot)

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        acquired = client.try_acquire_cache(CACHE_KEY, ARTICLE_ID, "toeic_900", "ja-en")

    assert acquired is False
    mock_firestore_db.transaction.return_value.set.assert_not_called()


def test_try_acquire_cache_returns_false_when_status_is_completed(mock_firestore_db):
    """status=completed のキャッシュは取得できない（False を返す、書き込みもしない）。

    完了済みキャッシュの再生成を防ぐ。
    """
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": "completed"}
    _setup_cache_snapshot(mock_firestore_db, snapshot)

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        acquired = client.try_acquire_cache(CACHE_KEY, ARTICLE_ID, "toeic_900", "ja-en")

    assert acquired is False
    mock_firestore_db.transaction.return_value.set.assert_not_called()


def test_try_acquire_cache_writes_all_required_fields(mock_firestore_db):
    """processing 確保時に article_id / difficulty / language / status / created_at を書き込むこと。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    snapshot = MagicMock()
    snapshot.exists = False
    _setup_cache_snapshot(mock_firestore_db, snapshot)

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        client.try_acquire_cache(CACHE_KEY, ARTICLE_ID, "toeic_900", "ja-en")

    set_data = mock_firestore_db.transaction.return_value.set.call_args[0][1]
    assert set_data["article_id"] == ARTICLE_ID
    assert set_data["difficulty"] == "toeic_900"
    assert set_data["language"] == "ja-en"
    assert set_data["status"] == "processing"
    assert "created_at" in set_data


def test_try_acquire_cache_uses_podcastcache_collection(mock_firestore_db):
    """podcastCache コレクションの {cache_key} ドキュメントをトランザクションで操作すること。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    snapshot = MagicMock()
    snapshot.exists = False
    _setup_cache_snapshot(mock_firestore_db, snapshot)

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        client.try_acquire_cache(CACHE_KEY, ARTICLE_ID, "toeic_900", "ja-en")

    mock_firestore_db.collection.assert_called_with("podcastCache")
    mock_firestore_db.collection.return_value.document.assert_called_with(CACHE_KEY)


def test_try_acquire_cache_returns_true_means_caller_owns_generation(mock_firestore_db):
    """True を返した場合、呼び出し元が生成権を取得したことを意味する。

    spec では「True = 自分が生成権を取得 / False = 他者が生成中または既に完了」と定義。
    """
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    snapshot = MagicMock()
    snapshot.exists = False
    _setup_cache_snapshot(mock_firestore_db, snapshot)

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        result = client.try_acquire_cache(CACHE_KEY, ARTICLE_ID, "toeic_900", "ja-en")

    assert result is True
