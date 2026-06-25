"""FastAPI 依存性注入の定義。

FirestoreClient はリクエストごとの再生成を避けるために lru_cache でシングルトン化する。
gRPC コネクションプールの初期化コストはリクエストあたり数十〜数百ms であるため、
ウォームスタートでは同一インスタンスを使い回す。

USER_ID は環境変数から取得し、未設定の場合は HTTP 500 で即座に失敗させる。
サイレントなデフォルト値("default")によるデータ混在バグを防ぐため。
"""
from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status

from shared.firestore_client import FirestoreClient
from shared.models import Session
from shared.security import hash_token
from shared.storage_client import StorageClient

_logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_storage_client() -> StorageClient:
    """StorageClient のシングルトンを返す。

    lru_cache により同一プロセス内で同じインスタンスを再利用する。
    signed URL 生成のたびに新インスタンスを作るコストを避けるため。
    """
    return StorageClient()


@lru_cache(maxsize=1)
def get_firestore_client() -> FirestoreClient:
    """FirestoreClient のシングルトンを返す。

    lru_cache により同一プロセス内で同じインスタンスを再利用する。
    Cloud Run のコールドスタート時のみ新規に初期化される。
    """
    return FirestoreClient()


@lru_cache(maxsize=1)
def get_job_trigger():
    """JobTrigger のシングルトンを返す。

    JOB_TRIGGER_BACKEND 環境変数で起動経路（Cloud Run / ローカルサブプロセス）を切り替える。
    debounce ロックに FirestoreClient を共有するため get_firestore_client() を注入する。
    """
    # 遅延 import: モジュール読み込み時の循環依存を避ける。
    from shared.job_trigger import build_job_trigger

    return build_job_trigger(get_firestore_client())


# 認証トークンを格納する Cookie 名。Web はこの httpOnly Cookie で、iOS は
# Authorization: Bearer ヘッダーで同一トークンを送る（dual transport）。
SESSION_COOKIE_NAME = "nl_session"


def _extract_session_token(request: Request) -> str | None:
    """リクエストからセッショントークンを取り出す。

    優先順: Authorization: Bearer（iOS など）→ Cookie nl_session（Web）。
    """
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[len("bearer ") :].strip()
        if token:
            return token
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    return cookie or None


def get_client_ip(request: Request) -> str:
    """リクエスト元 IP を取得する。

    優先順: X-Forwarded-For ヘッダの左端（ロードバランサー / プロキシ経由時）
    → request.client.host（直接接続時）。

    X-Forwarded-For は詐称可能だが、本番環境で Cloud Run / Cloud Armor
    経由であれば信頼できる。username 単位のレート制限と併用して多層防御を実施する。
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        # 複数 IP が含まれる場合は左端（元クライアント）を採用
        return xff.split(",")[0].strip()
    # フォールバック: request.client が None でも、エラーを避ける
    if request.client:
        return request.client.host
    return "unknown"


def get_current_user(
    request: Request,
    db: FirestoreClient = Depends(get_firestore_client),
) -> Session:
    """有効なセッションを解決して返す。未認証・期限切れは HTTP 401。

    生トークンは SHA-256 ハッシュに変換してから DB を引く（security.hash_token）。
    エラー詳細にはトークンや内部情報を含めない。
    """
    token = _extract_session_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    session = db.get_session(hash_token(token))
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return session


def get_user_id(current: Session = Depends(get_current_user)) -> str:
    """現在ログイン中ユーザーの user_id（データパーティションキー）を返す。

    旧来は環境変数 USER_ID 固定だったが、ログインセッション由来へ変更した。
    既存ルーターは Depends(get_user_id) のまま無改修でマルチユーザー化される。
    """
    return current.user_id


def require_admin(current: Session = Depends(get_current_user)) -> Session:
    """admin ロールを要求する。非 admin は HTTP 403。"""
    if current.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current


@lru_cache(maxsize=1)
def get_audit_logger():
    """AuditLogger のシングルトンを返す。

    FirestoreClient を依存として注入し、ベストエフォート設計で監査ログを記録する。
    テストで時刻を制御可能にするため clock 関数の依存性注入を設計している。
    """
    # 遅延 import: AuditLogger の循環依存を避ける
    from api.audit import AuditLogger

    return AuditLogger(firestore_client=get_firestore_client())


@lru_cache(maxsize=1)
def get_email_sender():
    """EmailSender のシングルトンを返す。

    環境変数から SMTP 設定を読み込み、揃っていれば SmtpEmailSender、
    未設定なら NoOpEmailSender を返す（テスト・ローカル安全）。
    """
    import os

    from shared.email_sender import build_email_sender

    return build_email_sender(os.environ)
