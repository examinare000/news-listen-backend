"""パスワードリセットトークン検証（純粋関数）。

DB / Clock / Random に依存しない pure module。テスト容易性最優先。
"""
from __future__ import annotations

from datetime import datetime

from shared.models import PasswordResetToken


def verify_reset_token(record: PasswordResetToken, now: datetime) -> bool:
    """パスワードリセットトークンが有効か検証する（純粋関数）。

    条件：
    - used_at is None（未使用）
    - now < expires_at（期限内。now == expires_at は False）

    Args:
        record: PasswordResetToken モデルインスタンス
        now: 現在時刻（テスト容易性のため注入可能）

    Returns:
        True: 有効なトークン。False: 期限切れ・既用。
    """
    # 既に使用済みなら False
    if record.used_at is not None:
        return False

    # 期限切れ判定。now >= expires_at なら期限切れ（now == expires_at も False）
    if now >= record.expires_at:
        return False

    return True
