from unittest.mock import MagicMock
from shared.models import Article
from datetime import datetime, timezone


def _make_article(title="Test Article", content="This is the article content about Rust programming."):
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    return Article(
        id="a1",
        title=title,
        url="https://example.com/a1",
        source="hackernews",
        content=content,
        published_at=now,
        fetched_at=now,
    )


def test_generate_returns_script_with_japanese_intro_and_english_body():
    mock_gemini = MagicMock()
    mock_gemini.generate_text.return_value = (
        "===JAPANESE_INTRO===\n"
        "2026年5月31日。本日のニュースはRustプログラミング言語についてです。\n"
        "===ENGLISH_BODY===\n"
        "Today we're talking about Rust, a systems programming language..."
    )

    from jobs.podcast_generator.script_generator import ScriptGenerator
    gen = ScriptGenerator(gemini_client=mock_gemini)

    script = gen.generate(
        main_article=_make_article(),
        related_articles=[],
        difficulty="toeic_900",
        date_str="2026-05-31",
    )

    assert script.japanese_intro != ""
    assert script.english_body != ""
    assert "Rust" in script.english_body or "Rust" in script.japanese_intro


def test_generate_uses_difficulty_in_prompt():
    mock_gemini = MagicMock()
    mock_gemini.generate_text.return_value = (
        "===JAPANESE_INTRO===\nイントロ\n===ENGLISH_BODY===\nBody"
    )

    from jobs.podcast_generator.script_generator import ScriptGenerator
    gen = ScriptGenerator(gemini_client=mock_gemini)

    gen.generate(
        main_article=_make_article(),
        related_articles=[],
        difficulty="ielts_7",
        date_str="2026-05-31",
    )

    call_args = mock_gemini.generate_text.call_args[0][0]
    # "ielts_7" に対応する難易度指示（日本語）がプロンプトに含まれること
    assert "ネイティブスピード" in call_args or "ielts" in call_args.lower()
