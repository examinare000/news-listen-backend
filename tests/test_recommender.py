import json
import logging
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
    # 新実装では generate_text_with_usage を使用するため、その戻り値を設定
    from shared.gemini_client import TextGenerationResult
    mock_gemini.find_cached_content.return_value = None
    mock_gemini.create_cached_content.return_value = None
    mock_gemini.generate_text_with_usage.return_value = TextGenerationResult(
        text=json.dumps([
            {"id": "a1", "score": 0.9},
            {"id": "a2", "score": 0.3},
        ]),
        prompt_token_count=100,
        cached_content_token_count=0,
    )

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
    from shared.gemini_client import TextGenerationResult
    mock_gemini.find_cached_content.return_value = None
    mock_gemini.create_cached_content.return_value = None
    mock_gemini.generate_text_with_usage.return_value = TextGenerationResult(
        text=json.dumps([
            {"id": "a1", "score": 0.5},
        ]),
        prompt_token_count=100,
        cached_content_token_count=0,
    )

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Some article")]
    dismissed = [_make_article("d1", "Dismissed article title")]

    rec.score_articles(candidates, starred_articles=[], dismissed_articles=dismissed)

    # create_cached_content が呼ばれる際に system_instruction にプロンプトが含まれている
    call_args = mock_gemini.create_cached_content.call_args
    system_instruction = call_args[1]["system_instruction"]
    assert "Dismissed article title" in system_instruction


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


def test_score_articles_filters_out_unknown_ids_from_gemini():
    """Gemini が候補に存在しない ID（幻覚）を返した場合、その ID は結果から除外される"""
    mock_gemini = MagicMock()
    mock_gemini.generate_text.return_value = json.dumps([
        {"id": "a1", "score": 0.9},
        {"id": "hallucinated", "score": 0.7},  # candidates に存在しない ID
    ])

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Rust is fast")]

    scores = rec.score_articles(candidates, starred_articles=[], dismissed_articles=[])

    assert len(scores) == 1
    assert scores[0].article_id == "a1"


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


# === T4: Cache 経路配線 ===


def test_score_articles_uses_cached_content_when_available():
    """既存キャッシュが見つかった場合、find_cached_content で取得したキャッシュ名を
    generate_text_with_usage に渡す"""
    mock_gemini = MagicMock()

    # キャッシュ検索で見つかった場合のシミュレーション
    mock_gemini.find_cached_content.return_value = "projects/test/locations/us-central1/cachedContents/cache123"
    # キャッシュを使用した生成で TextGenerationResult を返す
    from shared.gemini_client import TextGenerationResult
    mock_gemini.generate_text_with_usage.return_value = TextGenerationResult(
        text=json.dumps([{"id": "a1", "score": 0.8}]),
        prompt_token_count=50,
        cached_content_token_count=100,
    )

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Rust article")]
    starred = [_make_article("s1", "Go article")]
    dismissed = []

    scores = rec.score_articles(candidates, starred_articles=starred, dismissed_articles=dismissed)

    # キャッシュが使用されたことを確認
    mock_gemini.find_cached_content.assert_called_once()
    # cached_content 名が generate_text_with_usage に渡されたことを確認
    call_kwargs = mock_gemini.generate_text_with_usage.call_args[1]
    assert call_kwargs.get("cached_content") == "projects/test/locations/us-central1/cachedContents/cache123"

    assert len(scores) == 1
    assert scores[0].score == 0.8


def test_score_articles_creates_cache_when_not_found():
    """キャッシュが見つからない場合、create_cached_content で新規作成し、
    その名前を generate_text_with_usage に渡す"""
    mock_gemini = MagicMock()

    # キャッシュ未検出
    mock_gemini.find_cached_content.return_value = None
    # 新規作成で cache123 を返す
    mock_gemini.create_cached_content.return_value = "projects/test/locations/us-central1/cachedContents/cache123"
    # キャッシュを使用した生成
    from shared.gemini_client import TextGenerationResult
    mock_gemini.generate_text_with_usage.return_value = TextGenerationResult(
        text=json.dumps([{"id": "a1", "score": 0.7}]),
        prompt_token_count=50,
        cached_content_token_count=0,
    )

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Article")]
    starred = [_make_article("s1", "Starred")]
    dismissed = []

    scores = rec.score_articles(candidates, starred_articles=starred, dismissed_articles=dismissed)

    # create_cached_content が呼ばれたことを確認
    mock_gemini.create_cached_content.assert_called_once()
    # cache 名が generate_text_with_usage に渡されたことを確認
    call_kwargs = mock_gemini.generate_text_with_usage.call_args[1]
    assert call_kwargs.get("cached_content") == "projects/test/locations/us-central1/cachedContents/cache123"

    assert len(scores) == 1


def test_score_articles_fallback_to_normal_prompt_when_cache_creation_fails():
    """create_cached_content が None を返した場合（min-token 未満など），
    キャッシュなしで通常プロンプトを使用し、結果は不変"""
    mock_gemini = MagicMock()

    # キャッシュ未検出
    mock_gemini.find_cached_content.return_value = None
    # キャッシュ作成失敗（min-token 未満など）
    mock_gemini.create_cached_content.return_value = None
    # フォールバック: 通常プロンプトで呼び出し
    from shared.gemini_client import TextGenerationResult
    mock_gemini.generate_text_with_usage.return_value = TextGenerationResult(
        text=json.dumps([{"id": "a1", "score": 0.6}]),
        prompt_token_count=150,
        cached_content_token_count=0,
    )

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Article")]
    starred = [_make_article("s1", "Starred")]
    dismissed = []

    scores = rec.score_articles(candidates, starred_articles=starred, dismissed_articles=dismissed)

    # cached_content=None でフォールバック呼び出し
    call_kwargs = mock_gemini.generate_text_with_usage.call_args[1]
    assert call_kwargs.get("cached_content") is None

    assert len(scores) == 1
    assert scores[0].score == 0.6


# === T5: 計測（cache ヒット検証） ===


def test_score_articles_logs_cached_content_token_count_when_cache_hit(caplog):
    """キャッシュヒット時（cached_content_token_count > 0）、ログに記録される"""
    mock_gemini = MagicMock()

    # キャッシュが見つかった
    mock_gemini.find_cached_content.return_value = "cache-name"
    # キャッシュを使用した結果で cached_content_token_count > 0
    from shared.gemini_client import TextGenerationResult
    mock_gemini.generate_text_with_usage.return_value = TextGenerationResult(
        text=json.dumps([{"id": "a1", "score": 0.8}]),
        prompt_token_count=50,
        cached_content_token_count=1000,
    )

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Article")]
    starred = [_make_article("s1", "Starred")]

    with caplog.at_level(logging.INFO):
        scores = rec.score_articles(candidates, starred_articles=starred, dismissed_articles=[])

    # ログに cached_content_token_count が記録されている
    assert any("recommendation tokens" in record.message for record in caplog.records)
    assert any("cached=1000" in record.message for record in caplog.records)
    assert len(scores) == 1


# === T6（非回帰・最重要）: スコア結果が cache 有無で不変 ===


def test_score_articles_result_unchanged_with_cache():
    """キャッシュを使用した場合でも、スコア結果は同じ（非回帰）"""
    mock_gemini = MagicMock()

    # キャッシュなし → 通常プロンプトで呼び出し
    mock_gemini.find_cached_content.return_value = None
    mock_gemini.create_cached_content.return_value = None
    from shared.gemini_client import TextGenerationResult
    mock_gemini.generate_text_with_usage.return_value = TextGenerationResult(
        text=json.dumps([{"id": "a1", "score": 0.9}, {"id": "a2", "score": 0.3}]),
        prompt_token_count=150,
        cached_content_token_count=0,
    )

    from jobs.recommendation.recommender import Recommender
    rec = Recommender(gemini_client=mock_gemini)

    candidates = [_make_article("a1", "Rust"), _make_article("a2", "Celebrity")]
    starred = [_make_article("s1", "Go")]

    # キャッシュなしで実行
    scores_without_cache = rec.score_articles(
        candidates, starred_articles=starred, dismissed_articles=[]
    )

    # キャッシュありで実行（ユーザーが同じ starred_articles なので同じ cache が見つかる）
    mock_gemini.find_cached_content.return_value = "cache-found"
    mock_gemini.generate_text_with_usage.return_value = TextGenerationResult(
        text=json.dumps([{"id": "a1", "score": 0.9}, {"id": "a2", "score": 0.3}]),
        prompt_token_count=50,
        cached_content_token_count=100,
    )

    scores_with_cache = rec.score_articles(
        candidates, starred_articles=starred, dismissed_articles=[]
    )

    # スコアの値が同じ（トークン数は異なるが、スコア自体は不変）
    assert len(scores_without_cache) == len(scores_with_cache)
    assert scores_without_cache[0].article_id == scores_with_cache[0].article_id
    assert scores_without_cache[0].score == scores_with_cache[0].score
    assert scores_without_cache[1].article_id == scores_with_cache[1].article_id
    assert scores_without_cache[1].score == scores_with_cache[1].score
