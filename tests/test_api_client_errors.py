"""issue #83: クライアントエラー受信エンドポイント POST /client-errors のテスト。

web/iOS のエラー・クラッシュを受け取り構造化ログに記録する。認証セッション不要・
X-API-Key + レート制限で保護。機微情報は構造化ログのスクラブで送出時に伏せる。
"""
import logging

API_HEADERS = {"X-API-Key": "test-key"}


def test_post_client_error_returns_202(api_client):
    resp = api_client.post(
        "/client-errors",
        json={"source": "web", "kind": "render", "message": "boom"},
        headers=API_HEADERS,
    )
    assert resp.status_code == 202


def test_post_client_error_logs_structured_fields(api_client, caplog):
    with caplog.at_level(logging.ERROR):
        api_client.post(
            "/client-errors",
            json={"source": "ios", "kind": "crash", "message": "x", "context": {"build": "1.0"}},
            headers=API_HEADERS,
        )

    rec = next(r for r in caplog.records if getattr(r, "event", None) == "client_error")
    assert rec.client_source == "ios"
    assert rec.client_kind == "crash"
    assert rec.levelno == logging.ERROR


def test_post_client_error_works_without_session(api_client):
    """未ログイン（セッション未設定）でも 202（エラーはログイン前にも起き得る）。"""
    resp = api_client.post("/client-errors", json={"source": "web", "kind": "window"}, headers=API_HEADERS)
    assert resp.status_code == 202


def test_post_client_error_requires_source_and_kind(api_client):
    assert api_client.post("/client-errors", json={"kind": "render"}, headers=API_HEADERS).status_code == 422
    assert api_client.post("/client-errors", json={"source": "web"}, headers=API_HEADERS).status_code == 422
