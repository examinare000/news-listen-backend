"""Gemini TTS を使った音声バイト生成。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from shared.gemini_client import GeminiClient
from jobs.podcast_generator.script_generator import PodcastScript

logger = logging.getLogger(__name__)

# Kore: 落ち着いた女性（日本語に自然）、Puck: 明瞭な男性（英語に自然）
_JP_VOICE = "Kore"
_EN_VOICE = "Puck"


class TtsGenerationError(Exception):
    """TTS 生成に全セグメント失敗。部分失敗は TtsResult.failed_segments で表現。"""
    pass


@dataclass(frozen=True)
class TtsResult:
    """TTS 生成結果。成功分のみ音声をアップロード・配信。失敗セグメント名を記録。

    Attributes:
        audio: 成功セグメントのみ順序維持で連結した PCM
        failed_segments: 失敗セグメント名。全成功なら []
        error_message: 一部失敗時のみ文言。全成功なら None
    """
    audio: bytes
    failed_segments: list[str]
    error_message: str | None


class TtsGenerator:
    def __init__(self, gemini_client: GeminiClient | None = None) -> None:
        self._gemini = gemini_client or GeminiClient()

    def generate_audio(self, script: PodcastScript, *, max_attempts: int = 2) -> TtsResult:
        """TTS 生成。セグメント単位でリトライ。

        Args:
            script: PodcastScript（japanese_intro, english_body）
            max_attempts: 各セグメントのリトライ上限（既定2）

        Returns:
            TtsResult: 成功分連結音声 + 失敗セグメント名リスト

        Raises:
            TtsGenerationError: 全セグメント失敗時（成功≥1あるときは TtsResult.failed_segments で表現）
        """
        # セグメント列挙（順序が連結順）
        segments = [
            ("japanese_intro", script.japanese_intro, _JP_VOICE),
            ("english_body", script.english_body, _EN_VOICE),
        ]

        audio_parts: list[bytes] = []
        failed_segments: list[str] = []

        for segment_name, text, voice in segments:
            segment_audio = None

            # リトライループ（成功までまたは max_attempts 使い切るまで）
            for attempt in range(max_attempts):
                try:
                    segment_audio = self._gemini.generate_tts(text, voice=voice)
                    break  # 成功
                except ValueError as e:
                    # ValueError（応答構造不正）のみリトライ。間欠失敗の再試行パターンを
                    # 追跡できるよう記録する（資格情報は generate_tts 例外に含まれない）。
                    logger.warning(
                        "TTS attempt %d/%d failed for segment %s: %s",
                        attempt + 1, max_attempts, segment_name, e,
                    )
                except Exception:
                    # ValueError 以外は即送出（RuntimeError 等）
                    raise

            if segment_audio is not None:
                audio_parts.append(segment_audio)
            else:
                # max_attempts 使い切った
                failed_segments.append(segment_name)

        # ケース判定
        if not failed_segments:
            # 全成功
            return TtsResult(
                audio=b"".join(audio_parts),
                failed_segments=[],
                error_message=None,
            )
        elif audio_parts:
            # 一部失敗（成功≥1かつ失敗≥1）
            error_msg = f"TTS failed for segments: {', '.join(failed_segments)}"
            return TtsResult(
                audio=b"".join(audio_parts),
                failed_segments=failed_segments,
                error_message=error_msg,
            )
        else:
            # 全失敗
            raise TtsGenerationError(f"TTS generation failed for all segments: {failed_segments}")
