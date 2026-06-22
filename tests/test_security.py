"""shared.security のユニットテスト。"""
from shared.security import (
    generate_session_token,
    hash_password,
    hash_token,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        h = hash_password("s3cret-pass")
        assert h != "s3cret-pass"
        # bcrypt ハッシュは $2 で始まる
        assert h.startswith("$2")

    def test_verify_correct_password(self):
        h = hash_password("s3cret-pass")
        assert verify_password("s3cret-pass", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("s3cret-pass")
        assert verify_password("wrong", h) is False

    def test_salt_makes_hashes_unique(self):
        """同じ平文でもソルトにより毎回異なるハッシュになる。"""
        assert hash_password("same") != hash_password("same")

    def test_verify_handles_broken_hash(self):
        """壊れたハッシュ形式では例外を送出せず False を返す。"""
        assert verify_password("any", "not-a-bcrypt-hash") is False

    def test_long_multibyte_password_does_not_raise(self):
        """72 バイトを超える多バイトパスワードでも例外なくハッシュ・検証できる。"""
        pw = "パスワード" * 20  # 多バイトで 72 バイト超
        h = hash_password(pw)
        assert verify_password(pw, h) is True


class TestSessionToken:
    def test_generate_token_is_unique(self):
        assert generate_session_token() != generate_session_token()

    def test_hash_token_is_deterministic(self):
        t = generate_session_token()
        assert hash_token(t) == hash_token(t)

    def test_hash_token_differs_from_raw(self):
        t = generate_session_token()
        assert hash_token(t) != t
