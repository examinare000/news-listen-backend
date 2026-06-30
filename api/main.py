"""FastAPI アプリと API キー認証ミドルウェア。"""
import hmac
import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader

from api.cors_config import build_cors_options
from api.middleware.csrf import CsrfMiddleware, build_csrf_config
from api.middleware.security_headers import SecurityHeadersMiddleware, build_security_headers
from api.ratelimit import rate_limit
from api.routers import admin, articles, auth, feed, notifications, passkey as _passkey_router, podcasts, settings
from shared.logging_config import configure_logging

# 構造化ログ＋機微情報スクラブを最初に設定する（issue #83）。
# LOG_FORMAT=json で Cloud Logging 互換 JSON、LOG_LEVEL でレベル制御。冪等。
configure_logging()

_logger = logging.getLogger(__name__)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# 検証エラー本文に平文を載せてはならない機微フィールド（資格情報の漏洩防止）。
_SENSITIVE_FIELDS = frozenset({"password", "new_password", "current_password"})

app = FastAPI(title="Tech News Podcast API", version="0.6.0")


@app.exception_handler(RequestValidationError)
async def _redact_validation_errors(request: Request, exc: RequestValidationError):
    """422 検証エラーから機微フィールドの送信値（input/ctx）を伏せて返す。

    FastAPI 既定のハンドラは各エラーに input（送信された生値）を含めるため、
    パスワード等を弱い値で送ると 422 応答本文に平文が反映されてしまう。
    機微フィールドに該当するエラーのみ input/ctx を除去する（他は既定どおり）。
    """
    sanitized = []
    for err in exc.errors():
        item = dict(err)
        if any(part in _SENSITIVE_FIELDS for part in item.get("loc", ())):
            item.pop("input", None)
            item.pop("ctx", None)
        sanitized.append(item)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder({"detail": sanitized}),
    )


async def handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """未捕捉例外（500）を構造化ログに記録し、内部情報を漏らさない汎用本文を返す（issue #83）。

    例外メッセージ・トレースはサーバーログにのみ残し（スクラブ Filter 適用）、
    クライアントには定型メッセージのみ返す（情報漏洩防止）。path/method は可観測性のため記録する。
    """
    _logger.error(
        "unhandled_exception path=%s method=%s",
        request.url.path,
        request.method,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


# ミドルウェア登録順序（後で add されたものが外側に適用される）:
# 適用順（外→内）: SecurityHeaders（最外）→ CORS → CSRF（最内）→ ルーター
# WHY: CORS を CSRF の外側に置くことで、CsrfMiddleware が返す 403 が CORS を通過し、
#       Access-Control-Allow-Origin 等が付与される。これがないとブラウザは
#       クロスオリジンの 403 を CORS 違反として握りつぶし、フロントが理由を読めない。
# 注意: add 順が逆順適用のため、CSRF を先に add（最内）し CORS を後に add（外側）する。
app.add_middleware(CsrfMiddleware, config=build_csrf_config(os.environ))
app.add_middleware(CORSMiddleware, **build_cors_options(os.environ))
app.add_middleware(SecurityHeadersMiddleware, headers=build_security_headers(os.environ))

# 未捕捉例外（500）を構造化ログに記録し、内部情報を漏らさず返す（issue #83）。
app.add_exception_handler(Exception, handle_unhandled_exception)

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
# 汎用 api レート制限も全エンドポイント同様に適用する（issue #37「全API共通」要件）。
# login は ADR-014 専用ロックも併走するが、api 上限はそれより緩いため先に専用ロックが効く。
app.include_router(
    auth.router, prefix="", dependencies=[Security(verify_api_key), Depends(rate_limit("api"))]
)
app.include_router(
    feed.router,
    prefix="",
    dependencies=[Security(verify_api_key), Depends(rate_limit("api"))],
)
app.include_router(
    articles.router,
    prefix="",
    dependencies=[Security(verify_api_key), Depends(rate_limit("api"))],
)
app.include_router(
    podcasts.router,
    prefix="",
    dependencies=[Security(verify_api_key), Depends(rate_limit("api"))],
)
app.include_router(
    settings.router,
    prefix="",
    dependencies=[Security(verify_api_key), Depends(rate_limit("api"))],
)
app.include_router(
    notifications.router,
    prefix="",
    dependencies=[Security(verify_api_key), Depends(rate_limit("api"))],
)
# 管理用 CRUD。共有 X-API-Key（ゲートウェイ）に加え、各エンドポイントで admin ロールを要求する（admin.py 冒頭コメント参照）。
app.include_router(
    admin.router, prefix="", dependencies=[Security(verify_api_key), Depends(rate_limit("api"))]
)

# Passkey (WebAuthn/FIDO2) 認証。WEBAUTHN_RP_ID 未設定時はエンドポイントが 503 を返す（無効化）。
# login系2本（login/options・login/verify）は CSRF 免除（build_csrf_config のデフォルト参照）。
app.include_router(
    _passkey_router.router, prefix="", dependencies=[Security(verify_api_key), Depends(rate_limit("api"))]
)


@app.get("/health")
def health():
    return {"status": "ok"}
