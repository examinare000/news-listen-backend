"""通知関連 API エンドポイントのテスト。

GET  /notifications/vapid-public-key
POST /notifications/subscriptions
DELETE /notifications/subscriptions
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from shared.models import PushSubscription

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def test_get_vapid_public_key_returns_404_when_not_configured(api_client):
    """VAPID が設定されていなければ GET /notifications/vapid-public-key は 404"""
    # api_client は環境変数で VAPID が未設定の状態でセットアップされている
    response = api_client.get("/notifications/vapid-public-key", headers={"X-API-Key": "test-key"})
    assert response.status_code == 404


def test_post_subscriptions_returns_201_created(api_client, mock_db):
    """POST /notifications/subscriptions は 201 で {"status": "subscribed"} を返す"""
    mock_db.save_push_subscription = MagicMock()

    body = {
        "endpoint": "https://push.example.com/endpoint/abc123",
        "keys": {
            "p256dh": "dh_key_base64",
            "auth": "auth_secret_base64",
        },
    }

    response = api_client.post("/notifications/subscriptions", json=body, headers={"X-API-Key": "test-key"})

    assert response.status_code == 201
    assert response.json()["status"] == "subscribed"
    mock_db.save_push_subscription.assert_called_once()

    # 呼び出された PushSubscription を検証
    call_args = mock_db.save_push_subscription.call_args
    sub = call_args[0][0]
    assert isinstance(sub, PushSubscription)
    assert sub.user_id == "user1"
    assert sub.endpoint == "https://push.example.com/endpoint/abc123"
    assert sub.p256dh == "dh_key_base64"
    assert sub.auth == "auth_secret_base64"


def test_post_subscriptions_idempotent_same_endpoint_twice(api_client, mock_db):
    """POST /notifications/subscriptions は同じ endpoint を 2 回送信しても 201 を返す（冪等）"""
    mock_db.save_push_subscription = MagicMock()

    body = {
        "endpoint": "https://push.example.com/endpoint/same",
        "keys": {
            "p256dh": "dh_key",
            "auth": "auth_secret",
        },
    }

    response1 = api_client.post("/notifications/subscriptions", json=body, headers={"X-API-Key": "test-key"})
    response2 = api_client.post("/notifications/subscriptions", json=body, headers={"X-API-Key": "test-key"})

    assert response1.status_code == 201
    assert response2.status_code == 201
    # 2 回呼ばれている（DB は upsert で対応）
    assert mock_db.save_push_subscription.call_count == 2


def test_delete_subscriptions_returns_200_unsubscribed(api_client, mock_db):
    """DELETE /notifications/subscriptions は 200 で {"status": "unsubscribed"} を返す"""
    mock_db.delete_push_subscription = MagicMock()

    response = api_client.delete(
        "/notifications/subscriptions?endpoint=https://push.example.com/endpoint/abc123",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unsubscribed"
    mock_db.delete_push_subscription.assert_called_once_with("user1", "https://push.example.com/endpoint/abc123")


def test_delete_subscriptions_idempotent_nonexistent_endpoint(api_client, mock_db):
    """DELETE /notifications/subscriptions は存在しないエンドポイントでも 200 を返す（冪等）"""
    mock_db.delete_push_subscription = MagicMock()

    response = api_client.delete(
        "/notifications/subscriptions?endpoint=https://push.example.com/endpoint/nonexistent",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unsubscribed"
    # delete_push_subscription はスキップされても no-op（呼ばれる）
    mock_db.delete_push_subscription.assert_called_once()


def test_notifications_endpoints_require_authentication():
    """通知エンドポイントは認証（API Key）なしで 401 を返す"""
    from fastapi.testclient import TestClient
    import api.main as m

    # オーバーライドなしで直接テストクライアント使用（API Key 検証が有効）
    test_client = TestClient(m.app)

    # API Key なしでリクエスト
    response = test_client.get("/notifications/vapid-public-key")
    assert response.status_code == 401

    response = test_client.post("/notifications/subscriptions", json={})
    assert response.status_code == 401

    response = test_client.delete("/notifications/subscriptions?endpoint=http://example.com")
    assert response.status_code == 401


def test_post_subscriptions_with_optional_expiration_time(api_client, mock_db):
    """POST /notifications/subscriptions は expirationTime フィールドを受け取れるが無視する（W3C 仕様準拠）"""
    mock_db.save_push_subscription = MagicMock()

    body = {
        "endpoint": "https://push.example.com/endpoint/abc123",
        "keys": {
            "p256dh": "dh_key",
            "auth": "auth_secret",
        },
        "expirationTime": "2026-07-14T12:00:00Z",  # W3C フィールド
    }

    response = api_client.post("/notifications/subscriptions", json=body, headers={"X-API-Key": "test-key"})

    assert response.status_code == 201
    # 保存されている PushSubscription には expirationTime がない
    call_args = mock_db.save_push_subscription.call_args
    sub = call_args[0][0]
    assert not hasattr(sub, 'expirationTime')
