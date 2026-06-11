"""Cloud Storage upload/download helpers."""
from __future__ import annotations

import os
from datetime import timedelta

from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as AuthRequest
from google.cloud import storage


class StorageClient:
    def __init__(self) -> None:
        self._client = storage.Client()
        self._bucket_name = os.environ["GCS_BUCKET_NAME"]

    def upload_audio(self, podcast_id: str, difficulty: str, audio_bytes: bytes) -> str:
        """音声データを Cloud Storage にアップロードし、GCS blob パスを返す。

        セキュリティ上の理由で blob を公開設定にしない。
        再生 URL が必要な場合は generate_audio_url() を使用すること。

        Returns:
            GCS blob パス（例: "podcasts/{podcast_id}/{difficulty}.mp3"）
        """
        bucket = self._client.bucket(self._bucket_name)
        blob_name = f"podcasts/{podcast_id}/{difficulty}.mp3"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(audio_bytes, content_type="audio/mpeg")
        # make_public() は意図的に呼ばない。ユーザー固有データを永続公開しない。
        return blob_name

    def generate_audio_url(self, blob_name: str, expiration_seconds: int = 3600) -> str:
        """GCS blob パスから有効期限付きの署名付き URL を生成する。

        Args:
            blob_name: GCS blob パス（upload_audio() の戻り値）
            expiration_seconds: URL の有効期限（デフォルト 1 時間）

        Cloud Run のサービスアカウント認証情報（コンピュート認証情報）は秘密鍵を
        持たないため、引数なしの generate_signed_url() は
        "you need a private key to sign credentials" で失敗する。
        ADC から取得したアクセストークンと SA メールアドレスを渡し、
        IAM signBlob API 経由で署名する（SA に roles/iam.serviceAccountTokenCreator が必要）。
        """
        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(blob_name)

        credentials, _ = google_auth_default()
        # service_account_email / token は refresh 後に確定する
        credentials.refresh(AuthRequest())

        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expiration_seconds),
            service_account_email=credentials.service_account_email,
            access_token=credentials.token,
        )

