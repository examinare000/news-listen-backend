"""管理用 /admin/featured-sites CRUD エンドポイント。

おすすめサイト（featuredSites コレクション）をブートストラップ後に追加・更新・削除する。

認証: 専用の admin ロールは存在しないため、他のエンドポイントと同じ共有 `X-API-Key`
（main.py の verify_api_key）で保護する。デプロイ単位の単一ユーザー前提（ADR-007）であり、
管理操作も同じ運用者が行うため許容する。将来必要になれば `ADMIN_API_KEY` を分離し、本ルーターのみ
別キーで保護する余地を残す。
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.dependencies import get_firestore_client, require_admin, get_audit_logger, get_client_ip
from api.audit import AuditLogger
from api.schemas import (
    AuditLogsResponse,
    AuditLogResponse,
    FeaturedSiteRequest,
    FeaturedSiteResponse,
    FeaturedSitesResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)
from shared.firestore_client import FirestoreClient
from shared.models import FeaturedSite, User, Session
from shared.security import hash_password
from shared.utils import normalize_username, slugify

router = APIRouter()


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(username=user.username, role=user.role, display_name=user.display_name)


def _admin_count(db: FirestoreClient) -> int:
    """現在の admin ロールのユーザー数を返す（最後の admin 保護の判定に使う）。"""
    return sum(1 for u in db.list_users() if u.role == "admin")


def _to_response(site: FeaturedSite) -> FeaturedSiteResponse:
    return FeaturedSiteResponse(
        id=site.id,
        name=site.name,
        url=site.url,
        thumbnail_url=site.thumbnail_url,
        description=site.description,
    )


@router.get("/admin/featured-sites", response_model=FeaturedSitesResponse)
def list_featured_sites(
    db: FirestoreClient = Depends(get_firestore_client),
):
    return FeaturedSitesResponse(sites=[_to_response(s) for s in db.get_featured_sites()])


@router.post("/admin/featured-sites", response_model=FeaturedSiteResponse, status_code=201)
def create_featured_site(
    request: FeaturedSiteRequest,
    db: FirestoreClient = Depends(get_firestore_client),
):
    """おすすめサイトを新規作成する。doc id は name から slug 化する。"""
    site_id = slugify(request.name)
    if db.get_featured_site(site_id) is not None:
        raise HTTPException(status_code=409, detail="Featured site already exists")
    site = FeaturedSite(
        id=site_id,
        name=request.name,
        url=str(request.url),
        thumbnail_url=str(request.thumbnail_url) if request.thumbnail_url else None,
        description=request.description,
        order=request.order,
    )
    db.save_featured_site(site)
    return _to_response(site)


@router.put("/admin/featured-sites/{site_id}", response_model=FeaturedSiteResponse)
def update_featured_site(
    site_id: str,
    request: FeaturedSiteRequest,
    db: FirestoreClient = Depends(get_firestore_client),
):
    """既存おすすめサイトを全置換更新する。存在しなければ 404。"""
    if db.get_featured_site(site_id) is None:
        raise HTTPException(status_code=404, detail="Featured site not found")
    site = FeaturedSite(
        id=site_id,
        name=request.name,
        url=str(request.url),
        thumbnail_url=str(request.thumbnail_url) if request.thumbnail_url else None,
        description=request.description,
        order=request.order,
    )
    db.save_featured_site(site)
    return _to_response(site)


@router.delete("/admin/featured-sites/{site_id}")
def delete_featured_site(
    site_id: str,
    db: FirestoreClient = Depends(get_firestore_client),
):
    if db.get_featured_site(site_id) is None:
        raise HTTPException(status_code=404, detail="Featured site not found")
    db.delete_featured_site(site_id)
    return {"status": "deleted", "id": site_id}


# ── ユーザー管理（admin ロール必須） ──────────────────────────────
# featured-sites と異なり require_admin で保護する。共有 X-API-Key（ゲートウェイ）に
# 加えて、ログインユーザーが admin ロールであることを要求する。


@router.get("/admin/users", response_model=UserListResponse, dependencies=[Depends(require_admin)])
def list_users(db: FirestoreClient = Depends(get_firestore_client)):
    return UserListResponse(users=[_user_to_response(u) for u in db.list_users()])


@router.post(
    "/admin/users",
    response_model=UserResponse,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_user(
    http_request: Request,
    request: UserCreateRequest,
    db: FirestoreClient = Depends(get_firestore_client),
    actor: Session = Depends(require_admin),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """ユーザーを新規作成する。username は正規化して doc-id にする。"""
    username = normalize_username(request.username)
    if db.get_user(username) is not None:
        raise HTTPException(status_code=409, detail="User already exists")
    now = datetime.now(timezone.utc)
    user = User(
        username=username,
        # user_id はデータパーティションキー。username とは独立した不変 ID を採番する。
        user_id=uuid.uuid4().hex,
        password_hash=hash_password(request.password),
        role=request.role,
        display_name=request.display_name or username,
        created_at=now,
        updated_at=now,
    )
    db.save_user(user)
    # ユーザー作成を記録
    audit_logger.record(
        action="user_create",
        actor=actor,
        target_username=username,
        ip=get_client_ip(http_request),
    )
    return _user_to_response(user)


@router.patch(
    "/admin/users/{username}",
    response_model=UserResponse,
    dependencies=[Depends(require_admin)],
)
def update_user(
    username: str,
    http_request: Request,
    request: UserUpdateRequest,
    db: FirestoreClient = Depends(get_firestore_client),
    actor: Session = Depends(require_admin),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """ロール変更・パスワードリセット・表示名変更。指定フィールドのみ更新する。

    最後の admin を降格すると管理不能になるため 409 で防ぐ。降格・パスワードリセット時は
    当該ユーザーのセッションを失効させ、変更前の権限での継続アクセスを断つ。
    """
    normalized = normalize_username(username)
    user = db.get_user(normalized)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    demoting_admin = request.role == "user" and user.role == "admin"
    if demoting_admin and _admin_count(db) <= 1:
        raise HTTPException(status_code=409, detail="Cannot demote the last admin")

    # 変更を判定（監査ログのアクション種別決定用）
    role_changed = request.role is not None and request.role != user.role
    password_reset = request.new_password is not None
    display_name_changed = request.display_name is not None and request.display_name != user.display_name

    if request.role is not None:
        user.role = request.role
    if request.new_password is not None:
        user.password_hash = hash_password(request.new_password)
    if request.display_name is not None:
        user.display_name = request.display_name
    user.updated_at = datetime.now(timezone.utc)
    db.save_user(user)

    # 監査ログを記録（変更種別ごとに異なるアクションを使用）
    client_ip = get_client_ip(http_request)
    if role_changed:
        audit_logger.record(
            action="user_role_change",
            actor=actor,
            target_username=normalized,
            ip=client_ip,
            details={"new_role": request.role},
        )
    if password_reset:
        audit_logger.record(
            action="user_password_reset",
            actor=actor,
            target_username=normalized,
            ip=client_ip,
        )
    # 表示名のみの一般更新は user_update として記録する
    # （role / password の変更は上で専用アクションとして記録済み）。
    if display_name_changed and not role_changed and not password_reset:
        audit_logger.record(
            action="user_update",
            actor=actor,
            target_username=normalized,
            ip=client_ip,
            details={"field": "display_name"},
        )

    # 降格・パスワードリセットは既存セッションを失効させる（旧権限/旧資格情報での継続を断つ）。
    if demoting_admin or request.new_password is not None:
        session_count = db.delete_sessions_for_user(user.user_id)
        # セッション失効時に記録（ベストエフォート）
        audit_logger.record(
            action="session_revoke",
            actor=actor,
            target_username=normalized,
            ip=client_ip,
            details={"revoked_session_count": session_count},
        )
    return _user_to_response(user)


@router.delete("/admin/users/{username}", dependencies=[Depends(require_admin)])
def delete_user(
    username: str,
    http_request: Request,
    db: FirestoreClient = Depends(get_firestore_client),
    actor: Session = Depends(require_admin),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """ユーザーを削除する。最後の admin は削除不可（409）。削除時にセッションも失効させる。"""
    normalized = normalize_username(username)
    user = db.get_user(normalized)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "admin" and _admin_count(db) <= 1:
        raise HTTPException(status_code=409, detail="Cannot delete the last admin")
    db.delete_user(normalized)
    # 削除済みユーザーが TTL 満了まで API を叩けないようセッションを失効させる。
    session_count = db.delete_sessions_for_user(user.user_id)
    # ユーザー削除を記録
    audit_logger.record(
        action="user_delete",
        actor=actor,
        target_username=normalized,
        ip=get_client_ip(http_request),
        details={"revoked_session_count": session_count},
    )
    return {"status": "deleted", "username": normalized}


# ── 監査ログ ────────────────────────────────────────────


@router.get("/admin/audit-logs", response_model=AuditLogsResponse, dependencies=[Depends(require_admin)])
def list_audit_logs(
    action: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: FirestoreClient = Depends(get_firestore_client),
):
    """監査ログ一覧を取得する（admin のみ）。

    action フィルタで特定のアクション（login_success など）のみ抽出可能。
    limit で取得件数を制限（既定 50、上限 500）。
    timestamp 降順で返す。

    security note: actor_user_id は返さない（内部 UUID の露出防止）。
    レスポンスには actor_username / target_username / ip / action / timestamp / details のみ。
    """
    logs = db.list_audit_logs(action=action, limit=limit)
    # AuditLog を AuditLogResponse に変換（actor_user_id は除外）
    responses = [
        AuditLogResponse(
            action=log.action,
            timestamp=log.timestamp.isoformat(),
            actor_username=log.actor_username,
            target_username=log.target_username,
            ip=log.ip,
            details=log.details,
        )
        for log in logs
    ]
    return AuditLogsResponse(logs=responses)
