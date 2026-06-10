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
