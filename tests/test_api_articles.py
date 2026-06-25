"""POST /articles/{id}/star, /articles/{id}/dismiss のテスト。"""
from unittest.mock import patch

from shared.models import DEFAULT_PODCAST_LANGUAGE


def test_star_article_returns_202_when_article_exists(api_client_with_auth, mock_db):
    mock_db.article_exists.return_value = True
    mock_db.get_user_prefs.return_value.default_difficulty = "toeic_900"
    mock_db.try_acquire_user_podcast.return_value = "podcast-uuid-123"

    response = api_client_with_auth.post(
        "/articles/abc123/star",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    mock_db.add_starred_article.assert_called_once_with("user1", "abc123")


def test_star_article_creates_processing_podcast_row(api_client_with_auth, mock_db):
    """star 受付時に processing 行を原子的に確保し、クライアントに「生成中」を可視化する。

    難易度は prefs.default_difficulty（generator と一致させ重複行を防ぐ）。
    """
    mock_db.article_exists.return_value = True
    mock_db.get_user_prefs.return_value.default_difficulty = "toeic_900"
    mock_db.try_acquire_user_podcast.return_value = "podcast-uuid-123"

    response = api_client_with_auth.post(
        "/articles/abc123/star",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    # try_acquire_user_podcast が user_id, article_id, difficulty, language で呼ばれたことを確認
    mock_db.try_acquire_user_podcast.assert_called_once_with(
        "user1", "abc123", "toeic_900", DEFAULT_PODCAST_LANGUAGE
    )


def test_star_article_triggers_recommendation_and_podcast(api_client_with_auth, mock_db, mock_job_trigger):
    """スターは正のシグナル: recommendation 再計算と podcast 生成を起動する。"""
    mock_db.article_exists.return_value = True
    mock_db.get_user_prefs.return_value.default_difficulty = "toeic_900"
    mock_db.try_acquire_user_podcast.return_value = "podcast-uuid-123"

    api_client_with_auth.post("/articles/abc123/star", headers={"X-API-Key": "test-key"})

    triggered = {call.args[0] for call in mock_job_trigger.trigger.call_args_list}
    assert triggered == {"recommendation", "podcast-generator"}
    for call in mock_job_trigger.trigger.call_args_list:
        assert call.args[1] == "user1"


def test_star_article_does_not_trigger_jobs_when_article_missing(api_client_with_auth, mock_db, mock_job_trigger):
    """記事が存在しない(404)場合はジョブを起動しない。"""
    mock_db.article_exists.return_value = False

    response = api_client_with_auth.post("/articles/missing/star", headers={"X-API-Key": "test-key"})

    assert response.status_code == 404
    mock_job_trigger.trigger.assert_not_called()


def test_star_article_returns_404_when_article_not_found(api_client_with_auth, mock_db):
    mock_db.article_exists.return_value = False

    response = api_client_with_auth.post(
        "/articles/missing/star",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404


def test_dismiss_article_returns_200(api_client_with_auth, mock_db):
    mock_db.article_exists.return_value = True

    response = api_client_with_auth.post(
        "/articles/abc123/dismiss",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"
    mock_db.add_dismissed_article.assert_called_once_with("user1", "abc123")


def test_dismiss_article_triggers_recommendation_only(api_client_with_auth, mock_db, mock_job_trigger):
    """dismiss は負のシグナル: recommendation 再計算のみ起動し podcast は起動しない。"""
    mock_db.article_exists.return_value = True

    api_client_with_auth.post("/articles/abc123/dismiss", headers={"X-API-Key": "test-key"})

    triggered = [call.args[0] for call in mock_job_trigger.trigger.call_args_list]
    assert triggered == ["recommendation"]
    assert mock_job_trigger.trigger.call_args_list[0].args[1] == "user1"


def test_star_blocked_when_star_limit_exceeded_returns_429_and_no_job(
    api_client, mock_db, mock_job_trigger, mock_audit, current_session
):
    """star がレート制限超過で 429 を返し、ジョブを起動しない。"""
    mock_db.article_exists.return_value = True
    # star のレート制限超過: (False, 3600)
    mock_db.consume_rate_limit.return_value = (False, 3600)

    with patch.dict("os.environ", {"STAR_RATELIMIT_MAX_REQUESTS": "10"}):
        import importlib
        from unittest.mock import MagicMock

        import api.main as m

        importlib.reload(m)
        from api.dependencies import (
            get_firestore_client,
            get_job_trigger,
            get_storage_client,
            get_user_id,
            get_current_user,
            get_audit_logger,
        )

        mock_audit_logger = MagicMock()
        m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
        m.app.dependency_overrides[get_storage_client] = lambda: None
        m.app.dependency_overrides[get_job_trigger] = lambda: mock_job_trigger
        m.app.dependency_overrides[get_current_user] = lambda: current_session
        m.app.dependency_overrides[get_user_id] = lambda: "user1"
        m.app.dependency_overrides[get_audit_logger] = lambda: mock_audit_logger

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


def test_star_within_limit_triggers_jobs(api_client_with_auth, mock_db, mock_job_trigger, mock_audit, current_session):
    """star が制限内（consume_rate_limit=(True,0)）でジョブを起動する。"""
    mock_db.article_exists.return_value = True
    mock_db.consume_rate_limit.return_value = (True, 0)
    mock_db.get_user_prefs.return_value.default_difficulty = "toeic_900"
    mock_db.try_acquire_user_podcast.return_value = "podcast-uuid-123"

    with patch.dict("os.environ", {"STAR_RATELIMIT_MAX_REQUESTS": "10"}):
        import importlib
        from unittest.mock import MagicMock

        import api.main as m

        importlib.reload(m)
        from api.dependencies import (
            get_firestore_client,
            get_job_trigger,
            get_storage_client,
            get_user_id,
            get_current_user,
            get_audit_logger,
        )

        mock_audit_logger = MagicMock()
        m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
        m.app.dependency_overrides[get_storage_client] = lambda: None
        m.app.dependency_overrides[get_job_trigger] = lambda: mock_job_trigger
        m.app.dependency_overrides[get_current_user] = lambda: current_session
        m.app.dependency_overrides[get_user_id] = lambda: "user1"
        m.app.dependency_overrides[get_audit_logger] = lambda: mock_audit_logger

        from fastapi.testclient import TestClient

        test_client = TestClient(m.app)

        response = test_client.post(
            "/articles/abc123/star",
            headers={"X-API-Key": "test-key"},
        )

        m.app.dependency_overrides.clear()

    assert response.status_code == 202
    # ジョブが起動されている（recommendation と podcast-generator）
    assert mock_job_trigger.trigger.call_count >= 2


def test_dismiss_uses_global_limit_only(api_client_with_auth, mock_db):
    """dismiss エンドポイントはグローバル API レート制限のみを受け、star 用の専用制限を使わない。"""
    mock_db.article_exists.return_value = True
    mock_db.consume_rate_limit.return_value = (True, 0)

    # api レート制限を有効化して実際に消費経路を通す（env はリクエスト毎に読まれるため reload 不要）。
    with patch.dict("os.environ", {"API_RATELIMIT_MAX_REQUESTS": "120"}):
        response = api_client_with_auth.post(
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


def test_star_article_records_audit_log(api_client_with_auth, mock_db, mock_audit):
    """star_article が成功時に audit.record を正しい action・details で呼ぶ。"""
    mock_db.article_exists.return_value = True
    mock_db.get_user_prefs.return_value.default_difficulty = "toeic_900"
    mock_db.try_acquire_user_podcast.return_value = "podcast-uuid-123"

    response = api_client_with_auth.post(
        "/articles/abc123/star",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 202
    # audit.record が "article_star" action で呼ばれたことを確認
    mock_audit.record.assert_called_once()
    call_kwargs = mock_audit.record.call_args.kwargs
    assert call_kwargs["action"] == "article_star"
    assert call_kwargs["details"]["article_id"] == "abc123"


def test_star_article_does_not_record_when_404(api_client_with_auth, mock_db, mock_audit):
    """記事が存在しない場合（404）は audit.record を呼ばない。"""
    mock_db.article_exists.return_value = False

    response = api_client_with_auth.post(
        "/articles/missing/star",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 404
    mock_audit.record.assert_not_called()


def test_dismiss_article_records_audit_log(api_client_with_auth, mock_db, mock_audit):
    """dismiss_article が成功時に audit.record を正しい action・details で呼ぶ。"""
    mock_db.article_exists.return_value = True

    response = api_client_with_auth.post(
        "/articles/abc123/dismiss",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    # audit.record が "article_dismiss" action で呼ばれたことを確認
    mock_audit.record.assert_called_once()
    call_kwargs = mock_audit.record.call_args.kwargs
    assert call_kwargs["action"] == "article_dismiss"
    assert call_kwargs["details"]["article_id"] == "abc123"


def test_dismiss_article_does_not_record_when_404(api_client_with_auth, mock_db, mock_audit):
    """記事が存在しない場合（404）は audit.record を呼ばない。"""
    mock_db.article_exists.return_value = False

    response = api_client_with_auth.post(
        "/articles/abc123/dismiss",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 404
    mock_audit.record.assert_not_called()


def test_mark_read_article_returns_200(api_client_with_auth, mock_db):
    """mark_read が 200 で ActionResponse(status='read', article_id=...) を返す。"""
    mock_db.article_exists.return_value = True

    response = api_client_with_auth.post(
        "/articles/abc123/mark-read",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "read"
    assert response.json()["article_id"] == "abc123"
    mock_db.add_read_article.assert_called_once_with("user1", "abc123")


def test_mark_read_article_returns_404_when_not_found(api_client_with_auth, mock_db):
    """記事が存在しない場合は 404 を返す。"""
    mock_db.article_exists.return_value = False

    response = api_client_with_auth.post(
        "/articles/missing/mark-read",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404


def test_mark_read_article_is_idempotent(api_client_with_auth, mock_db):
    """mark_read は冪等（同じ記事を複数回マークしても重複しない）。"""
    mock_db.article_exists.return_value = True

    # 2回呼び出し
    api_client_with_auth.post(
        "/articles/abc123/mark-read",
        headers={"X-API-Key": "test-key"},
    )
    api_client_with_auth.post(
        "/articles/abc123/mark-read",
        headers={"X-API-Key": "test-key"},
    )

    # 2回とも add_read_article が呼ばれるが、Firestore の ArrayUnion で自動的に重複回避
    assert mock_db.add_read_article.call_count == 2
    mock_db.add_read_article.assert_any_call("user1", "abc123")


def test_mark_read_article_does_not_trigger_jobs(api_client_with_auth, mock_db, mock_job_trigger):
    """mark_read はジョブを起動しない（recommendation も podcast も）。"""
    mock_db.article_exists.return_value = True

    api_client_with_auth.post(
        "/articles/abc123/mark-read",
        headers={"X-API-Key": "test-key"},
    )

    # ジョブが一度も起動されないこと
    mock_job_trigger.trigger.assert_not_called()


def test_mark_read_article_records_audit_log(api_client_with_auth, mock_db, mock_audit):
    """mark_read が成功時に audit.record を正しい action・details で呼ぶ。"""
    mock_db.article_exists.return_value = True

    response = api_client_with_auth.post(
        "/articles/abc123/mark-read",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    # audit.record が "article_mark_read" action で呼ばれたことを確認
    mock_audit.record.assert_called_once()
    call_kwargs = mock_audit.record.call_args.kwargs
    assert call_kwargs["action"] == "article_mark_read"
    assert call_kwargs["details"]["article_id"] == "abc123"


def test_mark_read_article_does_not_record_when_404(api_client_with_auth, mock_db, mock_audit):
    """記事が存在しない場合（404）は audit.record を呼ばない。"""
    mock_db.article_exists.return_value = False

    response = api_client_with_auth.post(
        "/articles/missing/mark-read",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 404
    mock_audit.record.assert_not_called()
