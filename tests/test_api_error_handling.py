"""issue #83: グローバル例外ハンドラのテスト。

未捕捉の 500 を構造化ログに記録しつつ、レスポンス本文には内部情報
（例外メッセージ・トレース）を漏らさないことを検証する。
"""
import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_unhandled_exception_returns_generic_500():
    from api.main import handle_unhandled_exception

    request = SimpleNamespace(url=SimpleNamespace(path="/podcasts/x"), method="GET")
    exc = ValueError("super-secret-internal-detail token=leak")

    response = await handle_unhandled_exception(request, exc)

    assert response.status_code == 500
    body = json.loads(bytes(response.body).decode())
    # 内部情報・例外メッセージを漏らさない
    assert "super-secret-internal-detail" not in json.dumps(body)
    assert "leak" not in json.dumps(body)
    assert body["detail"] == "Internal server error"


@pytest.mark.asyncio
async def test_unhandled_exception_logs_with_event_name(caplog):
    import logging

    from api.main import handle_unhandled_exception

    request = SimpleNamespace(url=SimpleNamespace(path="/feed"), method="POST")
    with caplog.at_level(logging.ERROR):
        await handle_unhandled_exception(request, RuntimeError("boom"))

    # 500 が "unhandled_exception" イベントとして error ログに記録される（パス/メソッド付き）。
    assert any(
        "unhandled_exception" in r.getMessage() and r.levelno == logging.ERROR
        for r in caplog.records
    )


def test_global_handler_wired_returns_generic_500_via_asgi():
    """実 ASGI 経路で未捕捉例外が汎用 500 になり内部情報を漏らさない（結合テスト・#6/#7）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.main import handle_unhandled_exception

    app = FastAPI()
    app.add_exception_handler(Exception, handle_unhandled_exception)

    @app.get("/boom")
    def boom():
        raise ValueError("leak-internal-token=abc123")

    # raise_server_exceptions=False で実際の 500 レスポンスを観測する。
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}
    assert "leak-internal" not in resp.text
    assert "abc123" not in resp.text
