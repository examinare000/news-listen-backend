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
    get_audit_logger,
    get_client_ip,
    get_current_user,
    get_email_sender,
    get_firestore_client,
)
from api.audit import AuditLogger
from api.session_service import _set_csrf_cookie, issue_session
from api.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    ResetPasswordRequest,
    RevokeSessionsResponse,
    SessionResponse,
    SessionsListResponse,
    UserResponse,
)
from shared.firestore_client import FirestoreClient
from shared.models import Session, User
from shared.security import (
    hash_password,
    hash_token,
    verify_password,
)
from shared.utils import normalize_username

_logger = logging.getLogger(__name__)

router = APIRouter()

# ログイン試行レートリミット設定。既定値は .env.example / docker-compose と一致させる。
# LOGIN_RATELIMIT_MAX_ATTEMPTS=0 で機能無効化。
_DEFAULT_LOGIN_RATELIMIT_MAX_ATTEMPTS = 5
_DEFAULT_LOGIN_RATELIMIT_WINDOW_SECONDS = 900
_DEFAULT_LOGIN_RATELIMIT_LOCKOUT_SECONDS = 900


def _env_int(name: str, default: int, minimum: int) -> int:
    """環境変数を整数として読む。未設定・不正値は default、minimum 未満は minimum に丸める。"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


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

    # セッション発行: ローテーション・トークン生成・Cookie セット・CSRF Cookie を一括処理。
    # WHY: passkey ログインでも同じフローを再利用するため session_service に抽出した。
    token, session = issue_session(db, user, request, response, client_ip)

    # ログイン成功を記録（actor はセッション）
    audit_logger.record(action="login_success", actor=session, ip=client_ip)

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


# ── 自分のセッション管理（issue #84） ────────────────────────────────
# 端末紛失・不審ログイン時に本人が自衛できるよう、有効セッションの一覧・個別失効・
# 「他の全デバイスからログアウト」を提供する。passkey credentials の list/delete を踏襲。


@router.get("/auth/sessions", response_model=SessionsListResponse)
def list_sessions(
    request: Request,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
):
    """本人の有効セッションを一覧する。現在のセッションには current=True を立てる。

    現在のセッションはリクエスト由来のトークンから算出し、クライアント値に依存しない。
    """
    # get_current_user 通過後なので token は必ず存在する。else は防御的フォールバック。
    token = _extract_session_token(request)
    current_session_id = hash_token(token) if token else None

    sessions = db.list_sessions_for_user(current.user_id)
    items = [
        SessionResponse(
            id=s.session_id,
            device_label=s.device_label,
            created_at=s.created_at.isoformat(),
            last_used_at=s.last_used_at.isoformat() if s.last_used_at else None,
            current=(s.session_id == current_session_id),
        )
        for s in sessions
    ]
    return SessionsListResponse(sessions=items)


@router.delete("/auth/sessions/{session_id}")
def revoke_session(
    session_id: str,
    request: Request,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """本人のセッションを 1 件失効させる（所有権検証）。

    他人のセッション・不在は 404（存在を秘匿）。失効後そのセッションは 401 になる。
    """
    revoked = db.revoke_session(session_id, current.user_id)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    audit_logger.record(
        action="session_revoke",
        actor=current,
        ip=get_client_ip(request),
        details={"scope": "self_single"},
    )
    return {"status": "ok"}


@router.post("/auth/sessions/revoke-others", response_model=RevokeSessionsResponse)
def revoke_other_sessions(
    request: Request,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """現在のセッション以外を全て失効させる（「他の全デバイスからログアウト」）。

    保持する現在のセッションはリクエスト由来で算出し、クライアント提供値を信用しない。
    """
    # get_current_user 通過後なので token は必ず存在する。else は防御的フォールバック
    # （空文字はどの実 doc-id（64桁hex）とも一致しないため、誤って現在を巻き込まない）。
    token = _extract_session_token(request)
    current_session_id = hash_token(token) if token else ""
    revoked_count = db.delete_sessions_for_user_except(current.user_id, current_session_id)
    audit_logger.record(
        action="session_revoke",
        actor=current,
        ip=get_client_ip(request),
        details={"scope": "self_others", "revoked_session_count": revoked_count},
    )
    return RevokeSessionsResponse(revoked_count=revoked_count)


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


@router.post("/auth/password/forgot")
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    email_sender=Depends(get_email_sender),
):
    """パスワード忘れたフロー（認証不要）。

    **常に 200 を返す**（ユーザー列挙対策）。
    実在かつ email 有の場合のみ生トークン発行→save→send。
    """
    username = normalize_username(payload.username)
    client_ip = get_client_ip(request)
    now = datetime.now(timezone.utc)

    # レート制限チェック（PASSWORD_RESET_RATELIMIT_* 環境変数）
    max_requests = _env_int("PASSWORD_RESET_RATELIMIT_MAX_REQUESTS", 0, 0)
    if max_requests > 0:
        window_seconds = _env_int("PASSWORD_RESET_RATELIMIT_WINDOW_SECONDS", 3600, 1)
        ip_key = "password_reset:ip:" + hash_token(client_ip)
        allowed, retry_after = db.consume_rate_limit(ip_key, now, max_requests, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many password reset attempts. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    # ユーザー存在確認
    user = db.get_user(username)

    # 常に 200 を返す（存在有無を秘匿）
    if user is None or user.email is None:
        # ユーザー不在または email 無。副作用なし。監査は記録（発見可能性の秘匿）。
        audit_logger.record(
            action="password_reset_requested",
            ip=client_ip,
            details={"found": False},
        )
        return {"status": "ok"}

    # ユーザー存在かつ email 有。生トークン発行→保存→送信
    from shared.security import generate_session_token, hash_token as hash_token_fn
    from shared.models import PasswordResetToken

    raw_token = generate_session_token()
    token_hash = hash_token_fn(raw_token)
    # トークン寿命は env で調整可（既定 30 分）。短命にして漏洩時の窓を最小化する。
    ttl_minutes = _env_int("PASSWORD_RESET_TOKEN_TTL_MINUTES", 30, 1)
    expires_at = now + timedelta(minutes=ttl_minutes)

    token = PasswordResetToken(
        token_hash=token_hash,
        user_id=user.user_id,
        username=user.username,
        expires_at=expires_at,
        created_at=now,
        used_at=None,
    )

    # トークン保存
    db.save_reset_token(token)

    # メール送信（非致命）
    try:
        password_reset_url_base = os.environ.get(
            "PASSWORD_RESET_URL_BASE", "https://app.example.com/reset-password"
        )
        reset_url = f"{password_reset_url_base}?token={raw_token}"
        email_sender.send_password_reset_email(user.email, reset_url)
    except Exception:
        # メール送信失敗は warning ログのみで、本操作は成功させる（非致命）
        _logger.warning("Failed to send password reset email to %s", user.email)

    # 監査ログ記録（details に平文 PW・token・hash 入れない）
    audit_logger.record(
        action="password_reset_requested",
        ip=client_ip,
        target_username=user.username,
        details={"found": True},
    )

    return {"status": "ok"}


@router.post("/auth/password/reset")
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """パスワードリセット（トークンで認証）。

    有効なトークンなら password_hash 更新→delete_sessions_for_user。
    期限切れ・既用・不正トークンは汎用 400（原因秘匿）。
    """
    client_ip = get_client_ip(request)
    now = datetime.now(timezone.utc)

    # トークンハッシュ化
    token_hash = hash_token(payload.token)

    # トークンレコード取得
    token_record = db.get_reset_token(token_hash)
    if token_record is None:
        # 不正なトークン
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    # トークン消費（期限・既用チェック + used_at セット）
    if not db.consume_reset_token(token_hash, now):
        # 期限切れ・既用・不在
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    # ユーザー取得
    user = db.get_user(token_record.username)
    if user is None:
        # トークンは有効だがユーザーが削除済み
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User not found")

    # パスワード更新
    user.password_hash = hash_password(payload.new_password)
    user.updated_at = now
    db.save_user(user)

    # 全セッション失効（強制ログアウト）
    db.delete_sessions_for_user(user.user_id)

    # 監査ログ記録（details に平文 PW・token・hash 入れない）
    audit_logger.record(
        action="password_reset_completed",
        ip=client_ip,
        target_username=user.username,
    )

    return {"status": "ok"}
