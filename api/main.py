"""FastAPI アプリと API キー認証ミドルウェア。"""
import hmac
import logging
import os

from fastapi import FastAPI, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

from api.routers import admin, articles, auth, feed, podcasts, settings

_logger = logging.getLogger(__name__)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(title="Tech News Podcast API", version="0.1.0")

# API_KEY 未設定時は全リクエストが 401 になる。デプロイ設定ミスを起動時に検出できるよう警告する。
if not os.environ.get("API_KEY"):
    _logger.warning("API_KEY environment variable is not set — all requests will return 401")


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    expected = os.environ.get("API_KEY", "")
    # spec-reviewer: hmac.compare_digest でタイミング攻撃を防ぐ
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key


# 認証ルーター。ゲートウェイの X-API-Key 下にマウントする。login/logout はセッション不要、
# /auth/me 等はルーター内の get_current_user 依存でセッションを要求する。
app.include_router(auth.router, prefix="", dependencies=[Security(verify_api_key)])
app.include_router(feed.router, prefix="", dependencies=[Security(verify_api_key)])
app.include_router(articles.router, prefix="", dependencies=[Security(verify_api_key)])
app.include_router(podcasts.router, prefix="", dependencies=[Security(verify_api_key)])
app.include_router(settings.router, prefix="", dependencies=[Security(verify_api_key)])
# 管理用 CRUD。専用 admin ロールは無く共有 X-API-Key で保護する（admin.py 冒頭コメント参照）。
app.include_router(admin.router, prefix="", dependencies=[Security(verify_api_key)])


@app.get("/health")
def health():
    return {"status": "ok"}
