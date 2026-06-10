"""Gemini API を使ったコンテンツレコメンドエンジン。"""
from __future__ import annotations

import json
import logging

from shared.gemini_client import GeminiClient
from shared.models import Article, RecommendedArticle

logger = logging.getLogger(__name__)

_SCORE_PROMPT = """\
あなたはコンテンツレコメンドエンジンです。ユーザーの過去の行動から関心を分析し、候補記事にスコアを付けてください。

【ユーザーがStarした記事（好み）】
{starred}

【ユーザーがDismissした記事（非好み）】
{dismissed}

【スコアを付ける候補記事】
{candidates}

各候補記事に 0.0〜1.0 のスコアを付けてください。
1.0 = ユーザーが強く興味を持つ、0.0 = 全く興味を持たない。

必ず JSON 配列のみを返してください（他のテキスト不要）:
[{{"id": "記事ID", "score": 0.8}}, ...]
"""


def _extract_json(raw: str) -> str:
    """Gemini レスポンスから JSON 文字列を抽出する。

    Gemini が ```json ... ``` フェンスで囲んで返す場合に対応する。
    フェンスがない場合はそのまま返す。
    2つのブランチは同一ロジックなので len(parts) >= 2 で統合。
    """
    if "```" in raw:
        parts = raw.split("```")
        if len(parts) >= 2:
            # "```json\n[...]\n```" → parts[1] = "json\n[...]"
            return parts[1].removeprefix("json").strip()
    return raw.strip()


class Recommender:
    def __init__(self, gemini_client: GeminiClient | None = None) -> None:
        self._gemini = gemini_client or GeminiClient()

    def score_articles(
        self,
        candidates: list[Article],
        starred_articles: list[Article],
        dismissed_articles: list[Article],
    ) -> list[RecommendedArticle]:
        """候補記事にスコアを付ける。

        - starred_articles / dismissed_articles: 呼び出し元が ID → Article を解決して渡す。
          UserPrefs を引数に取らないのは、呼び出し元がすでにDB解決済みの Article リストを
          持っているため、二重のデータ依存を避けるため。
        """
        if not candidates:
            return []

        starred_titles = [a.title for a in starred_articles]
        dismissed_titles = [a.title for a in dismissed_articles]
        candidate_data = [
            {"id": a.id, "title": a.title, "source": a.source} for a in candidates
        ]

        prompt = _SCORE_PROMPT.format(
            starred=json.dumps(starred_titles, ensure_ascii=False) if starred_titles else "なし",
            dismissed=json.dumps(dismissed_titles, ensure_ascii=False) if dismissed_titles else "なし",
            candidates=json.dumps(candidate_data, ensure_ascii=False),
        )

        # --- Gemini API 呼び出し（ネットワーク障害は明示的に catch してフォールバック）---
        try:
            raw = self._gemini.generate_text(prompt, temperature=0.2)
        except Exception as e:
            logger.error("Gemini API call failed: %s", e)
            return [RecommendedArticle(article_id=a.id, score=0.5) for a in candidates]

        # --- JSON パース（不正フォーマットはフォールバック、プログラムエラーは再送出）---
        try:
            json_str = _extract_json(raw)
            scored = json.loads(json_str)
            # Gemini が候補集合に存在しない ID（幻覚）を返すことがあるため、
            # 元の candidates に含まれる ID のみを採用し、永続化される推薦が
            # 入力候補と整合するようにする。
            valid_ids = {a.id for a in candidates}
            return [
                RecommendedArticle(article_id=s["id"], score=float(s["score"]))
                for s in scored
                if s["id"] in valid_ids
            ]
        except json.JSONDecodeError as e:
            logger.warning("Gemini returned invalid JSON: %s. Raw: %.200r", e, raw)
            return [RecommendedArticle(article_id=a.id, score=0.5) for a in candidates]
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Unexpected JSON structure from Gemini: %s. Raw: %.200r", e, raw)
            return [RecommendedArticle(article_id=a.id, score=0.5) for a in candidates]
