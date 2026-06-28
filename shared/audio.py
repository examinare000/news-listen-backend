"""音声フォーマット変換ヘルパー。

Gemini TTS（`shared/gemini_client.py`）は 24kHz・モノラル・16bit signed の
生 PCM を返す。生 PCM はヘッダを持たないため、そのまま audio/mpeg として
配信するとクライアントのプレイヤーがデコードできず再生不可になる（#50）。
標準ライブラリ `wave` のみで RIFF/WAVE コンテナを付与し、依存を増やさずに
再生可能な音声として配信できるようにする。
"""
from __future__ import annotations

import io
import wave

# Gemini TTS 出力の固定フォーマット（gemini_client.generate_tts 準拠）
PCM_SAMPLE_RATE = 24000  # Hz
PCM_CHANNELS = 1  # モノラル
PCM_SAMPLE_WIDTH = 2  # byte（16bit signed）


def pcm_to_wav(
    pcm_bytes: bytes,
    *,
    sample_rate: int = PCM_SAMPLE_RATE,
    channels: int = PCM_CHANNELS,
    sample_width: int = PCM_SAMPLE_WIDTH,
) -> bytes:
    """生 PCM バイト列に RIFF/WAVE ヘッダを付与して WAV バイト列を返す。

    ペイロード（サンプルデータ）は無変換でそのまま格納するため、音質の劣化は
    一切ない。空の PCM でもデータ長 0 の有効な WAV を返す（境界値）。
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)
    return buf.getvalue()
