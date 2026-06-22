"""ログイン・ログアウト・セルフサービス（自分のパスワード変更／プロフィール編集）。

認証方式はサーバーサイドセッション。ログイン成功時に推測不能なトークンを発行し、
その SHA-256 ハッシュを Firestore `sessions` に保存する（生トークンは保存しない）。
トークンは Web 向けに httpOnly Cookie（Set-Cookie）で、iOS 向けにレスポンス body の
`token` で返す（dual transport）。

セキュリティ（`agent-rules/12-security-guidelines.md`）:
- 平文パスワード・生トークンはログに出さない。
- ログイン失敗はユーザー存在を漏らさない汎用メッセージにする。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from api.dependencies import (
    SESSION_COOKIE_NAME,
    _extract_session_token,
    get_current_user,
    get_firestore_client,
)
from api.schemas import (
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    UserResponse,
)
from shared.firestore_client import FirestoreClient
from shared.models import Session, User
from shared.security import (
    generate_session_token,
    hash_password,
    hash_token,
    verify_password,
)
from shared.utils import normalize_username

_logger = logging.getLogger(__name__)

router = APIRouter()

# セッション有効期間（時間）。未設定時は 7 日。
_DEFAULT_SESSION_TTL_HOURS = 168


def _session_ttl_hours() -> int:
    raw = os.environ.get("SESSION_TTL_HOURS")
    if not raw:
        return _DEFAULT_SESSION_TTL_HOURS
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_SESSION_TTL_HOURS


def _cookie_secure() -> bool:
    """Cookie の Secure 属性。ローカル http 開発では SESSION_COOKIE_SECURE=false で無効化する。"""
    return os.environ.get("SESSION_COOKIE_SECURE", "true").lower() != "false"


def _user_response(user: User) -> UserResponse:
    return UserResponse(username=user.username, role=user.role, display_name=user.display_name)


@router.post("/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: FirestoreClient = Depends(get_firestore_client),
):
    username = normalize_username(payload.username)
    user = db.get_user(username)
    # ユーザー不在でも verify_password を通し、存在有無によるタイミング差・情報漏洩を避ける。
    if user is None or not verify_password(payload.password, user.password_hash):
        _logger.warning("login failed for username=%s", username)  # 平文 PW は出さない
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

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
    return LoginResponse(token=token, user=_user_response(user))


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    db: FirestoreClient = Depends(get_firestore_client),
):
    """セッションを破棄する。トークンが無い／無効でも 200（冪等）。"""
    token = _extract_session_token(request)
    if token:
        db.delete_session(hash_token(token))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get("/auth/me", response_model=UserResponse)
def me(
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
):
    user = db.get_user(current.username)
    if user is None:
        # セッションは有効だがユーザーが削除済み。
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")
    return _user_response(user)


@router.patch("/auth/me", response_model=UserResponse)
def update_profile(
    payload: ProfileUpdateRequest,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
):
    user = db.get_user(current.username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")
    user.display_name = payload.display_name
    user.updated_at = datetime.now(timezone.utc)
    db.save_user(user)
    return _user_response(user)


@router.post("/auth/password")
def change_password(
    payload: PasswordChangeRequest,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
):
    user = db.get_user(current.username)
    if user is None or not verify_password(payload.current_password, user.password_hash):
        # 現在パスワードが誤り。汎用メッセージで詳細を伏せる。
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.updated_at = datetime.now(timezone.utc)
    db.save_user(user)
    return {"status": "ok"}
