"""GET /feed のテスト。"""


def test_feed_requires_api_key(api_client):
    response = api_client.get("/feed")
    assert response.status_code == 401


def test_feed_with_valid_api_key_returns_200(api_client, mock_db):
    # Recommendation が見つからない場合は空のフィードを返す
    mock_db.get_recommendation.return_value = None

    response = api_client.get("/feed", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert "articles" in response.json()


def test_feed_with_invalid_api_key_returns_401(api_client):
    response = api_client.get("/feed", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401
