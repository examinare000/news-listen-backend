"""Gemini API wrapper for text generation and TTS."""
from __future__ import annotations

import os

from google import genai
from google.genai import types


class GeminiClient:
    TEXT_MODEL = "gemini-2.5-flash"
    TTS_MODEL = "gemini-2.5-flash-preview-tts"

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.environ["GEMINI_API_KEY"]
        self._client = genai.Client(api_key=key)

    def generate_text(self, prompt: str, temperature: float = 0.7) -> str:
        response = self._client.models.generate_content(
            model=self.TEXT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=temperature),
        )
        return response.text

    def generate_tts(self, text: str, voice: str = "Kore") -> bytes:
        """テキストを音声バイト列（PCM）に変換する。

        voice 名: Kore（女性・落ち着いた）, Puck（男性・明瞭）

        Raises:
            ValueError: Gemini が音声データを返さなかった場合。
                candidates が空・parts が空・inline_data が None のいずれかのケースを
                明示的なエラーメッセージで区別して送出する（スタックトレースなしのクラッシュを防ぐ）。
        """
        response = self._client.models.generate_content(
            model=self.TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice
                        )
                    )
                ),
            ),
        )

        # 防御的アクセス: Gemini が想定外のレスポンス構造を返した際に
        # 意味のあるエラーメッセージで失敗させる
        if not response.candidates:
            raise ValueError(
                f"Gemini TTS returned no candidates for voice={voice!r}. "
                "Verify the TTS model name and API key."
            )

        # candidate.content は SAFETY 等でブロックされた際に None になり得る。
        # ガードなしで .parts へアクセスすると AttributeError でクラッシュするため明示的に弾く。
        content = response.candidates[0].content
        if content is None:
            raise ValueError(
                f"Gemini TTS returned a candidate without content for voice={voice!r} "
                "(likely blocked by safety filters)."
            )

        parts = content.parts
        if not parts:
            raise ValueError(
                f"Gemini TTS returned no audio parts for voice={voice!r}."
            )

        inline = parts[0].inline_data
        if inline is None or inline.data is None:
            raise ValueError(
                f"Gemini TTS returned no inline audio data for voice={voice!r}."
            )

        return inline.data
