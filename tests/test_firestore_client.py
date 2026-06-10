from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
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
