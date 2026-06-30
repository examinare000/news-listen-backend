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
===TITLE===
（ここにニュース内容を1センテンスに要約した日本語のタイトルを書く。記号や鉤括弧で囲まない。）

===JAPANESE_INTRO===
（ここに日本語イントロを書く。週刊ニュース番組のような番組設定・演出は不要。「こんにちは。」という挨拶で始め、日付「{date_str}」に軽く触れたうえで、「今回取り上げるニュースは〜」と続けて記事の概要を1〜5センテンスで紹介する。）

===ENGLISH_BODY===
（ここに英語本編を書く。上記の難易度指示に従うこと。関連記事の内容も自然に組み込むこと。3〜8分相当のテキスト量。）
"""

_DIGEST_SCRIPT_PROMPT = """\
あなたはポッドキャストの台本ライターです。複数の記事をまとめた日次ダイジェストの台本を作成してください。

【対象記事】
{articles_content}

【難易度指示】
{difficulty_instruction}

【出力フォーマット（必ずこの形式で出力）】
===TITLE===
（ここにダイジェスト内容を1センテンスに要約した日本語のタイトルを書く。記号や鉤括弧で囲まない。）

===JAPANESE_INTRO===
（ここに日本語イントロを書く。週刊ニュース番組のような番組設定・演出は不要。「こんにちは。」という挨拶で始め、日付「{date_str}」に軽く触れたうえで、「今回取り上げるニュースは〜」と続けて本日のダイジェスト概要を1〜3センテンスで紹介する。）

===ENGLISH_BODY===
（ここに英語本編を書く。上記の難易度指示に従うこと。複数の記事を coherent な流れで紹介する。5〜15分相当のテキスト量。）
"""


@dataclass
class PodcastScript:
    title: str
    japanese_intro: str
    english_body: str


class ScriptGenerator:
    def __init__(self, gemini_client: GeminiClient | None = None) -> None:
        self._gemini = gemini_client or GeminiClient()

    def _parse_script(self, raw: str) -> PodcastScript:
        """生スクリプトから PodcastScript を解析する（抽出された純粋関数）。

        ===TITLE===...===JAPANESE_INTRO===...===ENGLISH_BODY===... の形式をパースする。
        ===TITLE=== が無い場合は title="" とする（既存 Gemini 出力への後方互換）。
        ===JAPANESE_INTRO=== / ===ENGLISH_BODY=== が無い場合、または順序が乱れている場合は
        body に raw を割り当ててグレースフルに縮退する（例外は投げない）。

        WHY: str.split(sep, 1) の 2 要素アンパックはセパレータ未発見時に
        ValueError を出す。partition() は常に 3 要素タプルを返し、セパレータ発見の
        有無を sep フィールドの空文字で判定できるため安全。

        Args:
            raw: Gemini から返された生スクリプトテキスト

        Returns:
            PodcastScript
        """
        _TITLE = "===TITLE==="
        _INTRO = "===JAPANESE_INTRO==="
        _BODY  = "===ENGLISH_BODY==="

        # 必須マーカーが揃っているか確認
        if _INTRO not in raw or _BODY not in raw:
            logger.warning("Script format not found, using raw output as body")
            return PodcastScript(title="", japanese_intro="", english_body=raw)

        idx_intro = raw.index(_INTRO)
        idx_body  = raw.index(_BODY)

        # 順序検証: INTRO が BODY より前に来ること
        if idx_intro >= idx_body:
            logger.warning("Script format out of order, using raw output as body")
            return PodcastScript(title="", japanese_intro="", english_body=raw)

        # ===TITLE=== が ===JAPANESE_INTRO=== より前に出現する場合のみタイトルとして採用。
        # TITLE が INTRO より後に出現する場合は本文への混入とみなし無視する。
        title = ""
        if _TITLE in raw and raw.index(_TITLE) < idx_intro:
            _, _, after_title = raw.partition(_TITLE)
            title_part, sep, rest = after_title.partition(_INTRO)
            if not sep:
                # 理論上ここには来ないが防御的に縮退
                logger.warning("Script format not found, using raw output as body")
                return PodcastScript(title="", japanese_intro="", english_body=raw)
            title = title_part.strip()
        else:
            _, _, rest = raw.partition(_INTRO)

        # rest = ===JAPANESE_INTRO=== 以降のテキスト
        intro_part, sep2, body_part = rest.partition(_BODY)
        if not sep2:
            # ENGLISH_BODY が rest に無い（構造崩壊）→ 縮退
            logger.warning("Script format not found, using raw output as body")
            return PodcastScript(title="", japanese_intro="", english_body=raw)

        return PodcastScript(
            title=title,
            japanese_intro=intro_part.strip(),
            english_body=body_part.strip(),
        )

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
        return self._parse_script(raw)

    def generate_digest(
        self,
        articles: list[Article],
        difficulty: str,
        date_str: str,
    ) -> PodcastScript:
        """複数記事をまとめた日次ダイジェスト台本を生成する。

        Args:
            articles: ダイジェストに含める記事リスト
            difficulty: 難易度（_DIFFICULTY_INSTRUCTIONS のキー）
            date_str: 日付文字列（"YYYY-MM-DD"）

        Returns:
            PodcastScript
        """
        articles_content = "\n".join(
            f"- {a.title}: {a.content[:500]}" for a in articles
        )

        prompt = _DIGEST_SCRIPT_PROMPT.format(
            articles_content=articles_content,
            difficulty_instruction=_DIFFICULTY_INSTRUCTIONS.get(difficulty, difficulty),
            date_str=date_str,
        )

        raw = self._gemini.generate_text(prompt, temperature=0.8)
        return self._parse_script(raw)
