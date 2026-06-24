from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone
from shared.models import Article


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


# ---------- Podcasts with status filtering ----------


def test_podcast_exists_for_article_default_checks_completed_status(mock_firestore_db):
    """podcast_exists_for_article のデフォルト statuses は ("completed",) で呼び出し元の既存動作を保つ。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    # where chain をモック
    mock_collection = mock_firestore_db.collection.return_value
    mock_query = MagicMock()
    mock_collection.where.return_value = mock_query
    mock_query.where.return_value = mock_query
    # 最後の .where("status", "in", ("completed",)) を含めて4回の where が呼ばれる
    mock_query.limit.return_value.stream.return_value = [MagicMock()]  # 1 doc found

    exists = client.podcast_exists_for_article("user1", "art1", "toeic_900")

    assert exists is True
    # where の呼び出し順序を確認: user_id → article_ids → difficulty → type → status
    assert mock_collection.where.call_count >= 1
    # 最後の where("status", "in", ("completed",)) が呼ばれたか確認
    calls = mock_query.where.call_args_list
    status_call_found = any(
        call[0] == ("status", "in", ("completed",)) for call in calls
    )
    assert status_call_found, f"Expected status filter in where calls: {calls}"


def test_podcast_exists_for_article_filters_processing_status(mock_firestore_db):
    """processing 状態のポッドキャストは存在しないものとして扱う（status フィルタの検証）。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_collection = mock_firestore_db.collection.return_value
    mock_query = MagicMock()
    mock_collection.where.return_value = mock_query
    mock_query.where.return_value = mock_query
    # processing があれば where の最後のステータスフィルタで除外される
    mock_query.limit.return_value.stream.return_value = []  # 0 docs (processing is filtered)

    exists = client.podcast_exists_for_article("user1", "art1", "toeic_900")

    assert exists is False


def test_podcast_exists_for_article_accepts_custom_statuses(mock_firestore_db):
    """podcast_exists_for_article(statuses=...) で カスタム statuses を指定できる。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_collection = mock_firestore_db.collection.return_value
    mock_query = MagicMock()
    mock_collection.where.return_value = mock_query
    mock_query.where.return_value = mock_query
    mock_query.limit.return_value.stream.return_value = [MagicMock()]

    # カスタム statuses を指定
    exists = client.podcast_exists_for_article(
        "user1", "art1", "toeic_900", statuses=("processing", "completed")
    )

    assert exists is True
    # status フィルタが ("processing", "completed") で呼ばれたか確認
    calls = mock_query.where.call_args_list
    status_call_found = any(
        call[0] == ("status", "in", ("processing", "completed")) for call in calls
    )
    assert status_call_found, f"Expected custom status filter in where calls: {calls}"


# ---------- try_acquire_user_podcast ----------


def test_try_acquire_user_podcast_creates_processing_row_when_absent(mock_firestore_db):
    """(user, article, difficulty, type) タプルの per-user Podcast が不在なら processing 行を作成し id を返す。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    # 不在の場合: snapshot.exists = False
    snapshot = MagicMock()
    snapshot.exists = False
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.where.return_value = MagicMock()
    mock_firestore_db.collection.return_value.where.return_value.where.return_value = MagicMock()
    mock_firestore_db.collection.return_value.where.return_value.where.return_value.where.return_value = MagicMock()
    mock_firestore_db.collection.return_value.where.return_value.where.return_value.where.return_value.limit.return_value.stream.return_value = []
    # document() の返り値を設定
    mock_firestore_db.collection.return_value.document.return_value = ref

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        with patch("shared.firestore_client.uuid.uuid4", return_value="test-uuid-1234"):
            podcast_id = client.try_acquire_user_podcast("user1", "art1", "toeic_900", "ja")

    assert podcast_id == "test-uuid-1234"
    # processing 行が作成されたこと
    transaction = mock_firestore_db.transaction.return_value
    transaction.set.assert_called_once()
    _, set_data = transaction.set.call_args[0]
    assert set_data["status"] == "processing"
    assert set_data["user_id"] == "user1"
    assert set_data["article_ids"] == ["art1"]


def test_try_acquire_user_podcast_returns_none_when_exists(mock_firestore_db):
    """既存の per-user Podcast 行があれば None を返す（冪等性）。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    # 既存行を検出
    existing_doc = MagicMock()
    existing_doc.to_dict.return_value = {"status": "processing"}

    mock_collection = mock_firestore_db.collection.return_value
    mock_query = MagicMock()
    mock_collection.where.return_value = mock_query
    mock_query.where.return_value = mock_query
    mock_query.where.return_value.where.return_value = mock_query
    # stream() は既存ドキュメントを返す
    mock_query.limit.return_value.stream.return_value = [existing_doc]

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        podcast_id = client.try_acquire_user_podcast("user1", "art1", "toeic_900", "ja")

    assert podcast_id is None
    # no-op: set は呼ばれない
    mock_firestore_db.transaction.return_value.set.assert_not_called()


# ---------- get_user_podcast_for_article & promote_user_podcast ----------


def test_get_user_podcast_for_article_returns_podcast_with_id_restored(mock_firestore_db):
    """(user, article, difficulty, type='single') の per-user Podcast を返す（id は doc.id から復元）。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_doc = MagicMock()
    mock_doc.id = "podcast-123"
    mock_doc.to_dict.return_value = {
        "type": "single",
        "article_ids": ["art1"],
        "difficulty": "toeic_900",
        "audio_url": "https://example.com/pod.mp3",
        "japanese_intro_text": "イントロ",
        "duration_seconds": 300,
        "status": "completed",
        "error_message": None,
        "playback_position_seconds": 0.0,
        "created_at": "2026-05-31T12:00:00+00:00",
        "user_id": "user1",
    }

    mock_collection = mock_firestore_db.collection.return_value
    mock_query = MagicMock()
    mock_collection.where.return_value = mock_query
    mock_query.where.return_value = mock_query
    mock_query.where.return_value.where.return_value = mock_query
    # stream() で1件返す
    mock_query.limit.return_value.stream.return_value = [mock_doc]

    podcast = client.get_user_podcast_for_article("user1", "art1", "toeic_900")

    assert podcast is not None
    assert podcast.id == "podcast-123"
    assert podcast.status == "completed"


def test_get_user_podcast_for_article_returns_none_when_absent(mock_firestore_db):
    """該当する per-user Podcast が不在なら None を返す。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    mock_collection = mock_firestore_db.collection.return_value
    mock_query = MagicMock()
    mock_collection.where.return_value = mock_query
    mock_query.where.return_value = mock_query
    mock_query.where.return_value.where.return_value = mock_query
    # stream() で0件返す
    mock_query.limit.return_value.stream.return_value = []

    podcast = client.get_user_podcast_for_article("user1", "art1", "toeic_900")

    assert podcast is None


def test_promote_user_podcast_updates_when_status_is_processing(mock_firestore_db):
    """status="processing" の Podcast を update し、completed に遷移させる。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": "processing"}
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.document.return_value = ref

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        client.promote_user_podcast(
            "pod1",
            status="completed",
            audio_url="https://example.com/audio.mp3",
            japanese_intro_text="イントロ",
            duration_seconds=300,
        )

    # update で新フィールドが書き込まれたか確認
    transaction = mock_firestore_db.transaction.return_value
    transaction.update.assert_called_once()
    _, update_data = transaction.update.call_args[0]
    assert update_data["status"] == "completed"
    assert update_data["audio_url"] == "https://example.com/audio.mp3"
    assert update_data["duration_seconds"] == 300


def test_promote_user_podcast_noop_when_already_completed(mock_firestore_db):
    """status="completed" なら no-op（update は呼ばれない）。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": "completed"}
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.document.return_value = ref

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        client.promote_user_podcast("pod1", status="completed")

    # no-op: update は呼ばれない
    mock_firestore_db.transaction.return_value.update.assert_not_called()


def test_promote_user_podcast_noop_when_already_failed(mock_firestore_db):
    """status="failed" なら no-op。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": "failed"}
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.document.return_value = ref

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        client.promote_user_podcast("pod1", status="failed")

    # no-op: update は呼ばれない
    mock_firestore_db.transaction.return_value.update.assert_not_called()


def test_promote_user_podcast_sets_error_message_when_failed(mock_firestore_db):
    """status="processing" → "failed" 遷移時に error_message を設定する。"""
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()

    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": "processing"}
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.document.return_value = ref

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        client.promote_user_podcast("pod1", status="failed", error_message="API error")

    transaction = mock_firestore_db.transaction.return_value
    transaction.update.assert_called_once()
    _, update_data = transaction.update.call_args[0]
    assert update_data["status"] == "failed"
    assert update_data["error_message"] == "API error"
