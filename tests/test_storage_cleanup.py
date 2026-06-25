"""純粋関数（ロジック）のテスト：is_blob_deletable / select_podcasts_to_delete。"""
from datetime import datetime, timezone, timedelta

from shared.models import Podcast


# ---------- T3a: is_blob_deletable ----------


def test_is_blob_deletable_digest_per_user():
    """type='digest' で per-user blob（audio_url が podcasts/cache/ でない）→ True。"""
    from api.storage_cleanup import is_blob_deletable

    podcast = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1", "art2"],
        difficulty="toeic_900",
        audio_url="podcasts/user123/digest_20260625.mp3",
        japanese_intro_text="ダイジェスト",
        duration_seconds=600,
        status="completed",
        created_at=datetime.now(timezone.utc),
        user_id="user1",
    )
    assert is_blob_deletable(podcast) is True


def test_is_blob_deletable_single_shared_cache():
    """type='single' で shared cache（audio_url が podcasts/cache/）→ False（削除禁止）。"""
    from api.storage_cleanup import is_blob_deletable

    podcast = Podcast(
        id="pod1",
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/cache/art123__toeic_900__ja-en.mp3",
        japanese_intro_text="イントロ",
        duration_seconds=300,
        status="completed",
        created_at=datetime.now(timezone.utc),
        user_id="user1",
    )
    assert is_blob_deletable(podcast) is False


def test_is_blob_deletable_single_legacy():
    """type='single' で legacy per-user（audio_url が podcasts/{id}/{diff}.mp3）→ False。"""
    from api.storage_cleanup import is_blob_deletable

    podcast = Podcast(
        id="pod_abc",
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/pod_abc/toeic_900.mp3",
        japanese_intro_text="イントロ",
        duration_seconds=300,
        status="completed",
        created_at=datetime.now(timezone.utc),
        user_id="user1",
    )
    assert is_blob_deletable(podcast) is False


def test_is_blob_deletable_digest_shared_cache():
    """type='digest' でも audio_url が shared cache prefix → False（保護）。"""
    from api.storage_cleanup import is_blob_deletable

    podcast = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/cache/digest_shared.mp3",
        japanese_intro_text="共有ダイジェスト",
        duration_seconds=300,
        status="completed",
        created_at=datetime.now(timezone.utc),
        user_id="user1",
    )
    assert is_blob_deletable(podcast) is False


def test_is_blob_deletable_empty_audio_url():
    """audio_url が空文字 → False（processing行など）。"""
    from api.storage_cleanup import is_blob_deletable

    podcast = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="",
        japanese_intro_text="",
        duration_seconds=0,
        status="processing",
        created_at=datetime.now(timezone.utc),
        user_id="user1",
    )
    assert is_blob_deletable(podcast) is False


# ---------- T3b: select_podcasts_to_delete ----------


def test_select_podcasts_to_delete_all_when_none():
    """older_than_days=None → 全件返す。"""
    from api.storage_cleanup import select_podcasts_to_delete

    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=100),
        user_id="user1",
    )
    pod2 = Podcast(
        id="pod2",
        type="digest",
        article_ids=["art2"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest2.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=1),
        user_id="user1",
    )

    result = select_podcasts_to_delete([pod1, pod2], older_than_days=None, now=now)
    assert len(result) == 2


def test_select_podcasts_to_delete_filters_by_days():
    """older_than_days=10 → created_at < now - 10日 のみ。"""
    from api.storage_cleanup import select_podcasts_to_delete

    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=11),  # 11日前 → 含める
        user_id="user1",
    )
    pod2 = Podcast(
        id="pod2",
        type="digest",
        article_ids=["art2"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest2.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=10),  # ちょうど10日前 → 含めない（< 厳密）
        user_id="user1",
    )
    pod3 = Podcast(
        id="pod3",
        type="digest",
        article_ids=["art3"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest3.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=9),  # 9日前 → 含めない
        user_id="user1",
    )

    result = select_podcasts_to_delete([pod1, pod2, pod3], older_than_days=10, now=now)
    assert len(result) == 1
    assert result[0].id == "pod1"


def test_select_podcasts_to_delete_zero_days_includes_all():
    """older_than_days=0 → created_at < now - 0日 = created_at < now（ほぼ全件）。"""
    from api.storage_cleanup import select_podcasts_to_delete

    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(seconds=1),  # 1秒前 → < now
        user_id="user1",
    )
    pod2 = Podcast(
        id="pod2",
        type="digest",
        article_ids=["art2"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest2.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now,  # ちょうど now → < で exclude
        user_id="user1",
    )

    result = select_podcasts_to_delete([pod1, pod2], older_than_days=0, now=now)
    assert len(result) == 1
    assert result[0].id == "pod1"


def test_select_podcasts_to_delete_empty_list():
    """空リスト → 空を返す。"""
    from api.storage_cleanup import select_podcasts_to_delete

    now = datetime.now(timezone.utc)
    result = select_podcasts_to_delete([], older_than_days=10, now=now)
    assert result == []
