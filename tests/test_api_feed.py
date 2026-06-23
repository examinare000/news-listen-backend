"""GET /feed のテスト。"""
from datetime import datetime, timezone

from shared.models import (
    Article,
    Recommendation,
    RecommendedArticle,
    UserPrefs,
)

_NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _make_article(article_id: str) -> Article:
    return Article(
        id=article_id,
        title=f"title-{article_id}",
        url=f"https://example.com/{article_id}",
        source="example",
        content="body",
        published_at=_NOW,
        fetched_at=_NOW,
    )


def _make_prefs(dismissed: list[str] | None = None) -> UserPrefs:
    return UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        dismissed_article_ids=dismissed or [],
    )


def test_feed_requires_api_key(api_client):
    response = api_client.get("/feed")
    assert response.status_code == 401


def test_feed_with_invalid_api_key_returns_401(api_client):
    response = api_client.get("/feed", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_feed_no_recommendation_falls_back_to_all_articles(api_client, mock_db):
    # レコメンドが無い場合はフォールバックして最近記事を全件返す
    mock_db.get_recommendation.return_value = None
    mock_db.get_user_prefs.return_value = _make_prefs()
    mock_db.get_recent_articles.return_value = [_make_article(f"a{i}") for i in range(5)]

    response = api_client.get("/feed", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    body = response.json()
    assert "articles" in body
    assert len(body["articles"]) == 5


def test_feed_with_enough_recommendations_returns_top_50(api_client, mock_db):
    # レコメンドが 100 件以上ある場合は上位 50 件のみを返し、全記事フォールバックしない
    rec_articles = [
        RecommendedArticle(article_id=f"a{i}", score=1.0 - i * 0.001)
        for i in range(120)
    ]
    mock_db.get_recommendation.return_value = Recommendation(
        user_id="user1", date="2026-06-21", articles=rec_articles, generated_at=_NOW
    )
    mock_db.get_article.side_effect = lambda aid: _make_article(aid)

    response = api_client.get("/feed", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    assert len(response.json()["articles"]) == 50
    # 十分な件数があるためフォールバック経路（全記事取得）には入らない
    mock_db.get_recent_articles.assert_not_called()


def test_feed_with_few_recommendations_falls_back_to_all_articles(api_client, mock_db):
    # レコメンドが 100 件未満なら全記事フォールバックする
    rec_articles = [RecommendedArticle(article_id=f"a{i}", score=0.9) for i in range(3)]
    mock_db.get_recommendation.return_value = Recommendation(
        user_id="user1", date="2026-06-21", articles=rec_articles, generated_at=_NOW
    )
    mock_db.get_user_prefs.return_value = _make_prefs()
    mock_db.get_recent_articles.return_value = [
        _make_article(f"a{i}") for i in range(80)
    ]

    response = api_client.get("/feed", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    assert len(response.json()["articles"]) == 80
    mock_db.get_recent_articles.assert_called_once()


def test_feed_fallback_excludes_dismissed_articles(api_client, mock_db):
    # フォールバック時、dismiss された記事は結果から除外される
    mock_db.get_recommendation.return_value = None
    mock_db.get_user_prefs.return_value = _make_prefs(dismissed=["a1", "a3"])
    mock_db.get_recent_articles.return_value = [_make_article(f"a{i}") for i in range(5)]

    response = api_client.get("/feed", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    ids = [a["id"] for a in response.json()["articles"]]
    assert ids == ["a0", "a2", "a4"]


def test_feed_fallback_keeps_recommendation_scores(api_client, mock_db):
    # フォールバック時、rec に含まれる記事はスコアを引き継ぎ、それ以外は 0.5
    rec_articles = [RecommendedArticle(article_id="a0", score=0.87)]
    mock_db.get_recommendation.return_value = Recommendation(
        user_id="user1", date="2026-06-21", articles=rec_articles, generated_at=_NOW
    )
    mock_db.get_user_prefs.return_value = _make_prefs()
    mock_db.get_recent_articles.return_value = [_make_article("a0"), _make_article("a1")]

    response = api_client.get("/feed", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    scores = {a["id"]: a["score"] for a in response.json()["articles"]}
    assert scores["a0"] == 0.87
    assert scores["a1"] == 0.5
