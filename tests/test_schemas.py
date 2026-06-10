"""api.schemas の単体テスト。"""
from datetime import datetime, timezone

from shared.models import Podcast
from api.schemas import PodcastResponse


NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


def _make_podcast(**overrides) -> Podcast:
    defaults = dict(
        id="pod1",
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/pod1/toeic_900.mp3",
        japanese_intro_text="日本語イントロ",
        duration_seconds=300,
        status="completed",
        created_at=NOW,
        user_id="user1",
    )
    defaults.update(overrides)
    return Podcast(**defaults)


def test_podcast_response_from_podcast_classmethod_exists():
    """PodcastResponse.from_podcast() クラスメソッドが存在すること。"""
    assert hasattr(PodcastResponse, "from_podcast"), (
        "PodcastResponse.from_podcast() classmethod が未実装。"
        "list_podcasts / get_podcast の重複コードを解消するために必要。"
    )


def test_podcast_response_from_podcast_maps_all_fields():
    """from_podcast() が Podcast モデルの全フィールドを正しくマッピングすること。"""
    podcast = _make_podcast()
    response = PodcastResponse.from_podcast(podcast)

    assert response.id == "pod1"
    assert response.type == "single"
    assert response.article_ids == ["art1"]
    assert response.difficulty == "toeic_900"
    assert response.audio_url == "podcasts/pod1/toeic_900.mp3"
    assert response.japanese_intro_text == "日本語イントロ"
    assert response.duration_seconds == 300
    assert response.created_at == NOW.isoformat()


def test_podcast_response_from_podcast_preserves_created_at_as_iso():
    """created_at は ISO 8601 文字列に変換されること。"""
    podcast = _make_podcast()
    response = PodcastResponse.from_podcast(podcast)
    assert response.created_at == "2026-05-31T12:00:00+00:00"


def test_podcast_response_from_podcast_uses_audio_url_override():
    """audio_url 引数が指定された場合は Podcast.audio_url の代わりに使用されること。

    API ルーターは GCS blob path を StorageClient.generate_audio_url() で
    署名付き URL に変換してから from_podcast() に渡す設計。
    from_podcast() は渡された audio_url を優先する必要がある。
    """
    podcast = _make_podcast()
    signed_url = "https://storage.googleapis.com/bucket/signed-url-xyz"
    response = PodcastResponse.from_podcast(podcast, audio_url=signed_url)
    assert response.audio_url == signed_url
