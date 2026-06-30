"""クライアント（web/iOS）のエラー・クラッシュ受信エンドポイント（issue #83）。

web のエラーバウンダリ/グローバルハンドラや iOS の MetricKit から送られるエラー報告を
構造化ログに記録し、Cloud Logging（→ Error Reporting）へ集約する。

設計:
- **認証セッション不要**: エラーはログイン前にも起き得るため。X-API-Key（ゲートウェイ）と
  汎用レート制限で保護し、CSRF は免除する（`api/middleware/csrf.py` の既定免除に追加済み）。
- 機微情報（トークン・PW・PII）は構造化ログのスクラブ（`shared/logging_config`）が送出時に伏せる。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from api.dependencies import get_client_ip

_logger = logging.getLogger(__name__)

router = APIRouter()


class ClientErrorReport(BaseModel):
    """クライアントからのエラー報告。message/context は最小限・スクラブ前提。"""

    # 送信元プラットフォーム（"web" / "ios"）。
    source: str = Field(min_length=1, max_length=32)
    # エラー種別（"render" / "window" / "unhandledrejection" / "crash" 等）。
    kind: str = Field(min_length=1, max_length=64)
    # エラーメッセージ（任意・上限長で DoS を防ぐ）。
    message: str | None = Field(default=None, max_length=4000)
    # 付加情報（任意・小さく保つ）。
    context: dict | None = None


@router.post("/client-errors", status_code=status.HTTP_202_ACCEPTED)
def report_client_error(report: ClientErrorReport, request: Request):
    """クライアントのエラー/クラッシュを構造化ログに記録する（受理したら 202）。

    `extra` のキーは LogRecord 予約属性（message 等）と衝突しないよう client_ で前置する。
    """
    _logger.error(
        "client_error",
        extra={
            "event": "client_error",
            "client_source": report.source,
            "client_kind": report.kind,
            "client_message": report.message,
            "client_context": report.context,
            "ip": get_client_ip(request),
        },
    )
    return {"status": "ok"}
