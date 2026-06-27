"""セッション発行ロジック。

auth.py のログイン処理から抽出した、セッショントークン発行・Cookie セット・
ローテーションを担う薄いサービス層。監査ログの記録は呼び出し側の責務。

WHY: passkey ログインでも同じセッション発行フローが必要なため、重複を排除するために
     共通化した。auth.py の既存動作を完全に保持しつつ再利用可能にする。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response

from api.dependencies import SESSION_COOKIE_NAME, _extract_session_token
from api.middleware.csrf import generate_csrf_token
from shared.firestore_client import FirestoreClient
from shared.models import Session, User
from shared.security import generate_session_token, hash_token

# セッション有効期間（時間）。未設定時は 7 日（auth.py と同値）。
_DEFAULT_SESSION_TTL_HOURS = 168


def _env_int(name: str, default: int, minimum: int) -> int:
    """環境変数を整数として読む。未設定・不正値は default、minimum 未満は minimum に丸める。"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _session_ttl_hours() -> int:
    return _env_int("SESSION_TTL_HOURS", _DEFAULT_SESSION_TTL_HOURS, 1)


def _cookie_secure() -> bool:
    """Cookie の Secure 属性。ローカル http 開発では SESSION_COOKIE_SECURE=false で無効化する。"""
    return os.environ.get("SESSION_COOKIE_SECURE", "true").lower() != "false"


def _set_csrf_cookie(response: Response) -> None:
    """CSRF double-submit 用の csrf_token Cookie を発行する。

    WHY: 非 httpOnly（JS が読んで X-CSRF-Token ヘッダに載せる）・SameSite=lax・セッションと
         同じ TTL/Secure 属性。login と /auth/me 補填で同一属性を保証するため一箇所に集約する。
    """
    response.set_cookie(
        key="csrf_token",
        value=generate_csrf_token(),
        max_age=_session_ttl_hours() * 3600,
        httponly=False,  # JS から読める（CSRF double-submit 必須）
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def issue_session(
    db: FirestoreClient,
    user: User,
    request: Request,
    response: Response,
    ip: str,
) -> tuple[str, Session]:
    """セッションを発行し、Cookie をセットして返す。

    1. 既存トークンがあれば旧セッションを失効（固定化対策）
    2. 新しいセッショントークンを生成して DB に保存
    3. nl_session Cookie（httpOnly）と csrf_token Cookie をセット
    4. (生トークン, Session) を返す

    監査ログは呼び出し側で記録する（本関数には含めない）。

    Args:
        db: FirestoreClient インスタンス
        user: ログイン対象ユーザー
        request: FastAPI Request（既存トークン抽出に使用）
        response: FastAPI Response（Cookie セットに使用）
        ip: クライアント IP（セッションには保存しない、呼び出し側の監査に使う）

    Returns:
        (生トークン文字列, Session オブジェクト)
    """
    # セッションローテーション: 既存トークン提示時は新発行前に旧セッションを失効（固定化対策・冪等）。
    old_token = _extract_session_token(request)
    if old_token:
        db.delete_session(hash_token(old_token))

    token = generate_session_token()
    ttl_hours = _session_ttl_hours()
    now = datetime.now(timezone.utc)
    session = Session(
        session_id=hash_token(token),
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        created_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
    )
    db.create_session(session)

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=ttl_hours * 3600,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )

    # CSRF double-submit cookie をセッションと同時に発行する。
    _set_csrf_cookie(response)

    return token, session
