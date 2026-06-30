"""FastAPI 依存性注入の定義。

FirestoreClient はリクエストごとの再生成を避けるために lru_cache でシングルトン化する。
gRPC コネクションプールの初期化コストはリクエストあたり数十〜数百ms であるため、
ウォームスタートでは同一インスタンスを使い回す。

USER_ID は環境変数から取得し、未設定の場合は HTTP 500 で即座に失敗させる。
サイレントなデフォルト値("default")によるデータ混在バグを防ぐため。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
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
    session_id = hash_token(token)
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _touch_last_used(db, session_id, session)
    return session


# last_used_at のスロットル間隔（秒）。既定 5 分。0 以下で毎リクエスト更新。
_DEFAULT_LASTUSED_THROTTLE_SECONDS = 300


def _touch_last_used(db: FirestoreClient, session_id: str, session: Session) -> None:
    """セッション一覧 UI 用に last_used_at を更新する（issue #84）。

    認証経路は高頻度なので、最後の更新から一定時間（SESSION_LASTUSED_THROTTLE_SECONDS、
    既定 300 秒）経過した場合のみ書き込み、Firestore 書き込みコストを抑える。
    get_session で取得済みの session を使うため追加の読み取りは発生しない。

    ベストエフォート: 認証は決して妨げない。書き込み失敗だけでなく、スロットル判定の
    datetime 演算（万一 last_used_at が naive datetime のレガシードキュメントだった場合の
    aware-naive 減算 TypeError など）も含めて関数全体を握り、認証経路の 500 化を防ぐ。
    """
    import os

    try:
        now = datetime.now(timezone.utc)
        raw = os.environ.get("SESSION_LASTUSED_THROTTLE_SECONDS")
        try:
            throttle = int(raw) if raw else _DEFAULT_LASTUSED_THROTTLE_SECONDS
        except ValueError:
            throttle = _DEFAULT_LASTUSED_THROTTLE_SECONDS

        last = session.last_used_at
        if last is not None and (now - last).total_seconds() < throttle:
            return
        db.update_session_last_used(session_id, now)
    except Exception:  # noqa: BLE001 - ベストエフォート（認証は継続）
        _logger.warning("failed to update session last_used_at", exc_info=True)


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


@lru_cache(maxsize=1)
def get_webauthn_config():
    """WebAuthnConfig のシングルトンを返す。

    WEBAUTHN_RP_ID が未設定の場合は None を返す。
    passkey ルーターは None を受け取ったら 503 を返す。
    lru_cache によりプロセス内で同一インスタンスを再利用し、
    テストでは cache_clear() → dependency_overrides で差し替える。
    """
    import os

    from shared.webauthn_config import WebAuthnConfig

    return WebAuthnConfig.from_env(os.environ)
