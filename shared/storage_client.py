"""Cloud Storage upload/download helpers."""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as AuthRequest
from google.cloud import storage
from google.cloud.exceptions import NotFound

logger = logging.getLogger(__name__)


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

        # スコープを明示する。SA 鍵ファイルを ADC として読む環境（ローカル / Docker）では
        # google.auth.default() がスコープ未設定の認証情報を返し、refresh が空スコープの
        # JWT grant を送って "invalid_scope" で失敗するため。cloud-platform スコープは
        # IAM signBlob と Storage 双方をカバーし、Cloud Run のコンピュート認証情報でも無害。
        credentials, _ = google_auth_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
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

    def get_blob_size(self, blob_name: str) -> int:
        """GCS blob のサイズ（バイト）を取得する。不在・エラー時は 0 を返す（read 安全）。

        Args:
            blob_name: GCS blob パス。空文字の場合は GCS を叩かず 0 を返す。

        Returns:
            blob のサイズ（バイト）。blob 不在・エラー時は 0。
        """
        if not blob_name:
            return 0

        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(blob_name)
        try:
            blob.reload()
            return blob.size if blob.size is not None else 0
        except NotFound:
            return 0
        except Exception as e:
            # その他のエラー（権限不足など）も 0 を返す（best-effort）。
            # サイレント 0 で使用量が過小報告されうるため warning で可観測化する。
            logger.warning("get_blob_size failed for %s: %s", blob_name, e)
            return 0

    def delete_blob(self, blob_name: str) -> bool:
        """GCS blob を削除する。冪等・不在でも例外を上げない。

        Returns:
            実際に削除できたら True、不在・失敗（権限不足など）なら False。
            呼び出し側は戻り値で「解放できたか」を正直に集計できる（best-effort）。
        """
        try:
            bucket = self._client.bucket(self._bucket_name)
            blob = bucket.blob(blob_name)
            blob.delete()
            return True
        except NotFound:
            # blob が存在しない → 既に削除されている → 解放はしていない
            return False
        except Exception as e:
            # その他のエラー（権限不足など）は握り潰すが（best-effort・処理継続）、
            # 削除失敗を成功と誤認しないよう False を返し warning で可観測化する。
            logger.warning("delete_blob failed for %s: %s", blob_name, e)
            return False

