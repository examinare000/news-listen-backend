"""StorageClient のユニットテスト。"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


@contextmanager
def _mock_adc(service_account_email="news-listen-sa@example.iam.gserviceaccount.com",
              token="ya29.access-token"):
    """google.auth.default() をモックし、Cloud Run の SA 認証情報を模す。

    Cloud Run のコンピュート認証情報は秘密鍵を持たないため、署名には
    IAM signBlob API（service_account_email + access_token）が必要になる。
    """
    creds = MagicMock()
    creds.service_account_email = service_account_email
    creds.token = token
    with patch("shared.storage_client.google_auth_default", return_value=(creds, "proj")), \
         patch("shared.storage_client.AuthRequest"):
        yield creds


def test_generate_audio_url_creates_signed_url():
    """generate_audio_url() が blob パスから署名付き URL を生成すること。"""
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class, \
         _mock_adc():

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


def test_generate_audio_url_uses_iam_signing_on_cloud_run():
    """Cloud Run の SA 認証情報は秘密鍵を持たないため、generate_signed_url() は
    IAM signBlob 方式（service_account_email + access_token）で呼ばれること。

    引数なしの blob.generate_signed_url() は "you need a private key" で 500 になるため、
    ADC から取得した access_token と service_account_email を必ず渡す。
    """
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class, \
         _mock_adc(service_account_email="sa@example.iam.gserviceaccount.com",
                   token="ya29.token123") as creds:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        client.generate_audio_url("podcasts/pod1/toeic_900.mp3")

        # ADC をリフレッシュしてトークンを取得していること
        creds.refresh.assert_called_once()

        # IAM signBlob に必要な引数が渡されていること
        _, kwargs = mock_blob.generate_signed_url.call_args
        assert kwargs["service_account_email"] == "sa@example.iam.gserviceaccount.com"
        assert kwargs["access_token"] == "ya29.token123"


def test_generate_audio_url_fails_fast_when_credentials_cannot_sign():
    """SA でない ADC（service_account_email / token を持たない）の場合は、
    曖昧な AttributeError ではなく明確なエラーで早期に失敗すること。

    Cloud Run 以外（ユーザー ADC 等）で誤って動かした際に原因を即座に把握できるようにする。
    """
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class, \
         _mock_adc(service_account_email=None, token=None):

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()

        with pytest.raises(RuntimeError, match="service_account_email"):
            client.generate_audio_url("podcasts/pod1/toeic_900.mp3")

        # 署名は試行されないこと
        mock_blob.generate_signed_url.assert_not_called()


# ---------- #50: upload_cached_audio は再生可能な WAV を配信する ----------


def test_upload_cached_audio_wraps_pcm_as_wav():
    """生 PCM を WAV コンテナ化し、audio/wav・.wav blob で配信すること（#50）。

    従来は生 PCM を content_type="audio/mpeg"・.mp3 で配信していたため
    プレイヤーがデコードできず再生不可だった。RIFF/WAVE ヘッダを付与し、
    正しい MIME と拡張子で配信することを検証する。
    """
    import struct

    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()

        pcm = struct.pack("<100h", *range(100))
        blob_name = client.upload_cached_audio("abc123", pcm)

        # blob 名・戻り値は .wav 拡張子
        assert blob_name == "podcasts/cache/abc123.wav"
        mock_client.bucket.return_value.blob.assert_called_with(
            "podcasts/cache/abc123.wav"
        )

        # アップロードは WAV バイト列・audio/wav で行われる
        args, kwargs = mock_blob.upload_from_string.call_args
        uploaded = args[0]
        assert uploaded[:4] == b"RIFF"
        assert uploaded[8:12] == b"WAVE"
        assert kwargs["content_type"] == "audio/wav"


# ---------- T1: get_blob_size / delete_blob ----------


def test_get_blob_size_returns_blob_size():
    """get_blob_size(blob_name) が GCS blob のサイズ（バイト）を返すこと。"""
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_blob.size = 12345
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        size = client.get_blob_size("podcasts/pod1/audio.mp3")

        assert size == 12345
        mock_client.bucket.return_value.blob.assert_called_with("podcasts/pod1/audio.mp3")
        mock_blob.reload.assert_called_once()


def test_get_blob_size_returns_0_when_blob_not_found():
    """blob が存在しない場合（NotFound）、0 を返す（例外を上げない）。"""
    from google.cloud.exceptions import NotFound

    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_blob.reload.side_effect = NotFound("not found")
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        size = client.get_blob_size("missing/blob.mp3")

        assert size == 0


def test_get_blob_size_returns_0_for_empty_blob_name():
    """blob_name が空文字の場合、GCS を叩かず 0 を返す。"""
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        from shared.storage_client import StorageClient
        client = StorageClient()
        size = client.get_blob_size("")

        assert size == 0
        # GCS に一度も接触しないこと
        mock_client.bucket.assert_not_called()


def test_get_blob_size_returns_0_on_other_errors():
    """blob 取得時に他のエラー（例：403 Forbidden）が起きても 0 を返す。"""
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_blob.reload.side_effect = Exception("access denied")
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        size = client.get_blob_size("forbidden/blob.mp3")

        assert size == 0


def test_delete_blob_calls_delete():
    """delete_blob(blob_name) が blob.delete() を呼び出すこと。"""
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        client.delete_blob("podcasts/pod1/audio.mp3")

        mock_client.bucket.return_value.blob.assert_called_with("podcasts/pod1/audio.mp3")
        mock_blob.delete.assert_called_once()


def test_delete_blob_noop_when_blob_not_found():
    """blob が存在しない場合（NotFound）、例外を上げず処理を続行する（冪等）。"""
    from google.cloud.exceptions import NotFound

    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_blob.delete.side_effect = NotFound("not found")
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        # 例外を上げないこと
        client.delete_blob("missing/blob.mp3")


def test_delete_blob_noop_on_other_errors():
    """delete 時に他のエラー（例：403 Forbidden）が起きても例外を握り潰す。"""
    with patch.dict("os.environ", {"GCS_BUCKET_NAME": "test-bucket"}), \
         patch("shared.storage_client.storage.Client") as mock_client_class:

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_blob = MagicMock()
        mock_blob.delete.side_effect = Exception("access denied")
        mock_client.bucket.return_value.blob.return_value = mock_blob

        from shared.storage_client import StorageClient
        client = StorageClient()
        # 例外を握り潰す（best-effort）
        client.delete_blob("forbidden/blob.mp3")
