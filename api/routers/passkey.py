"""Passkey (WebAuthn/FIDO2) 認証ルーター。

設計方針:
- 暗号演算は shared.webauthn_service に完全隔離（このファイルは薄い I/O 層）。
- challenge はワンタイム（consume_challenge がトランザクションで delete）。
- login 系 2 本（login/options・login/verify）は CSRF 免除対象（T11 の csrf.py 設定参照）。
- 全エラーは汎用メッセージのみ（情報漏洩防止）。
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from api.audit import AuditLogger
from api.dependencies import (
    get_audit_logger,
    get_client_ip,
    get_current_user,
    get_firestore_client,
    get_webauthn_config,
)
from api.schemas import LoginResponse, UserResponse
from api.session_service import issue_session
from shared.firestore_client import FirestoreClient
from shared.models import Session, WebAuthnChallenge, WebAuthnCredential
from shared.webauthn_config import WebAuthnConfig
from shared.webauthn_service import (
    generate_authentication_options_wrapper,
    generate_registration_options_wrapper,
    is_sign_count_valid,
    verify_authentication_response_wrapper,
    verify_registration_response_wrapper,
)

_logger = logging.getLogger(__name__)

router = APIRouter()


# ── リクエスト/レスポンス スキーマ ────────────────────────────────────────────


class PasskeyOptionsResponse(BaseModel):
    challenge_id: str
    options: str  # options_to_json() の出力


class PasskeyVerifyRequest(BaseModel):
    challenge_id: str
    credential: dict  # クライアントの WebAuthn credential オブジェクト


class PasskeyLoginOptionsRequest(BaseModel):
    username: str | None = None


class PasskeyCredentialResponse(BaseModel):
    """クレデンシャル公開情報。public_key は除外する。"""

    credential_id: str
    username: str
    name: str | None
    transports: list[str]
    aaguid: str | None
    sign_count: int
    created_at: str  # ISO 8601
    last_used_at: str | None  # ISO 8601


class PasskeyCredentialsListResponse(BaseModel):
    credentials: list[PasskeyCredentialResponse]


# ── 内部ヘルパー ──────────────────────────────────────────────────────────────


def _require_config(config: WebAuthnConfig | None) -> WebAuthnConfig:
    """WebAuthn が未設定なら 503 を送出する。"""
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Passkey authentication is not configured",
        )
    return config


def _user_response(user) -> UserResponse:
    return UserResponse(username=user.username, role=user.role, display_name=user.display_name)


def _save_new_challenge(
    db: FirestoreClient,
    cfg: WebAuthnConfig,
    user_id: str | None,
    ctype: str,
    challenge_b64url: str,
) -> WebAuthnChallenge:
    """WebAuthnChallenge を採番・保存して返す。"""
    now = datetime.now(timezone.utc)
    challenge = WebAuthnChallenge(
        challenge_id=secrets.token_urlsafe(32),
        challenge=challenge_b64url,
        user_id=user_id,
        type=ctype,  # type: ignore[arg-type]
        expires_at=now + timedelta(milliseconds=cfg.timeout_ms),
        created_at=now,
    )
    db.save_challenge(challenge)
    return challenge


# ── T6: POST /auth/passkey/register/options ───────────────────────────────────


@router.post("/auth/passkey/register/options", response_model=PasskeyOptionsResponse)
def register_options(
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    config: WebAuthnConfig | None = Depends(get_webauthn_config),
):
    """passkey 登録オプションを返す（ログイン必須）。

    既存クレデンシャルを exclude_credentials に渡し、重複登録を防ぐ。
    """
    cfg = _require_config(config)

    # 既存クレデンシャルを除外リストとして渡す
    existing = db.get_credentials_by_user(current.user_id)
    exclude_ids = [c.credential_id for c in existing]

    options_json = generate_registration_options_wrapper(
        rp_id=cfg.rp_id,
        rp_name=cfg.rp_name,
        user_name=current.username,
        timeout_ms=cfg.timeout_ms,
        exclude_credential_ids=exclude_ids,
    )

    # options JSON から challenge を抽出して DB に保存（verify 時に消費する）
    challenge_b64url = json.loads(options_json).get("challenge", "")
    challenge = _save_new_challenge(db, cfg, current.user_id, "registration", challenge_b64url)

    return PasskeyOptionsResponse(challenge_id=challenge.challenge_id, options=options_json)


# ── T7: POST /auth/passkey/register/verify ───────────────────────────────────


@router.post("/auth/passkey/register/verify")
def register_verify(
    payload: PasskeyVerifyRequest,
    request: Request,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    config: WebAuthnConfig | None = Depends(get_webauthn_config),
):
    """passkey 登録を検証して保存する（ログイン必須）。

    challenge はワンタイム消費。user_id 再確認でセッション固定化を防ぐ。
    """
    cfg = _require_config(config)
    now = datetime.now(timezone.utc)

    # 1. challenge をワンタイム消費
    challenge = db.consume_challenge(payload.challenge_id, now)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired challenge",
        )

    # 2. challenge の種別を確認（多層防御: authentication challenge の横流し防止）
    if challenge.type != "registration":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid challenge",
        )

    # 3. challenge が現在のログインユーザーのものか再確認
    if challenge.user_id != current.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid challenge",
        )

    # 4. WebAuthn 検証（失敗は 400）
    try:
        verified = verify_registration_response_wrapper(
            credential=payload.credential,
            expected_challenge_b64url=challenge.challenge,
            rp_id=cfg.rp_id,
            origins=cfg.origins,
        )
    except Exception:
        _logger.warning("passkey registration verification failed for user=%s", current.username)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration verification failed",
        )

    # 5. 重複クレデンシャルチェック（同じ credential_id が既登録なら 409）
    credential_id_b64url = verified["credential_id"]
    if db.get_credential_by_id(credential_id_b64url) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Credential already registered",
        )

    # 6. クレデンシャルを保存
    cred = WebAuthnCredential(
        credential_id=credential_id_b64url,
        user_id=current.user_id,
        username=current.username,
        public_key=verified["public_key"],
        sign_count=verified["sign_count"],
        transports=verified.get("transports", []),
        aaguid=verified.get("aaguid"),
        created_at=now,
    )
    db.save_credential(cred)

    # 7. 監査ログ（DB 書き込み成功後のみ）
    audit_logger.record(
        action="passkey_register",
        actor=current,
        ip=get_client_ip(request),
    )

    return {"status": "ok"}


# ── T8: POST /auth/passkey/login/options ─────────────────────────────────────


@router.post("/auth/passkey/login/options", response_model=PasskeyOptionsResponse)
def login_options(
    payload: PasskeyLoginOptionsRequest,
    db: FirestoreClient = Depends(get_firestore_client),
    config: WebAuthnConfig | None = Depends(get_webauthn_config),
):
    """passkey 認証オプションを返す（認証不要）。

    allowCredentials は常に空（discoverable credential / autofill フロー前提）。
    username を受け付けても credential 列挙には使わない。
    既知/未知 username でレスポンス形状を不可識別にし、ユーザー列挙 oracle を防ぐ。
    """
    cfg = _require_config(config)

    # allowCredentials は常に空: discoverable credential フローに統一する。
    # username が来ても credential_id を列挙すると未認証クライアントへのユーザー存在 oracle と
    # credential 漏洩になるため、username は一切 DB 参照に使用しない。
    options_json = generate_authentication_options_wrapper(
        rp_id=cfg.rp_id,
        timeout_ms=cfg.timeout_ms,
        allow_credential_ids=[],
    )

    challenge_b64url = json.loads(options_json).get("challenge", "")
    challenge = _save_new_challenge(db, cfg, None, "authentication", challenge_b64url)

    return PasskeyOptionsResponse(challenge_id=challenge.challenge_id, options=options_json)


# ── T9: POST /auth/passkey/login/verify ──────────────────────────────────────


@router.post("/auth/passkey/login/verify", response_model=LoginResponse)
def login_verify(
    payload: PasskeyVerifyRequest,
    request: Request,
    response: Response,
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    config: WebAuthnConfig | None = Depends(get_webauthn_config),
):
    """passkey 認証を検証してセッションを発行する（認証不要）。

    失敗は全て汎用 401（原因秘匿）。sign_count 後退はリプレイアタックとみなし拒否。
    """
    cfg = _require_config(config)
    ip = get_client_ip(request)
    now = datetime.now(timezone.utc)

    # 1. challenge をワンタイム消費（期限切れ・不在は 401）
    challenge = db.consume_challenge(payload.challenge_id, now)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed")

    # 2. challenge の種別を確認（多層防御: registration challenge の横流し防止）
    if challenge.type != "authentication":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed")

    # 3. クレデンシャル ID をペイロードから取得して DB を引く
    credential_id_from_client = payload.credential.get("id", "")
    stored_cred = db.get_credential_by_id(credential_id_from_client)
    if stored_cred is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed")

    # 4. WebAuthn 検証（公開鍵と格納 sign_count を使用）
    try:
        verified = verify_authentication_response_wrapper(
            credential=payload.credential,
            expected_challenge_b64url=challenge.challenge,
            rp_id=cfg.rp_id,
            origins=cfg.origins,
            credential_public_key_b64url=stored_cred.public_key,
            credential_current_sign_count=stored_cred.sign_count,
        )
    except Exception:
        _logger.warning("passkey authentication verification failed")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed")

    # 5. sign_count 後退チェック（リプレイアタック防御。update しない）
    new_sign_count = verified["new_sign_count"]
    if not is_sign_count_valid(stored_cred.sign_count, new_sign_count):
        _logger.warning("passkey sign_count regression detected for credential")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed")

    # 6. ユーザーを取得
    user = db.get_user(stored_cred.username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed")

    # 7. sign_count を更新
    db.update_sign_count(stored_cred.credential_id, new_sign_count, now)

    # 8. セッション発行（Cookie + CSRF Cookie セット）
    token, session = issue_session(db, user, request, response, ip)

    # 9. 監査ログ（DB 操作成功後のみ）
    audit_logger.record(action="passkey_used", actor=session, ip=ip)

    return LoginResponse(token=token, user=_user_response(user))


# ── T10: GET /auth/passkey/credentials ───────────────────────────────────────


@router.get("/auth/passkey/credentials", response_model=PasskeyCredentialsListResponse)
def list_credentials(
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    config: WebAuthnConfig | None = Depends(get_webauthn_config),
):
    """ログインユーザーのクレデンシャル一覧を返す。public_key は除外する。"""
    _require_config(config)

    creds = db.get_credentials_by_user(current.user_id)
    items = [
        PasskeyCredentialResponse(
            credential_id=c.credential_id,
            username=c.username,
            name=c.name,
            transports=c.transports,
            aaguid=c.aaguid,
            sign_count=c.sign_count,
            created_at=c.created_at.isoformat(),
            last_used_at=c.last_used_at.isoformat() if c.last_used_at else None,
        )
        for c in creds
    ]
    return PasskeyCredentialsListResponse(credentials=items)


# ── T10: DELETE /auth/passkey/credentials/{credential_id} ────────────────────


@router.delete("/auth/passkey/credentials/{credential_id}")
def delete_credential(
    credential_id: str,
    request: Request,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    config: WebAuthnConfig | None = Depends(get_webauthn_config),
):
    """クレデンシャルを削除する（ログイン必須・所有権検証・冪等）。

    FirestoreClient.delete_credential が所有権を検証し、不在でも例外を出さない。
    """
    _require_config(config)

    db.delete_credential(current.user_id, credential_id)

    audit_logger.record(
        action="passkey_removed",
        actor=current,
        ip=get_client_ip(request),
    )

    return {"status": "ok"}
