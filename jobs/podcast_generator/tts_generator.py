"""Gemini TTS を使った音声バイト生成。"""
from __future__ import annotations

from shared.gemini_client import GeminiClient
from jobs.podcast_generator.script_generator import PodcastScript

# Kore: 落ち着いた女性（日本語に自然）、Puck: 明瞭な男性（英語に自然）
_JP_VOICE = "Kore"
_EN_VOICE = "Puck"


class TtsGenerator:
    def __init__(self, gemini_client: GeminiClient | None = None) -> None:
        self._gemini = gemini_client or GeminiClient()

    def generate_audio(self, script: PodcastScript) -> bytes:
        jp_audio = self._gemini.generate_tts(script.japanese_intro, voice=_JP_VOICE)
        en_audio = self._gemini.generate_tts(script.english_body, voice=_EN_VOICE)
        # PCM バイト列を結合（同一サンプルレート・チャンネル数前提）
        return jp_audio + en_audio
