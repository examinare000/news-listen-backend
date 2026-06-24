"""POST /articles/{id}/star, /articles/{id}/dismiss のテスト。"""
from unittest.mock import patch


def test_star_article_returns_200_when_article_exists(api_client, mock_db):
    mock_db.article_exists.return_value = True

    response = api_client.post(
        "/articles/abc123/star",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "starred"
    mock_db.add_starred_article.assert_called_once_with("user1", "abc123")


def test_star_article_triggers_recommendation_and_podcast(api_client, mock_db, mock_job_trigger):
    """スターは正のシグナル: recommendation 再計算と podcast 生成を起動する。"""
    mock_db.article_exists.return_value = True

    api_client.post("/articles/abc123/star", headers={"X-API-Key": "test-key"})

    triggered = {call.args[0] for call in mock_job_trigger.trigger.call_args_list}
    assert triggered == {"recommendation", "podcast-generator"}
    for call in mock_job_trigger.trigger.call_args_list:
        assert call.args[1] == "user1"


def test_star_article_does_not_trigger_jobs_when_article_missing(api_client, mock_db, mock_job_trigger):
    """記事が存在しない(404)場合はジョブを起動しない。"""
    mock_db.article_exists.return_value = False

    api_client.post("/articles/missing/star", headers={"X-API-Key": "test-key"})

    mock_job_trigger.trigger.assert_not_called()


def test_star_article_returns_404_when_article_not_found(api_client, mock_db):
    mock_db.article_exists.return_value = False

    response = api_client.post(
        "/articles/missing/star",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404


def test_dismiss_article_returns_200(api_client, mock_db):
    mock_db.article_exists.return_value = True

    response = api_client.post(
        "/articles/abc123/dismiss",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"
    mock_db.add_dismissed_article.assert_called_once_with("user1", "abc123")


def test_dismiss_article_triggers_recommendation_only(api_client, mock_db, mock_job_trigger):
    """dismiss は負のシグナル: recommendation 再計算のみ起動し podcast は起動しない。"""
    mock_db.article_exists.return_value = True

    api_client.post("/articles/abc123/dismiss", headers={"X-API-Key": "test-key"})

    triggered = [call.args[0] for call in mock_job_trigger.trigger.call_args_list]
    assert triggered == ["recommendation"]
    assert mock_job_trigger.trigger.call_args_list[0].args[1] == "user1"


def test_star_blocked_when_star_limit_exceeded_returns_429_and_no_job(
    api_client, mock_db, mock_job_trigger
):
    """star がレート制限超過で 429 を返し、ジョブを起動しない。"""
    mock_db.article_exists.return_value = True
    # star のレート制限超過: (False, 3600)
    mock_db.consume_rate_limit.return_value = (False, 3600)

    with patch.dict("os.environ", {"STAR_RATELIMIT_MAX_REQUESTS": "10"}):
        import importlib

        import api.main as m

        importlib.reload(m)
        from api.dependencies import (
            get_firestore_client,
            get_job_trigger,
            get_storage_client,
            get_user_id,
            get_audit_logger,
        )

        m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
        m.app.dependency_overrides[get_storage_client] = lambda: None
        m.app.dependency_overrides[get_job_trigger] = lambda: mock_job_trigger
        m.app.dependency_overrides[get_user_id] = lambda: "user1"
        m.app.dependency_overrides[get_audit_logger] = lambda: None

        from fastapi.testclient import TestClient

        test_client = TestClient(m.app)

        response = test_client.post(
            "/articles/abc123/star",
            headers={"X-API-Key": "test-key"},
        )

        m.app.dependency_overrides.clear()

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3600"
    # ジョブが起動されていない
    mock_job_trigger.trigger.assert_not_called()


def test_star_within_limit_triggers_jobs(api_client, mock_db, mock_job_trigger):
    """star が制限内（consume_rate_limit=(True,0)）でジョブを起動する。"""
    mock_db.article_exists.return_value = True
    mock_db.consume_rate_limit.return_value = (True, 0)

    with patch.dict("os.environ", {"STAR_RATELIMIT_MAX_REQUESTS": "10"}):
        import importlib

        import api.main as m

        importlib.reload(m)
        from api.dependencies import (
            get_firestore_client,
            get_job_trigger,
            get_storage_client,
            get_user_id,
            get_audit_logger,
        )

        m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
        m.app.dependency_overrides[get_storage_client] = lambda: None
        m.app.dependency_overrides[get_job_trigger] = lambda: mock_job_trigger
        m.app.dependency_overrides[get_user_id] = lambda: "user1"
        m.app.dependency_overrides[get_audit_logger] = lambda: None

        from fastapi.testclient import TestClient

        test_client = TestClient(m.app)

        response = test_client.post(
            "/articles/abc123/star",
            headers={"X-API-Key": "test-key"},
        )

        m.app.dependency_overrides.clear()

    assert response.status_code == 200
    # ジョブが起動されている（recommendation と podcast-generator）
    assert mock_job_trigger.trigger.call_count >= 2


def test_dismiss_uses_global_limit_only(api_client, mock_db):
    """dismiss エンドポイントはグローバル API レート制限のみを受け、star 用の専用制限を使わない。"""
    mock_db.article_exists.return_value = True
    mock_db.consume_rate_limit.return_value = (True, 0)

    # api レート制限を有効化して実際に消費経路を通す（env はリクエスト毎に読まれるため reload 不要）。
    with patch.dict("os.environ", {"API_RATELIMIT_MAX_REQUESTS": "120"}):
        response = api_client.post(
            "/articles/abc123/dismiss",
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 200
    # api レート制限が実際に消費されたことを確認（消費キーは全て api バケット）。
    consumed_keys = [call.args[0] for call in mock_db.consume_rate_limit.call_args_list]
    assert consumed_keys, "api レート制限が消費されていない（グローバル制限が効いていない）"
    assert all(key.startswith("api:") for key in consumed_keys)
    # dismiss は star 専用制限を使わない。
    assert not any(key.startswith("star:") for key in consumed_keys)
