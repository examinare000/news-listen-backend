"""パスワードハッシュとセッショントークンのセキュリティヘルパー。

- パスワードは bcrypt でソルト付きハッシュ化する（`agent-rules/12-security-guidelines.md`）。
- セッショントークンは推測不能な乱数で生成し、DB には **SHA-256 ハッシュ** のみを保存する。
  これにより Firestore 流出時も生のトークンが漏れない（パスワード同様の最小権限原則）。

平文パスワード・生トークンはログに出力しないこと。
"""
from __future__ import annotations

import hashlib
import secrets

import bcrypt

# bcrypt は 72 バイトを超える入力を受け付けない（4.x 系は例外送出）。
# 仕様上のパスワード最大長はスキーマ側でも制限するが、多バイト文字で 72 バイトを
# 超えうるため、ハッシュ生成時にバイト列を 72 に切り詰めて実行時エラーを防ぐ。
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    """平文パスワードを bcrypt ハッシュ（utf-8 文字列）に変換する。"""
    payload = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(payload, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """平文パスワードがハッシュと一致するか検証する。

    不正なハッシュ形式（破損データ等）では例外を握りつぶして False を返す。
    """
    payload = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(payload, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_session_token() -> str:
    """推測不能なセッショントークン（URL セーフな乱数）を生成する。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """セッショントークンを SHA-256 でハッシュ化し、DB 保存用のキーにする。

    トークンは高エントロピーな乱数のため、パスワードと異なりソルト/ストレッチは不要。
    決定論的ハッシュにすることで、受領トークンから O(1) でセッションを引ける。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
