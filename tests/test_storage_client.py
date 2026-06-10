"""StorageClient のユニットテスト。"""
from unittest.mock import MagicMock, patch


def test_upload_audio_does_not_make_blob_public():
    """セキュリティ: upload_audio() は blob.make_public() を呼ばないこと。
    ユーザー固有の音声データを誰でもアクセス可能な URL で公開してはならない。
    """
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        result = client.upload_audio("pod1", "toeic_900", b"audio-bytes")

        mock_blob.make_public.assert_not_called()


def test_upload_audio_returns_blob_path_not_public_url():
    """upload_audio() は GCS blob パス（"podcasts/..."）を返すこと。
    公開 URL ではなくパスを返し、API レイヤーで署名付き URL を生成させる。
    """
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        result = client.upload_audio("pod1", "toeic_900", b"audio-bytes")

        # GCS blob パス形式であること（公開 URL ではない）
        assert result == "podcasts/pod1/toeic_900.mp3"


def test_generate_audio_url_creates_signed_url():
    """generate_audio_url() が blob パスから署名付き URL を生成すること。"""
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = "https://signed.url/audio.mp3"
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        url = client.generate_audio_url("podcasts/pod1/toeic_900.mp3")

        mock_client.bucket.return_value.blob.assert_called_with("podcasts/pod1/toeic_900.mp3")
        mock_blob.generate_signed_url.assert_called_once()
        assert url == "https://signed.url/audio.mp3"
