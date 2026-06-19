"""管理用 /admin/featured-sites CRUD エンドポイント。

おすすめサイト（featuredSites コレクション）をブートストラップ後に追加・更新・削除する。

認証: 専用の admin ロールは存在しないため、他のエンドポイントと同じ共有 `X-API-Key`
（main.py の verify_api_key）で保護する。デプロイ単位の単一ユーザー前提（ADR-007）であり、
管理操作も同じ運用者が行うため許容する。将来必要になれば `ADMIN_API_KEY` を分離し、本ルーターのみ
別キーで保護する余地を残す。
"""
from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_firestore_client
from api.schemas import (
    FeaturedSiteRequest,
    FeaturedSiteResponse,
    FeaturedSitesResponse,
)
from shared.firestore_client import FirestoreClient
from shared.models import FeaturedSite
from shared.utils import slugify

router = APIRouter()


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
