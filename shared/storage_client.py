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

    def upload_cached_audio(self, cache_key: str, audio_bytes: bytes) -> str:
        """共有キャッシュ用音声を Cloud Storage にアップロードし、GCS blob パスを返す。

        決定論的パス podcasts/cache/{cache_key}.mp3 を使用するため、
        複数ジョブが同一 cache_key でアップロードしても同一 blob に収束する（べき等）。
        make_public() は呼ばない — 共有キャッシュでも GCS オブジェクトを永続公開しない。
        アクセスは generate_audio_url() 経由の署名付き URL に限定する。

        Returns:
            GCS blob パス（例: "podcasts/cache/{cache_key}.mp3"）
        """
        bucket = self._client.bucket(self._bucket_name)
        blob_name = f"podcasts/cache/{cache_key}.mp3"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(audio_bytes, content_type="audio/mpeg")
        return blob_name

    def generate_audio_url(self, blob_name: str, expiration_seconds: int = 3600) -> str:
        """GCS blob パスから有効期限付きの署名付き URL を生成する。

        Args:
            blob_name: GCS blob パス
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

        # IAM signBlob には SA メールとアクセストークンが必須。ユーザー ADC など
        # SA でない認証情報では service_account_email が存在せず、そのまま渡すと
        # 曖昧な AttributeError になる。原因が分かる明確なエラーで早期に失敗させる。
        service_account_email = getattr(credentials, "service_account_email", None)
        token = getattr(credentials, "token", None)
        if not service_account_email or not token:
            raise RuntimeError(
                "署名付きURLの生成には service_account_email と access_token を持つ"
                "サービスアカウント認証情報が必要です。Cloud Run のSA・SA鍵・"
                "インパーソネーションのいずれかで実行してください。"
            )

        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expiration_seconds),
            service_account_email=service_account_email,
            access_token=token,
        )

