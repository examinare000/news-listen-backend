import pytest
from unittest.mock import MagicMock
from jobs.podcast_generator.script_generator import PodcastScript
from jobs.podcast_generator.tts_generator import TtsGenerator, TtsResult, TtsGenerationError


def test_generate_audio_concatenates_intro_and_body():
    """全成功: 日本語・英語の音声を順序維持で連結。TtsResult が failed_segments=[] を返す。"""
    mock_gemini = MagicMock()
    # 日本語イントロの音声: 100 bytes、英語本編: 200 bytes
    mock_gemini.generate_tts.side_effect = [b"\x00" * 100, b"\x01" * 200]

    gen = TtsGenerator(gemini_client=mock_gemini)

    script = PodcastScript(
        japanese_intro="今日は2026年5月31日です。",
        english_body="Today we discuss Rust programming language.",
    )

    result = gen.generate_audio(script)

    assert isinstance(result, TtsResult)
    assert result.audio == b"\x00" * 100 + b"\x01" * 200
    assert result.failed_segments == []
    assert result.error_message is None
    # TTS が2回呼ばれる（日本語イントロ + 英語本編）
    assert mock_gemini.generate_tts.call_count == 2


def test_generate_audio_uses_different_voices_for_languages():
    """全成功時に日本語・英語で異なる voice が使われることを確認。"""
    mock_gemini = MagicMock()
    mock_gemini.generate_tts.side_effect = [b"\x00" * 100, b"\x01" * 200]

    gen = TtsGenerator(gemini_client=mock_gemini)

    script = PodcastScript(japanese_intro="日本語", english_body="English")
    result = gen.generate_audio(script)

    assert isinstance(result, TtsResult)
    calls = mock_gemini.generate_tts.call_args_list
    # 1回目（日本語）と2回目（英語）で voice が異なること
    assert calls[0][1]["voice"] != calls[1][1]["voice"]


def test_generate_audio_partial_failure():
    """一部失敗: 日本語成功・英語失敗。失敗セグメント名と error_message を記録。"""
    mock_gemini = MagicMock()
    # 日本語は成功（100 bytes）、英語は max_attempts 回 ValueError で失敗
    # max_attempts=2 なので英語は2回 ValueError を投げる
    mock_gemini.generate_tts.side_effect = [
        b"\x00" * 100,  # 日本語成功
        ValueError("TTS service unavailable"),  # 英語 1回目
        ValueError("TTS service unavailable"),  # 英語 2回目
    ]

    gen = TtsGenerator(gemini_client=mock_gemini)

    script = PodcastScript(
        japanese_intro="今日は2026年5月31日です。",
        english_body="Today we discuss Rust programming language.",
    )

    result = gen.generate_audio(script)

    assert isinstance(result, TtsResult)
    assert result.audio == b"\x00" * 100  # 成功分のみ
    assert result.failed_segments == ["english_body"]
    assert result.error_message == "TTS failed for segments: english_body"


def test_generate_audio_all_failure_raises_exception():
    """全失敗: TtsGenerationError を送出（リトライなし）。"""
    mock_gemini = MagicMock()
    # 両セグメントで ValueError
    mock_gemini.generate_tts.side_effect = ValueError("TTS service unavailable")

    gen = TtsGenerator(gemini_client=mock_gemini)

    script = PodcastScript(
        japanese_intro="今日は2026年5月31日です。",
        english_body="Today we discuss Rust programming language.",
    )

    with pytest.raises(TtsGenerationError):
        gen.generate_audio(script)


def test_generate_audio_retry_success():
    """リトライ成功: 1回目失敗・2回目成功。max_attempts=2 で成功。"""
    mock_gemini = MagicMock()
    # 日本語: 成功
    # 英語: 1回目 ValueError、2回目 成功
    mock_gemini.generate_tts.side_effect = [
        b"\x00" * 100,  # 日本語成功
        ValueError("TTS service unavailable"),  # 英語 1回目失敗
        b"\x01" * 200,  # 英語 2回目成功
    ]

    gen = TtsGenerator(gemini_client=mock_gemini)

    script = PodcastScript(
        japanese_intro="今日は2026年5月31日です。",
        english_body="Today we discuss Rust programming language.",
    )

    result = gen.generate_audio(script, max_attempts=2)

    assert isinstance(result, TtsResult)
    assert result.audio == b"\x00" * 100 + b"\x01" * 200
    assert result.failed_segments == []
    assert result.error_message is None
    # 呼び出し3回: 日本語1回 + 英語2回（リトライ1回）
    assert mock_gemini.generate_tts.call_count == 3


def test_generate_audio_retry_exhausted():
    """リトライ枯渇: max_attempts=2 で両回失敗。failed_segments に記録。"""
    mock_gemini = MagicMock()
    # 英語が常に ValueError
    mock_gemini.generate_tts.side_effect = [
        b"\x00" * 100,  # 日本語成功
        ValueError("TTS service unavailable"),  # 英語 1回目
        ValueError("TTS service unavailable"),  # 英語 2回目
    ]

    gen = TtsGenerator(gemini_client=mock_gemini)

    script = PodcastScript(
        japanese_intro="今日は2026年5月31日です。",
        english_body="Today we discuss Rust programming language.",
    )

    result = gen.generate_audio(script, max_attempts=2)

    assert isinstance(result, TtsResult)
    assert result.audio == b"\x00" * 100  # 日本語のみ
    assert result.failed_segments == ["english_body"]
    assert "english_body" in result.error_message
    # 呼び出し3回: 日本語1回 + 英語2回（max_attempts=2）
    assert mock_gemini.generate_tts.call_count == 3


def test_generate_audio_non_value_error_raises_immediately():
    """非ValueError は即送出: RuntimeError が投げられて、呼び出し1回で終了。"""
    mock_gemini = MagicMock()
    # 英語が RuntimeError を投げる（ValueError でない）
    mock_gemini.generate_tts.side_effect = [
        b"\x00" * 100,  # 日本語成功
        RuntimeError("TTS service internal error"),  # 英語で RuntimeError
    ]

    gen = TtsGenerator(gemini_client=mock_gemini)

    script = PodcastScript(
        japanese_intro="今日は2026年5月31日です。",
        english_body="Today we discuss Rust programming language.",
    )

    with pytest.raises(RuntimeError):
        gen.generate_audio(script, max_attempts=2)

    # 呼び出し2回: 日本語1回 + 英語1回（RuntimeError で即送出）
    assert mock_gemini.generate_tts.call_count == 2
