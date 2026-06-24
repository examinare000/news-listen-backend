"""汎用 API レート制限ミドルウェア。

ADR-016 / ADR-018 流の純関数ポリシー + 薄い依存関数シェル。
2層構成（user 軸 + IP 軸）で多角的に制御する。
"""
import logging
import os
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status

from api.dependencies import get_client_ip, get_firestore_client
from shared.firestore_client import FirestoreClient
from shared.security import hash_token

_logger = logging.getLogger(__name__)

# 既定レート制限テーブル（bucket -> (max_requests, window_seconds)）
_DEFAULTS = {
    "api": (120, 60),
    "star": (10, 3600),
}


def _env_int(name: str, default: int, minimum: int) -> int:
    """環境変数を整数として読む。未設定・不正値は default、minimum 未満は minimum に丸める。"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def evaluate_rate_limit(
    allowed_user: bool,
    retry_user: int,
    allowed_ip: bool,
    retry_ip: int,
) -> tuple[bool, int]:
    """user/ip 軸を組み合わせるポリシー関数（DB 非依存）。

    いずれかの軸が NotAllowed なら全体 NotAllowed。
    retry_after は NotAllowed な軸のうち最大値。

    Args:
        allowed_user: user 軸で許可したか
        retry_user: user 軸の retry_after（allowed_user=True の場合は 0）
        allowed_ip: IP 軸で許可したか
        retry_ip: IP 軸の retry_after（allowed_ip=True の場合は 0）

    Returns:
        (allowed, retry_after)
    """
    if not allowed_user or not allowed_ip:
        # いずれかが NotAllowed → 全体 NotAllowed
        retry_after = max(retry_user, retry_ip)
        return False, retry_after

    # 両方 allowed
    return True, 0


def _resolve_user_id_optional(request: Request, db: FirestoreClient) -> str | None:
    """セッショントークンから user_id を解決する（例外安全）。

    トークン未検出・無効・期限切れ・内部エラーを全て None で返す。
    トークン詳細やエラーをログに出さない。

    Args:
        request: HTTPRequest
        db: FirestoreClient

    Returns:
        user_id or None
    """
    from api.dependencies import _extract_session_token

    try:
        token = _extract_session_token(request)
        if not token:
            return None
        session = db.get_session(hash_token(token))
        if session is None:
            return None
        return session.user_id
    except Exception:
        # 内部エラー（DB 障害等）も安全に None で返す
        return None


def rate_limit(bucket: str):
    """レート制限の依存関数ファクトリ。

    各 bucket（"api", "star" 等）について、max_requests と window_seconds を
    環境変数から読み込んで制御する。

    max_requests <= 0 のとき無効化（no-op、DB アクセス無し）。

    Args:
        bucket: バケット名（既定値テーブル _DEFAULTS のキー）

    Returns:
        依存関数 async def _rate_limit_dep(...)
    """

    async def _rate_limit_dep(
        request: Request,
        db: FirestoreClient = Depends(get_firestore_client),
    ) -> None:
        """リクエストごとにレート制限チェック。

        環境変数をリクエスト毎に読み込むため、再スタート無しに設定変更可能。
        """
        # リクエスト毎に env を読む
        defaults = _DEFAULTS.get(bucket)
        if defaults is None:
            # 未知の bucket: 警告してスキップ
            _logger.warning(f"Unknown rate limit bucket: {bucket}")
            return

        default_max, default_window = defaults
        max_requests = _env_int(
            f"{bucket.upper()}_RATELIMIT_MAX_REQUESTS", default_max, 0
        )
        window_seconds = _env_int(
            f"{bucket.upper()}_RATELIMIT_WINDOW_SECONDS", default_window, 1
        )

        # max_requests <= 0 で無効化
        if max_requests <= 0:
            return

        # IP と user の両軸を計算
        now = datetime.now(timezone.utc)
        ip = get_client_ip(request)
        ip_key = f"{bucket}:ip:" + hash_token(ip)

        user_id = _resolve_user_id_optional(request, db)
        user_key = f"{bucket}:user:{user_id}" if user_id else None

        # IP 軸のレート制限
        allowed_ip, retry_ip = db.consume_rate_limit(
            ip_key, now=now, max_requests=max_requests, window_seconds=window_seconds
        )

        # User 軸のレート制限（user_id が解決できれば）
        if user_key:
            allowed_user, retry_user = db.consume_rate_limit(
                user_key,
                now=now,
                max_requests=max_requests,
                window_seconds=window_seconds,
            )
        else:
            # user_id 未解決なら user 軸は allowed（IP 軸のみ効く）
            allowed_user, retry_user = True, 0

        # ポリシー統合
        allowed, retry_after = evaluate_rate_limit(
            allowed_user, retry_user, allowed_ip, retry_ip
        )

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    return _rate_limit_dep
