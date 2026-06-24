"""パスワード強度検証ポリシー。

このモジュールは DB / Network / Clock / Random に依存しない pure module。
ブロックリストは import 時に一度だけ読み込まれ、その後の実行では再読しない。

validate_password_strength(password, *, username=None) -> None
    パスワードの強度をチェックする。以下の順で検証：
    1. 最小長（12文字）
    2. 文字クラス数（3つ以上）
    3. ブロックリスト
    4. ユーザー名非包含

    すべて OK なら None を返す。失敗時は ValueError を raise（メッセージに平文PW不含）。
"""
from __future__ import annotations

from pathlib import Path


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

_MIN_LENGTH = 12
_MIN_CHAR_CLASSES = 3
# ASCII の印字可能な記号（句読点）一式。文字クラス判定に使う。
_SYMBOLS = frozenset(r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~""")
_DATA_PATH = Path(__file__).resolve().parent / "data" / "common_passwords.txt"


# ──────────────────────────────────────────────────────────────────────────────
# Blocklist loader (module-level initialization)
# ──────────────────────────────────────────────────────────────────────────────


def _load_blocklist() -> frozenset[str]:
    """パスワードブロックリストを読み込む（fail-open）。

    ファイルが存在しない場合は空の frozenset を返し、
    他の検証（長さ、文字クラス、ユーザー名）は機能する。
    """
    try:
        text = _DATA_PATH.read_text(encoding="utf-8")
    except OSError:
        # ファイル不在・読み取り不可時は fail-open（他の検証は機能する）
        return frozenset()
    # 空行とコメントは無視、小文字化
    return frozenset(
        line.strip().lower()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    )


_BLOCKLIST = _load_blocklist()


# ──────────────────────────────────────────────────────────────────────────────
# Validation logic
# ──────────────────────────────────────────────────────────────────────────────


def validate_password_strength(
    password: str, *, username: str | None = None
) -> None:
    """パスワードの強度をチェックする。

    Args:
        password: 検証対象のパスワード（平文）
        username: ユーザー名（指定時は非包含チェックを行う）

    Raises:
        ValueError: パスワードが要件を満たさない場合。メッセージに平文PW不含。

    Returns:
        None（すべての検証に合格した場合のみ）。
    """
    # 1. 最小長チェック
    if len(password) < _MIN_LENGTH:
        raise ValueError("password must be at least 12 characters long")

    # 2. 文字クラス数チェック
    has_lower = any(c.islower() for c in password)
    has_upper = any(c.isupper() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_symbol = any(
        (c in _SYMBOLS) or (not c.isalnum() and not c.isspace()) for c in password
    )

    class_count = sum([has_lower, has_upper, has_digit, has_symbol])
    if class_count < _MIN_CHAR_CLASSES:
        raise ValueError("password must contain at least 3 of: lowercase, uppercase, digit, symbol")

    # 3. ブロックリスト検証
    if password.lower() in _BLOCKLIST:
        raise ValueError("password is too common")

    # 4. ユーザー名非包含チェック
    # 前後空白を除去して照合する。空白付き username（例 "alice "）で
    # 部分文字列チェックをすり抜けられないようにする。
    username_stripped = username.strip() if username else ""
    if username_stripped:  # username が空・空白のみの場合は skip
        username_lower = username_stripped.lower()

        # 等価性チェック
        if password.lower() == username_lower:
            raise ValueError("password must not contain the username")

        # 部分文字列チェック（username が 4文字以上の場合のみ）
        if len(username_stripped) >= 4 and username_lower in password.lower():
            raise ValueError("password must not contain the username")


# 任意の module-level call を避けるため、_load_blocklist() は module 初期化時に実行済み。
