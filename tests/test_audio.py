"""PCM→WAV 変換ユーティリティのユニットテスト。

Gemini TTS は 24kHz モノラル 16bit の生 PCM を返す。これをそのまま audio/mpeg
として配信するとプレイヤーがデコードできず再生不可になる（#50）。RIFF/WAVE
コンテナを付与して audio/wav として配信できることを検証する。
"""
import io
import struct
import wave

from shared.audio import (
    PCM_CHANNELS,
    PCM_SAMPLE_RATE,
    PCM_SAMPLE_WIDTH,
    pcm_to_wav,
)


def _make_pcm(num_samples: int) -> bytes:
    """16bit signed PCM のダミーサンプル列を生成する。"""
    return struct.pack(f"<{num_samples}h", *range(num_samples))


def test_pcm_to_wav_prepends_riff_wave_header():
    """生 PCM の先頭に RIFF/WAVE ヘッダ（44 byte）が付与されること。"""
    pcm = _make_pcm(100)  # 100 サンプル × 2 byte = 200 byte
    wav = pcm_to_wav(pcm)

    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    # 標準 PCM WAV ヘッダは 44 byte。データ長は元の PCM と一致する。
    assert len(wav) == 44 + len(pcm)


def test_pcm_to_wav_sets_correct_format_fields():
    """fmt チャンクが 24000Hz / 1ch / 16bit を表すこと。"""
    pcm = _make_pcm(50)
    wav = pcm_to_wav(pcm)

    with wave.open(io.BytesIO(wav), "rb") as r:
        assert r.getframerate() == PCM_SAMPLE_RATE == 24000
        assert r.getnchannels() == PCM_CHANNELS == 1
        assert r.getsampwidth() == PCM_SAMPLE_WIDTH == 2
        assert r.readframes(r.getnframes()) == pcm


def test_pcm_to_wav_roundtrip_preserves_payload():
    """WAV から読み戻した PCM が元データと完全一致すること（劣化なし）。"""
    pcm = _make_pcm(1024)
    wav = pcm_to_wav(pcm)

    with wave.open(io.BytesIO(wav), "rb") as r:
        assert r.readframes(r.getnframes()) == pcm


def test_pcm_to_wav_handles_empty_payload():
    """空 PCM でも有効な（データ長 0 の）WAV を返すこと（境界値）。"""
    wav = pcm_to_wav(b"")

    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    with wave.open(io.BytesIO(wav), "rb") as r:
        assert r.getnframes() == 0
