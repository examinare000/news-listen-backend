"""GET /feed のテスト。"""
from datetime import datetime, timezone

from api.routers.feed import _filter_unread_ids
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


def _make_prefs(dismissed: list[str] | None = None, read: list[str] | None = None) -> UserPrefs:
    return UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        dismissed_article_ids=dismissed or [],
        read_article_ids=read or [],
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


# ── _filter_unread_ids 純粋関数のユニットテスト ────────────────────────


def test_filter_unread_ids_excludes_read_and_dismissed():
    """_filter_unread_ids は read_ids と dismissed_ids の和集合を除外する（読了フィルタ）。"""
    candidates = ["a0", "a1", "a2", "a3", "a4"]
    read_ids = ["a1", "a3"]
    dismissed_ids = ["a2"]

    result = _filter_unread_ids(candidates, read_ids, dismissed_ids)

    assert result == ["a0", "a4"]
    assert "a1" not in result
    assert "a2" not in result
    assert "a3" not in result


def test_filter_unread_ids_empty_candidates():
    """_filter_unread_ids は空リストを受けると空を返す。"""
    result = _filter_unread_ids([], ["a1"], ["a2"])
    assert result == []


def test_filter_unread_ids_empty_read_and_dismissed():
    """_filter_unread_ids は read・dismissed が空なら全候補を返す。"""
    candidates = ["a0", "a1", "a2"]
    result = _filter_unread_ids(candidates, [], [])
    assert result == candidates


def test_filter_unread_ids_overlapping_read_and_dismissed():
    """_filter_unread_ids は read と dismissed に重複があっても正しく除外する。"""
    candidates = ["a0", "a1", "a2", "a3"]
    read_ids = ["a1", "a2"]
    dismissed_ids = ["a2", "a3"]  # a2 は両方に含まれる

    result = _filter_unread_ids(candidates, read_ids, dismissed_ids)

    # a1（read）、a2（両方）、a3（dismissed）は除外。a0 のみ残る。
    assert result == ["a0"]


def test_filter_unread_ids_preserves_order():
    """_filter_unread_ids は候補の順序を保持する。"""
    candidates = ["z", "y", "x", "w"]
    read_ids = ["y"]
    dismissed_ids = ["w"]

    result = _filter_unread_ids(candidates, read_ids, dismissed_ids)

    # 順序が保持されていること
    assert result == ["z", "x"]


# ── filter=unread フィルタと is_read フィールド統合テスト ────────────────────────


def test_feed_filter_unread_excludes_read_and_dismissed(api_client, mock_db):
    """filter=unread のとき、read と dismissed の両方を除外する（フォールバック経路）。"""
    mock_db.get_recommendation.return_value = None
    mock_db.get_user_prefs.return_value = _make_prefs(dismissed=["a1", "a2"], read=["a3"])
    mock_db.get_recent_articles.return_value = [_make_article(f"a{i}") for i in range(5)]

    response = api_client.get("/feed?filter=unread", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    ids = [a["id"] for a in response.json()["articles"]]
    # a1（dismissed）、a2（dismissed）、a3（read）は除外。a0, a4 だけ
    assert ids == ["a0", "a4"]


def test_feed_filter_all_excludes_dismissed_only(api_client, mock_db):
    """filter=all（既定）のとき、dismissed のみ除外する（後方互換）。"""
    mock_db.get_recommendation.return_value = None
    mock_db.get_user_prefs.return_value = _make_prefs(dismissed=["a1", "a2"], read=["a3"])
    mock_db.get_recent_articles.return_value = [_make_article(f"a{i}") for i in range(5)]

    response = api_client.get("/feed?filter=all", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    ids = [a["id"] for a in response.json()["articles"]]
    # dismissed のみ除外（a1, a2）。a3（read）は含まれる（後方互換）
    assert ids == ["a0", "a3", "a4"]


def test_feed_default_filter_is_all(api_client, mock_db):
    """filter 未指定はデフォルト 'all'（dismissed のみ除外）。"""
    mock_db.get_recommendation.return_value = None
    mock_db.get_user_prefs.return_value = _make_prefs(dismissed=["a1"], read=["a3"])
    mock_db.get_recent_articles.return_value = [_make_article(f"a{i}") for i in range(5)]

    # filter パラメータ未指定
    response = api_client.get("/feed", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    ids = [a["id"] for a in response.json()["articles"]]
    # dismissed のみ除外（a1）。a3 は含まれる（後方互換）
    assert ids == ["a0", "a2", "a3", "a4"]


def test_feed_invalid_filter_returns_422(api_client, mock_db):
    """不正な filter 値は 422 を返す。"""
    response = api_client.get("/feed?filter=invalid", headers={"X-API-Key": "test-key"})
    assert response.status_code == 422


def test_feed_article_response_has_is_read_field(api_client, mock_db):
    """ArticleResponse に is_read フィールドが含まれる（新フィールド・既存クライアント無害）。"""
    mock_db.get_recommendation.return_value = None
    mock_db.get_user_prefs.return_value = _make_prefs(read=["a0"])
    mock_db.get_recent_articles.return_value = [_make_article(f"a{i}") for i in range(3)]

    response = api_client.get("/feed", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    articles = response.json()["articles"]
    for article in articles:
        assert "is_read" in article
        assert isinstance(article["is_read"], bool)


def test_feed_is_read_marks_read_articles(api_client, mock_db):
    """is_read が read_ids に応じて true/false で付与される。"""
    mock_db.get_recommendation.return_value = None
    mock_db.get_user_prefs.return_value = _make_prefs(read=["a0", "a2"])
    mock_db.get_recent_articles.return_value = [_make_article(f"a{i}") for i in range(3)]

    response = api_client.get("/feed", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    is_read_map = {a["id"]: a["is_read"] for a in response.json()["articles"]}
    assert is_read_map["a0"] is True
    assert is_read_map["a1"] is False
    assert is_read_map["a2"] is True


def test_feed_unread_filter_with_recommendation_excludes_correctly(api_client, mock_db):
    """filter=unread がレコメンド経路でも read ∪ dismissed を除外する。"""
    # レコメンドが十分にある場合
    rec_articles = [RecommendedArticle(article_id=f"a{i}", score=1.0) for i in range(110)]
    mock_db.get_recommendation.return_value = Recommendation(
        user_id="user1", date="2026-06-21", articles=rec_articles, generated_at=_NOW
    )
    mock_db.get_user_prefs.return_value = _make_prefs(read=["a5", "a10"], dismissed=["a15"])
    mock_db.get_article.side_effect = lambda aid: _make_article(aid) if int(aid[1:]) < 50 else None

    response = api_client.get("/feed?filter=unread", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    ids = [a["id"] for a in response.json()["articles"]]
    # a5, a10, a15 は除外される
    assert "a5" not in ids
    assert "a10" not in ids
    assert "a15" not in ids
    # a0, a1, ... の他は含まれる（50件超なので上位50件のみ）
    assert len(ids) <= 50


