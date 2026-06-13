"""POST /articles/{id}/star, /articles/{id}/dismiss のテスト。"""


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
