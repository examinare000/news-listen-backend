"""cache_key_for() のユニットテスト。

cache_key_for は article_id / difficulty / language を __ で連結した
Firestore doc-id 安全な文字列を返す。article_id（SHA-256 hex[:20]）と
difficulty（Literal 値）はいずれも __ を含まないため、セパレータで一意に分解できる。
"""


def test_cache_key_format_is_triple_underscore_separated():
    """cache_key は article_id__difficulty__language 形式であること。"""
    from shared.utils import cache_key_for

    result = cache_key_for("abc123def456789012", "toeic_900", "ja-en")

    parts = result.split("__")
    assert len(parts) == 3
    assert parts[0] == "abc123def456789012"
    assert parts[1] == "toeic_900"
    assert parts[2] == "ja-en"


def test_cache_key_is_deterministic():
    """同じ引数に対して常に同じキーを返すこと（Firestore doc-id の冪等性を保証）。"""
    from shared.utils import cache_key_for

    key1 = cache_key_for("abc123def456789012", "toeic_900", "ja-en")
    key2 = cache_key_for("abc123def456789012", "toeic_900", "ja-en")

    assert key1 == key2


def test_cache_key_differs_for_different_article_ids():
    from shared.utils import cache_key_for

    key1 = cache_key_for("article1aaaaaaaaaa", "toeic_900", "ja-en")
    key2 = cache_key_for("article2aaaaaaaaaa", "toeic_900", "ja-en")

    assert key1 != key2


def test_cache_key_differs_for_different_difficulties():
    from shared.utils import cache_key_for

    key1 = cache_key_for("art1abc123456789xx", "toeic_600", "ja-en")
    key2 = cache_key_for("art1abc123456789xx", "toeic_900", "ja-en")

    assert key1 != key2


def test_cache_key_differs_for_different_languages():
    from shared.utils import cache_key_for

    key1 = cache_key_for("art1abc123456789xx", "toeic_900", "ja-en")
    key2 = cache_key_for("art1abc123456789xx", "toeic_900", "ja-jp")

    assert key1 != key2


def test_cache_key_has_no_slash():
    """Firestore doc-id はスラッシュを含めないこと（コレクション階層と混同するため）。"""
    from shared.utils import cache_key_for

    key = cache_key_for("abc123def456789012", "toeic_900", "ja-en")

    assert "/" not in key


def test_cache_key_unique_decomposition():
    """__ セパレータで一意に分解できることを確認する。

    article_id（SHA-256 hex[:20]）と difficulty（Literal 値）は
    どちらも __ を含まないため、split('__') で曖昧さなく元の 3 要素に復元できる。
    """
    from shared.utils import cache_key_for

    article_id = "abc123def456789012"
    difficulty = "ielts_7"
    language = "ja-en"
    key = cache_key_for(article_id, difficulty, language)

    parts = key.split("__")
    assert parts[0] == article_id
    assert parts[1] == difficulty
    assert parts[2] == language


def test_cache_key_all_valid_difficulties():
    """DifficultyLevel の全バリアントでキーを生成できること。"""
    from shared.utils import cache_key_for

    article_id = "abc123def456789012"
    for difficulty in ["toeic_600", "toeic_900", "ielts_55", "ielts_7", "eiken_2", "eiken_p1"]:
        key = cache_key_for(article_id, difficulty, "ja-en")
        assert key.startswith(article_id)
        assert difficulty in key
