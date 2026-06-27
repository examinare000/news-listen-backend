"""Gemini API wrapper for text generation and TTS."""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


def recommendation_cache_display_name(user_id: str, stable_context: str) -> str:
    """レコメンド用キャッシュの表示名を生成する（決定的）。

    Args:
        user_id: ユーザー ID。
        stable_context: 安定部分のテキスト（指示＋履歴）。

    Returns:
        str: 表示名（形式: rec-{user_id}-{hash[:16]}）。
    """
    # WHY: ハッシュを使用することで、長いテキストを短い ID に圧縮し、
    # 同じ内容なら常に同じ名前を生成する（クロスプロセス再利用が可能）。
    context_hash = hashlib.sha256(stable_context.encode()).hexdigest()[:16]
    return f"rec-{user_id}-{context_hash}"


@dataclass
class TextGenerationResult:
    """Gemini のテキスト生成レスポンスと利用トークン情報。"""
    text: str
    prompt_token_count: int
    cached_content_token_count: int


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

    def generate_text_with_usage(
        self,
        prompt: str,
        *,
        cached_content: str | None = None,
        temperature: float = 0.7,
    ) -> TextGenerationResult:
        """テキスト生成を実行し、トークン利用情報を含むレスポンスを返す。

        Args:
            prompt: 生成プロンプト。
            cached_content: キャッシュ名（caches.create で返された name）。None なら通常呼び出し。
            temperature: 温度パラメータ。

        Returns:
            TextGenerationResult: text、prompt_token_count、cached_content_token_count を含む結果。
                usage_metadata が無い場合は token_count を 0 に安全化。
        """
        response = self._client.models.generate_content(
            model=self.TEXT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                cached_content=cached_content,
                temperature=temperature,
            ),
        )

        # WHY: usage_metadata が無いケース（例：SDK 構造変更）に対応し、
        # AttributeError でクラッシュせず 0 で安全化。さらに google-genai は
        # キャッシュ未使用時に *_token_count を None で返すため、属性が存在しても
        # None なら 0 に畳み込む（`or 0`）。これを怠ると呼び出し側の
        # `cached_content_token_count > 0` が None との比較で TypeError になる。
        prompt_token_count = 0
        cached_content_token_count = 0
        if response.usage_metadata is not None:
            prompt_token_count = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            cached_content_token_count = (
                getattr(response.usage_metadata, "cached_content_token_count", 0) or 0
            )

        return TextGenerationResult(
            text=response.text,
            prompt_token_count=prompt_token_count,
            cached_content_token_count=cached_content_token_count,
        )

    def create_cached_content(
        self,
        system_instruction: str,
        *,
        display_name: str,
        ttl_seconds: int,
    ) -> str | None:
        """system_instruction をキャッシュし、キャッシュ名を返す。

        Args:
            system_instruction: キャッシュする system_instruction テキスト。
            display_name: キャッシュの表示名（find_cached_content で検索用）。
            ttl_seconds: TTL（秒単位）。

        Returns:
            str: キャッシュ名（caches.create が返した name）。
                 作成失敗や例外時は None を返し、warning をログする。
        """
        try:
            # WHY: ttl は Google API の duration 文字列形式（例："3600s"）で指定する。
            # google.genai の types は ttl: str パラメータを期待する。
            ttl_duration = f"{ttl_seconds}s"
            cached_content = self._client.caches.create(
                model=self.TEXT_MODEL,
                config=types.CreateCachedContentConfig(
                    systemInstruction=system_instruction,
                    displayName=display_name,
                    ttl=ttl_duration,
                ),
            )
            return cached_content.name
        except Exception as e:
            logger.warning(
                "Failed to create cached content (display_name=%r): %s",
                display_name,
                e,
            )
            return None

    def find_cached_content(self, display_name: str) -> str | None:
        """display_name が一致する既存キャッシュを探し、キャッシュ名を返す。

        Args:
            display_name: 検索する表示名。

        Returns:
            str: 見つかったキャッシュの name。
                 見つからない / 例外時は None を返し、必要に応じて warning をログする。
        """
        try:
            cached_contents = self._client.caches.list()
            for cached_content in cached_contents:
                if getattr(cached_content, "display_name", None) == display_name:
                    return cached_content.name
            return None
        except Exception as e:
            logger.warning(
                "Failed to list cached contents (display_name=%r): %s",
                display_name,
                e,
            )
            return None

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
