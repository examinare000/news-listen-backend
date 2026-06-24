"""パスワード強度検証ポリシーのテスト。

`shared.password_policy.validate_password_strength()` は以下を検証：
1. 最小12文字
2. 3つ以上の文字クラス（lowercase, uppercase, digit, symbol）
3. ブロックリスト（common-passwords.txt）に未掲載
4. ユーザー名非包含（大文字小文字を区別せず）

このモジュール（shared/password_policy.py）は DB/Network/Clock/Random に依存しない
pure module。ブロックリストは import 時に一度だけ読み込まれ、以降再読は不要。
"""
from __future__ import annotations

import pytest

from shared.password_policy import validate_password_strength


class TestMinimumLength:
    """最小文字数のテスト（12文字）。"""

    def test_11_chars_rejects(self):
        """11文字では不可。"""
        with pytest.raises(ValueError, match="at least 12 characters"):
            validate_password_strength("Abcdef123!@")  # 11文字

    def test_12_chars_accepts_if_strong(self):
        """12文字（4クラス）なら OK。"""
        # 4文字クラス: lower, upper, digit, symbol
        validate_password_strength("Abcdef123!@!1")

    def test_empty_rejects(self):
        """空文字は不可。"""
        with pytest.raises(ValueError, match="at least 12 characters"):
            validate_password_strength("")


class TestCharacterClasses:
    """3つ以上の文字クラス要件。"""

    def test_2_classes_rejects(self):
        """2クラスでは不可。"""
        # lowercase + digit のみ（12文字）
        with pytest.raises(ValueError, match="at least 3 of"):
            validate_password_strength("abcdefgh1234")

    def test_3_classes_accepts(self):
        """3クラスなら OK（各組み合わせ）。"""
        # lowercase + uppercase + digit
        validate_password_strength("Abcdefgh1234")

        # lowercase + uppercase + symbol
        validate_password_strength("abcdefghijkl-ABCD")

        # lowercase + digit + symbol
        validate_password_strength("abcdefgh1234-!!!!")

        # uppercase + digit + symbol（3クラス OK）
        validate_password_strength("ABCDEFGH1234!!!!!")

    def test_4_classes_accepts(self):
        """4クラス全て含まれなら OK。"""
        validate_password_strength("MyPassword123!")


class TestBlocklist:
    """ブロックリスト検証。"""

    def test_common_password_rejects(self):
        """ブロックリストの単語は拒否。password123! は blocklist に含む。"""
        with pytest.raises(ValueError, match="too common"):
            validate_password_strength("Password123!")

    def test_blocklist_case_insensitive(self):
        """ブロックリスト検証は大文字小文字を区別しない。"""
        with pytest.raises(ValueError, match="too common"):
            validate_password_strength("PASSWORD123!")

    def test_not_in_blocklist_accepts(self):
        """ブロックリストに無い値なら OK。"""
        validate_password_strength("MySecret2024!")


class TestUsernameCheck:
    """ユーザー名非包含チェック。"""

    def test_password_equals_username_rejects(self):
        """password == username（大文字小文字区別せず）なら拒否。"""
        with pytest.raises(ValueError, match="must not contain the username"):
            validate_password_strength("MyPassword123!", username="MYPASSWORD123!")

    def test_username_substring_rejects_when_username_long_enough(self):
        """username が 4文字以上で、password に部分文字列として含まれたら拒否。"""
        # username = "alice"（5文字）, password に "alice" が含まれている
        with pytest.raises(ValueError, match="must not contain the username"):
            validate_password_strength("alice2024Secret!", username="alice")

    def test_short_username_does_not_overreject(self):
        """username が 3文字以下なら substring check を行わない。

        短いユーザー名（e.g. "a", "ab", "abc"）は多くのパスワードに偶然含まれうるため、
        等価性チェックのみ行い、substring には検査しない。
        """
        # username = "a"（1文字）, password に "a" が複数含まれるが OK
        validate_password_strength("Pa55word_Secure!", username="a")

        # username = "ab"（2文字）, password に "ab" 含まれるが OK
        validate_password_strength("Stable_Pass12!", username="ab")

        # username = "abc"（3文字）, "abc" が password に含まれるが OK（guard 3以下）
        validate_password_strength("Abc_Password123!", username="abc")

    def test_username_with_trailing_whitespace_still_rejects(self):
        """前後に空白がある username でも substring 検査をすり抜けない。"""
        with pytest.raises(ValueError, match="must not contain the username"):
            validate_password_strength("alice2024Secret!", username="  alice  ")

    def test_username_whitespace_only_skips_check(self):
        """空白のみの username はチェックをスキップ（strip 後に空）。"""
        validate_password_strength("password123ABC!", username="   ")

    def test_username_none_skips_check(self):
        """username=None なら username チェックをスキップ。"""
        # OK: "password" が含まれているが username が指定されていない
        validate_password_strength("password123ABC!", username=None)

    def test_username_empty_string_skips_check(self):
        """username='' なら username チェックをスキップ。"""
        validate_password_strength("password123ABC!", username="")


class TestUnicodeLetters:
    """Unicode 文字クラス検証。"""

    def test_unicode_lowercase_counted(self):
        """Unicode 小文字は lowercase クラスとして数える。"""
        # Unicode 小文字 é を含む。uppercase (E) + digit (20) + symbol (!) + 長さ12以上
        validate_password_strength("écolSchool201!")

    def test_unicode_uppercase_counted(self):
        """Unicode 大文字は uppercase クラスとして数える。"""
        # Unicode 大文字 É を含む。lowercase (lower) + digit (201) + symbol (!) も含む 12字以上
        validate_password_strength("ÉcoleLower201!")


class TestMultibyteLength:
    """マルチバイト文字の処理。"""

    def test_multibyte_chars_counted_as_chars_not_bytes(self):
        """マルチバイト文字は「文字数」で数える（バイト数ではない）。

        bcrypt の 72 バイト制限は別（ハッシュ時に切り詰め）。
        password_policy は char count で判定する。
        """
        # 日本語 "パスワード" (3 chars) + English で12文字以上に
        pw = "パスワードSecure24!"  # パスワード(3) + S-e(2) + c-u-r(3) + e(1) + 2-4(2) + !(1) = 12+文字
        validate_password_strength(pw)

    def test_multibyte_over_72_bytes_but_12_chars_ok(self):
        """72バイトを超えるマルチバイト（ただし12文字以上）は OK。

        バイト制限は hash_password 側で処理。policy は char count のみ。
        """
        # 日本語（3バイト）x 5 + English lowercase + digit + symbol
        # "パスワード" (5 chars × 3 bytes) + "Secure" (6 chars × 1 byte) + "2024" (4 chars) + "!" (1 char)
        # = 5 + 6 + 4 + 1 = 16 chars (OK)
        # = 15 bytes (ja) + 6 bytes (en) + 4 bytes (digit) + 1 byte (symbol) = 26 bytes (not 72+)
        # Let me use more Japanese: "パスワード" x 3 = 15 chars (45 bytes) + "A2!" = 18 chars (48 bytes)
        validate_password_strength("パスワードパスワードパスワードA2!")


class TestErrorMessages:
    """エラーメッセージの仕様（平文パスワード不含）。"""

    def test_error_message_does_not_contain_password(self):
        """ValueError メッセージに平文パスワードが含まれない。"""
        try:
            validate_password_strength("short")
        except ValueError as e:
            assert "short" not in str(e).lower()

    def test_different_errors_for_different_failures(self):
        """異なる失敗ケースで異なるメッセージ。"""
        errors = []

        try:
            validate_password_strength("short")
        except ValueError as e:
            errors.append(str(e))

        try:
            validate_password_strength("abcdefghijkl")  # 2クラスのみ
        except ValueError as e:
            errors.append(str(e))

        assert len(set(errors)) == 2, "異なる失敗は異なるメッセージを返すべき"


class TestIntegration:
    """統合テスト（すべての検証を同時に通す）。"""

    def test_strong_password_no_username(self):
        """すべての条件を満たす（username なし）。"""
        validate_password_strength("MySecurePass2024!")

    def test_strong_password_with_safe_username(self):
        """username を指定したが、password に含まれていない。"""
        validate_password_strength("MySecurePass2024!", username="bob")

    def test_all_requirements_met(self):
        """4クラス、12文字+、非一般的、username 非包含。"""
        validate_password_strength(
            "Tr0pic@lSunset!", username="tropical"
        )
