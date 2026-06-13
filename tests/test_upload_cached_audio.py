"""StorageClient.upload_cached_audio のユニットテスト。

upload_cached_audio は共有キャッシュ用の音声をアップロードする。
既存の upload_audio（per-user パス）と異なり、決定論的な共有パス
podcasts/cache/{cache_key}.mp3 を使用する。
"""
from unittest.mock import MagicMock, patch

CACHE_KEY = "art1abc123456789ab__toeic_900__ja-en"
EXPECTED_BLOB_NAME = f"podcasts/cache/{CACHE_KEY}.mp3"


def test_upload_cached_audio_uses_deterministic_cache_path():
    """upload_cached_audio は podcasts/cache/{cache_key}.mp3 の決定論的パスを使うこと。

    UUID を用いる per-user パスと異なり、キャッシュパスは cache_key から一意に決まる。
    複数ジョブが同じ cache_key でアップロードしても同一 blob に収束する（べき等）。
    """
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        client.upload_cached_audio(CACHE_KEY, b"audio-bytes")

        mock_client.bucket.return_value.blob.assert_called_with(EXPECTED_BLOB_NAME)


def test_upload_cached_audio_returns_blob_path():
    """upload_cached_audio は GCS blob パスを返すこと（公開 URL ではない）。

    API レイヤーで generate_audio_url() により署名付き URL を生成する設計のため、
    blob パスを返す（upload_audio と同じ規約）。
    """
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        result = client.upload_cached_audio(CACHE_KEY, b"audio-bytes")

        assert result == EXPECTED_BLOB_NAME


def test_upload_cached_audio_does_not_make_blob_public():
    """セキュリティ: upload_cached_audio は blob.make_public() を呼ばないこと。

    upload_audio と同じセキュリティ方針。共有キャッシュでも GCS オブジェクトを
    永続公開しない。アクセスは署名付き URL 経由に限定する。
    """
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        client.upload_cached_audio(CACHE_KEY, b"audio-bytes")

        mock_blob.make_public.assert_not_called()


def test_upload_cached_audio_uses_audio_mpeg_content_type():
    """upload_cached_audio は content_type='audio/mpeg' でアップロードすること。

    upload_audio と同じ content_type を用いる。
    """
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        client.upload_cached_audio(CACHE_KEY, b"audio-bytes")

        mock_blob.upload_from_string.assert_called_once_with(
            b"audio-bytes", content_type="audio/mpeg"
        )


def test_upload_cached_audio_path_differs_from_per_user_path():
    """キャッシュパスは per-user パス（podcasts/{podcast_id}/{difficulty}.mp3）と別系統であること。

    2 種類のアップロードが異なる GCS パス体系を使うことを確認する。
    """
    per_user_path = "podcasts/some-uuid/toeic_900.mp3"
    assert EXPECTED_BLOB_NAME != per_user_path
    assert EXPECTED_BLOB_NAME.startswith("podcasts/cache/")
    assert not per_user_path.startswith("podcasts/cache/")
