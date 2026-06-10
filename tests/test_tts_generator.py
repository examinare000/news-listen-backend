from unittest.mock import MagicMock
from jobs.podcast_generator.script_generator import PodcastScript


def test_generate_audio_concatenates_intro_and_body():
    mock_gemini = MagicMock()
    # 日本語イントロの音声: 100 bytes、英語本編: 200 bytes
    mock_gemini.generate_tts.side_effect = [b"\x00" * 100, b"\x01" * 200]

    from jobs.podcast_generator.tts_generator import TtsGenerator
    gen = TtsGenerator(gemini_client=mock_gemini)

    script = PodcastScript(
        japanese_intro="今日は2026年5月31日です。",
        english_body="Today we discuss Rust programming language.",
    )

    audio = gen.generate_audio(script)

    assert len(audio) > 0
    # TTS が2回呼ばれる（日本語イントロ + 英語本編）
    assert mock_gemini.generate_tts.call_count == 2


def test_generate_audio_uses_different_voices_for_languages():
    mock_gemini = MagicMock()
    mock_gemini.generate_tts.side_effect = [b"\x00" * 100, b"\x01" * 200]

    from jobs.podcast_generator.tts_generator import TtsGenerator
    gen = TtsGenerator(gemini_client=mock_gemini)

    script = PodcastScript(japanese_intro="日本語", english_body="English")
    gen.generate_audio(script)

    calls = mock_gemini.generate_tts.call_args_list
    # 1回目（日本語）と2回目（英語）で voice が異なること
    assert calls[0][1]["voice"] != calls[1][1]["voice"]
