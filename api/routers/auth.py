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
    get_client_ip,
    get_current_user,
    get_firestore_client,
    get_audit_logger,
)
from api.audit import AuditLogger
from api.middleware.csrf import generate_csrf_token
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


# ログイン試行レートリミット設定。既定値は .env.example / docker-compose と一致させる。
# LOGIN_RATELIMIT_MAX_ATTEMPTS=0 で機能無効化。
_DEFAULT_LOGIN_RATELIMIT_MAX_ATTEMPTS = 5
_DEFAULT_LOGIN_RATELIMIT_WINDOW_SECONDS = 900
_DEFAULT_LOGIN_RATELIMIT_LOCKOUT_SECONDS = 900


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


def _user_response(user: User) -> UserResponse:
    return UserResponse(username=user.username, role=user.role, display_name=user.display_name)


def _make_rate_limit_keys(client_ip: str, username: str) -> tuple[str, str]:
    """IP と username をレートリミットキーに変換する。

    IP は生値を保存しないよう SHA-256 でハッシュ化する。username は正規化済みで
    機微情報ではないため、プリフィックス付与のみとする。
    """
    ip_key = "ip:" + hash_token(client_ip)
    user_key = "user:" + username
    return ip_key, user_key


def _raise_if_locked(locked_until: datetime | None, now: datetime) -> None:
    """ロック中（locked_until が未来）なら 429 + Retry-After を送出する。"""
    if locked_until:
        retry_after = max(0, int((locked_until - now).total_seconds()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


@router.post("/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    username = normalize_username(payload.username)
    client_ip = get_client_ip(request)
    max_attempts = _env_int(
        "LOGIN_RATELIMIT_MAX_ATTEMPTS", _DEFAULT_LOGIN_RATELIMIT_MAX_ATTEMPTS, 0
    )
    # max_attempts == 0 でレートリミット無効。有効時のみキー・しきい値を一度だけ算出する。
    rate_limited = max_attempts > 0
    if rate_limited:
        now = datetime.now(timezone.utc)
        ip_key, user_key = _make_rate_limit_keys(client_ip, username)
        window_seconds = _env_int(
            "LOGIN_RATELIMIT_WINDOW_SECONDS", _DEFAULT_LOGIN_RATELIMIT_WINDOW_SECONDS, 0
        )
        lockout_seconds = _env_int(
            "LOGIN_RATELIMIT_LOCKOUT_SECONDS", _DEFAULT_LOGIN_RATELIMIT_LOCKOUT_SECONDS, 0
        )

        # 事前チェック: IP / username いずれかがロック中なら資格情報検証より前に 429。
        _raise_if_locked(db.check_login_lock(ip_key, now), now)
        _raise_if_locked(db.check_login_lock(user_key, now), now)

    user = db.get_user(username)
    # ユーザー不在でも verify_password を通し、存在有無によるタイミング差・情報漏洩を避ける。
    if user is None or not verify_password(payload.password, user.password_hash):
        # IP と username 両方に失敗を記録。新規ロック時のみログ（IP は生値を出さずハッシュキーで）。
        lockout_triggered = False
        if rate_limited:
            if db.register_failed_login(
                ip_key, now, max_attempts, window_seconds, lockout_seconds
            ):
                lockout_triggered = True
                _logger.warning("login rate limit lockout for ip_key=%s", ip_key)
                # ロックアウト時は監査ログに記録
                audit_logger.record(action="login_lockout", ip=client_ip)
            if db.register_failed_login(
                user_key, now, max_attempts, window_seconds, lockout_seconds
            ):
                if not lockout_triggered:
                    lockout_triggered = True
                    audit_logger.record(action="login_lockout", ip=client_ip)
                _logger.warning("login rate limit lockout for username=%s", username)

        _logger.warning("login failed for username=%s", username)  # 平文 PW は出さない
        # ロックアウト以外のログイン失敗を記録
        if not lockout_triggered:
            audit_logger.record(action="login_failure", ip=client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # ログイン成功時は試行カウンタをクリア
    if rate_limited:
        db.clear_login_attempts(ip_key)
        db.clear_login_attempts(user_key)

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

    # ログイン成功を記録（actor はセッション）
    audit_logger.record(action="login_success", actor=session, ip=client_ip)

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

    return LoginResponse(token=token, user=_user_response(user))


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """セッションを破棄する。トークンが無い／無効でも 200（冪等）。

    認証セッションがあればログアウト監査を記録する。
    """
    token = _extract_session_token(request)
    if token:
        session_id = hash_token(token)
        # 監査ログの actor を得るため、セッションを削除する前に読み出す。
        # 先に delete すると get_session が None を返し、ログアウトが記録されない。
        current = db.get_session(session_id)
        db.delete_session(session_id)
        if current:
            audit_logger.record(action="logout", actor=current, ip=get_client_ip(request))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get("/auth/me", response_model=UserResponse)
def me(
    request: Request,
    response: Response,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
):
    user = db.get_user(current.username)
    if user is None:
        # セッションは有効だがユーザーが削除済み。
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")

    # 既存セッションが CSRF トークンを持っていない場合に補填する。
    # WHY: ログインし直さないユーザーも CSRF 保護を受けられるよう、
    #       /auth/me アクセス時に csrf_token cookie を発行する。
    #       既に cookie がある場合は上書きしない（ローテーションは login で行う）。
    if "csrf_token" not in request.cookies:
        _set_csrf_cookie(response)
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
