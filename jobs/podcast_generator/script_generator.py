"""Gemini API を使ったポッドキャストスクリプト生成。"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from shared.gemini_client import GeminiClient
from shared.models import Article

logger = logging.getLogger(__name__)

_DIFFICULTY_INSTRUCTIONS: dict[str, str] = {
    "toeic_600": "中学〜高校基本語彙のみ使用。短文・単純な文構造。ゆっくりと明瞭に話すイメージで書く。",
    "toeic_900": "ビジネス基本語彙。複合文あり。標準的な英語ニュース番組レベル。",
    "ielts_55":  "アカデミック語彙を含む。接続詞・節を使った文。NPR ラジオレベル。",
    "ielts_7":   "高度な学術・専門語彙。複雑な文構造。ネイティブスピード。",
    "eiken_2":   "日常〜社会的話題の語彙。標準的な英文。英検2級のリスニング問題レベル。",
    "eiken_p1":  "時事・専門語彙。論説文レベルの複雑な文構造。英検準1級レベル。",
}

_SCRIPT_PROMPT = """\
あなたはポッドキャストの台本ライターです。以下の記事からポッドキャスト台本を作成してください。

【対象記事】
タイトル: {title}
本文:
{content}

【関連記事】
{related}

【難易度指示】
{difficulty_instruction}

【出力フォーマット（必ずこの形式で出力）】
===JAPANESE_INTRO===
（ここに日本語イントロを書く。日付「{date_str}」から始め、記事の概要を1〜5センテンスで紹介する。）

===ENGLISH_BODY===
（ここに英語本編を書く。上記の難易度指示に従うこと。関連記事の内容も自然に組み込むこと。3〜8分相当のテキスト量。）
"""


@dataclass
class PodcastScript:
    japanese_intro: str
    english_body: str


class ScriptGenerator:
    def __init__(self, gemini_client: GeminiClient | None = None) -> None:
        self._gemini = gemini_client or GeminiClient()

    def generate(
        self,
        main_article: Article,
        related_articles: list[Article],
        difficulty: str,
        date_str: str,
    ) -> PodcastScript:
        related_text = "\n".join(
            f"- {a.title}: {a.content[:300]}" for a in related_articles
        ) or "なし"

        prompt = _SCRIPT_PROMPT.format(
            title=main_article.title,
            content=main_article.content[:3000],
            related=related_text,
            difficulty_instruction=_DIFFICULTY_INSTRUCTIONS.get(difficulty, difficulty),
            date_str=date_str,
        )

        raw = self._gemini.generate_text(prompt, temperature=0.8)

        intro = ""
        body = ""
        if "===JAPANESE_INTRO===" in raw and "===ENGLISH_BODY===" in raw:
            parts = raw.split("===ENGLISH_BODY===")
            intro = parts[0].replace("===JAPANESE_INTRO===", "").strip()
            body = parts[1].strip()
        else:
            logger.warning("Script format not found, using raw output as body")
            body = raw

        return PodcastScript(japanese_intro=intro, english_body=body)
