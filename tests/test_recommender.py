import json
from unittest.mock import MagicMock
from shared.models import Article
from datetime import datetime, timezone


def _make_article(article_id, title):
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    return Article(
        id=article_id,
        title=title,
        url=f"https://example.com/{article_id}",
        source="hackernews",
        content="Some content",
        published_at=now,
        fetched_at=now,
    )


def test_score_articles_returns_scores_for_all_candidates():
    mock_gemini = MagicMock()
    mock_gemini.generate_text.return_value = json.dumps([
        {"id": "a1", "score": 0.9},
        {"id": "a2", "score": 0.3},
    ])

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Rust is fast"), _make_article("a2", "Celebrity news")]

    scores = rec.score_articles(
        candidates,
        starred_articles=[_make_article("starred1", "Go is fun")],
        dismissed_articles=[],
    )

    assert len(scores) == 2
    a1_score = next(s for s in scores if s.article_id == "a1")
    assert a1_score.score == 0.9


def test_score_articles_includes_dismissed_in_prompt():
    """dismissed_articles が Gemini プロンプトに含まれることを確認する"""
    mock_gemini = MagicMock()
    mock_gemini.generate_text.return_value = json.dumps([
        {"id": "a1", "score": 0.5},
    ])

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Some article")]
    dismissed = [_make_article("d1", "Dismissed article title")]

    rec.score_articles(candidates, starred_articles=[], dismissed_articles=dismissed)

    call_prompt = mock_gemini.generate_text.call_args[0][0]
    assert "Dismissed article title" in call_prompt


def test_score_articles_fallback_when_gemini_api_fails():
    """Gemini API 呼び出し自体の失敗はフォールバックスコアを返す"""
    mock_gemini = MagicMock()
    mock_gemini.generate_text.side_effect = Exception("API error")

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Some article")]

    scores = rec.score_articles(candidates, starred_articles=[], dismissed_articles=[])
    assert len(scores) == 1
    assert scores[0].score == 0.5  # フォールバックスコア


def test_score_articles_fallback_when_gemini_returns_invalid_json():
    """Gemini が不正 JSON を返した場合もフォールバックスコアを返す（API 障害とは区別）"""
    mock_gemini = MagicMock()
    mock_gemini.generate_text.return_value = "This is not JSON at all"

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Some article")]
    scores = rec.score_articles(candidates, starred_articles=[], dismissed_articles=[])

    assert len(scores) == 1
    assert scores[0].score == 0.5


def test_score_articles_with_no_history_returns_default_scores():
    mock_gemini = MagicMock()
    mock_gemini.generate_text.return_value = json.dumps([
        {"id": "a1", "score": 0.5},
    ])

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Some article")]

    scores = rec.score_articles(candidates, starred_articles=[], dismissed_articles=[])
    assert len(scores) == 1
