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
