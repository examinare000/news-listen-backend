from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone
from shared.models import Article, UserPrefs


def test_save_article_calls_firestore_set(mock_firestore_db):
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    article = Article(
        id="hash123",
        title="Test",
        url="https://example.com",
        source="hackernews",
        content="content",
        published_at=now,
        fetched_at=now,
    )

    mock_doc_ref = MagicMock()
    mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

    client.save_article(article)

    mock_firestore_db.collection.assert_called_with("articles")
    mock_firestore_db.collection.return_value.document.assert_called_with("hash123")
    mock_doc_ref.set.assert_called_once()


def test_save_article_serializes_datetime_as_string(mock_firestore_db):
    """model_dump(mode='json') により datetime が ISO 文字列として保存されることを確認する"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    article = Article(
        id="hash123",
        title="Test",
        url="https://example.com",
        source="hackernews",
        content="content",
        published_at=now,
        fetched_at=now,
    )

    mock_doc_ref = MagicMock()
    mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

    client.save_article(article)

    call_args = mock_doc_ref.set.call_args[0][0]
    # datetime は ISO 文字列で保存される（Pydantic v2 は "Z" suffix を使う）
    assert isinstance(call_args["published_at"], str)
    # "2026-05-31T12:00:00Z" or "2026-05-31T12:00:00+00:00" どちらも有効なUTC表現
    assert call_args["published_at"].startswith("2026-05-31T12:00:00")


def test_article_exists_returns_true_when_document_exists(mock_firestore_db):
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    assert client.article_exists("hash123") is True


def test_article_exists_returns_false_when_document_missing(mock_firestore_db):
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    assert client.article_exists("hash123") is False


def test_get_user_prefs_returns_default_when_not_found(mock_firestore_db):
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    prefs = client.get_user_prefs("user1")
    assert prefs.user_id == "user1"
    assert prefs.rss_sources == []
    assert prefs.default_difficulty == "toeic_900"


def test_get_podcast_returns_podcast_by_id(mock_firestore_db):
    """get_podcast(podcast_id) が O(1) で直接 Firestore ドキュメントを取得することを確認する"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = "pod1"
    mock_doc.to_dict.return_value = {
        "type": "single",
        "article_ids": ["art1"],
        "difficulty": "toeic_900",
        "audio_url": "https://example.com/pod1.mp3",
        "japanese_intro_text": "イントロ",
        "duration_seconds": 300,
        "status": "completed",
        "error_message": None,
        "created_at": "2026-05-31T12:00:00+00:00",
        "user_id": "user1",
    }
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    podcast = client.get_podcast("pod1")

    assert podcast is not None
    assert podcast.id == "pod1"
    mock_firestore_db.collection.assert_called_with("podcasts")
    mock_firestore_db.collection.return_value.document.assert_called_with("pod1")


def test_get_podcast_returns_none_when_not_found(mock_firestore_db):
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    result = client.get_podcast("missing")
    assert result is None


# ---------- Featured sites (global) ----------


def test_get_featured_sites_orders_by_order(mock_firestore_db):
    """get_featured_sites が order 昇順でストリームし、id を doc.id から復元する。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    doc = MagicMock()
    doc.id = "the-verge"
    doc.to_dict.return_value = {
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "thumbnail_url": None,
        "description": None,
        "order": 0,
    }
    order_by = mock_firestore_db.collection.return_value.order_by
    order_by.return_value.stream.return_value = [doc]

    sites = client.get_featured_sites()

    mock_firestore_db.collection.assert_called_with("featuredSites")
    order_by.assert_called_with("order")
    assert len(sites) == 1
    assert sites[0].id == "the-verge"


def test_save_featured_site_pops_id_and_sets(mock_firestore_db):
    """save_featured_site は id を doc-id にして payload から除外する（save_podcast と同流儀）。"""
    from shared.firestore_client import FirestoreClient
    from shared.models import FeaturedSite
    client = FirestoreClient()

    mock_doc_ref = MagicMock()
    mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

    site = FeaturedSite(
        id="techcrunch", name="TechCrunch", url="https://techcrunch.com/feed/", order=1
    )
    client.save_featured_site(site)

    mock_firestore_db.collection.assert_called_with("featuredSites")
    mock_firestore_db.collection.return_value.document.assert_called_with("techcrunch")
    saved_data = mock_doc_ref.set.call_args[0][0]
    assert "id" not in saved_data
    assert saved_data["name"] == "TechCrunch"
    assert saved_data["order"] == 1


def test_delete_featured_site_calls_delete(mock_firestore_db):
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_doc_ref = MagicMock()
    mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

    client.delete_featured_site("techcrunch")

    mock_firestore_db.collection.assert_called_with("featuredSites")
    mock_firestore_db.collection.return_value.document.assert_called_with("techcrunch")
    mock_doc_ref.delete.assert_called_once()


# ---------- Job locks (debounce) ----------

def _setup_lock_mock(mock_firestore_db, snapshot):
    """jobLocks ドキュメントの get がトランザクション経由で snapshot を返すよう構成する。"""
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.document.return_value = ref
    return ref


def test_try_acquire_job_lock_acquires_when_no_existing_lock(mock_firestore_db):
    """ロックが存在しなければ取得して True を返し、TTL 付きでロックを書き込む。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    snapshot = MagicMock()
    snapshot.exists = False
    ref = _setup_lock_mock(mock_firestore_db, snapshot)

    # firestore.transactional を恒等関数に差し替え、トランザクション本体を直接実行させる。
    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        acquired = client.try_acquire_job_lock("user1", "recommendation", 120)

    assert acquired is True
    mock_firestore_db.collection.assert_called_with("jobLocks")
    mock_firestore_db.collection.return_value.document.assert_called_with("user1_recommendation")
    # トランザクション経由でロックドキュメントを set する。
    transaction = mock_firestore_db.transaction.return_value
    transaction.set.assert_called_once()
    set_ref, set_data = transaction.set.call_args[0]
    assert set_ref is ref
    assert "expires_at" in set_data


def test_try_acquire_job_lock_returns_false_when_lock_still_valid(mock_firestore_db):
    """未失効のロックがあれば取得せず False を返し、書き込みも行わない。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"expires_at": future}
    _setup_lock_mock(mock_firestore_db, snapshot)

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        acquired = client.try_acquire_job_lock("user1", "recommendation", 120)

    assert acquired is False
    mock_firestore_db.transaction.return_value.set.assert_not_called()


def test_try_acquire_job_lock_acquires_when_existing_lock_expired(mock_firestore_db):
    """失効済みロックは取得し直せる。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"expires_at": past}
    _setup_lock_mock(mock_firestore_db, snapshot)

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        acquired = client.try_acquire_job_lock("user1", "recommendation", 120)

    assert acquired is True
    mock_firestore_db.transaction.return_value.set.assert_called_once()
