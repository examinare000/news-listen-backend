"""ストレージ管理：純粋関数（I/O なし）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from shared.models import Podcast

SHARED_CACHE_PREFIX = "podcasts/cache/"


def is_blob_deletable(podcast: Podcast) -> bool:
    """Podcast の blob が削除対象かを判定する（純粋関数・副作用なし）。

    一次判定:
    - type != "digest" → False（single/legacy は削除禁止）
    - audio_url が空 → False（processing行など）

    二次防御:
    - audio_url が SHARED_CACHE_PREFIX で始まる → False（digest でも共有 prefix は削除禁止）

    それ以外（digest の per-user blob）→ True

    Args:
        podcast: Podcast モデル

    Returns:
        True なら削除対象、False なら削除禁止
    """
    # 一次判定: type != "digest"
    if podcast.type != "digest":
        return False

    # 一次判定: audio_url が空
    if not podcast.audio_url:
        return False

    # 二次防御: shared cache prefix
    if podcast.audio_url.startswith(SHARED_CACHE_PREFIX):
        return False

    # digest の per-user blob → 削除対象
    return True


def select_podcasts_to_delete(
    podcasts: list[Podcast],
    older_than_days: int | None,
    now: datetime,
) -> list[Podcast]:
    """削除対象の Podcast を日数でフィルタリングする（純粋関数・副作用なし）。

    Args:
        podcasts: フィルタ対象の Podcast リスト
        older_than_days:
            - None: 全件を返す
            - 整数 N: created_at < now - N日 のみ（境界: < 厳密・ちょうど N 日前は除外）
        now: 現在時刻（UTC）

    Returns:
        フィルタ済み Podcast リスト（順序は入力と同じ）
    """
    if older_than_days is None:
        return podcasts

    # now - N日より前の Podcast のみ（< 厳密）
    cutoff = now - timedelta(days=older_than_days)
    return [p for p in podcasts if p.created_at < cutoff]
