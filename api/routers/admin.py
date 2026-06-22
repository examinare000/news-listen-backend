"""管理用 /admin/featured-sites CRUD エンドポイント。

おすすめサイト（featuredSites コレクション）をブートストラップ後に追加・更新・削除する。

認証: 専用の admin ロールは存在しないため、他のエンドポイントと同じ共有 `X-API-Key`
（main.py の verify_api_key）で保護する。デプロイ単位の単一ユーザー前提（ADR-007）であり、
管理操作も同じ運用者が行うため許容する。将来必要になれば `ADMIN_API_KEY` を分離し、本ルーターのみ
別キーで保護する余地を残す。
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_firestore_client, require_admin
from api.schemas import (
    FeaturedSiteRequest,
    FeaturedSiteResponse,
    FeaturedSitesResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)
from shared.firestore_client import FirestoreClient
from shared.models import FeaturedSite, User
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
    request: UserCreateRequest,
    db: FirestoreClient = Depends(get_firestore_client),
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
    return _user_to_response(user)


@router.patch(
    "/admin/users/{username}",
    response_model=UserResponse,
    dependencies=[Depends(require_admin)],
)
def update_user(
    username: str,
    request: UserUpdateRequest,
    db: FirestoreClient = Depends(get_firestore_client),
):
    """ロール変更・パスワードリセット・表示名変更。指定フィールドのみ更新する。

    最後の admin を降格すると管理不能になるため 409 で防ぐ。降格・パスワードリセット時は
    当該ユーザーのセッションを失効させ、変更前の権限での継続アクセスを断つ。
    """
    user = db.get_user(normalize_username(username))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    demoting_admin = request.role == "user" and user.role == "admin"
    if demoting_admin and _admin_count(db) <= 1:
        raise HTTPException(status_code=409, detail="Cannot demote the last admin")

    if request.role is not None:
        user.role = request.role
    if request.new_password is not None:
        user.password_hash = hash_password(request.new_password)
    if request.display_name is not None:
        user.display_name = request.display_name
    user.updated_at = datetime.now(timezone.utc)
    db.save_user(user)

    # 降格・パスワードリセットは既存セッションを失効させる（旧権限/旧資格情報での継続を断つ）。
    if demoting_admin or request.new_password is not None:
        db.delete_sessions_for_user(user.user_id)
    return _user_to_response(user)


@router.delete("/admin/users/{username}", dependencies=[Depends(require_admin)])
def delete_user(
    username: str,
    db: FirestoreClient = Depends(get_firestore_client),
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
    db.delete_sessions_for_user(user.user_id)
    return {"status": "deleted", "username": normalized}
