"""GET /articles/search のテスト。"""
from datetime import datetime, timezone

from api.routers.articles import _search_articles
from shared.models import Article, UserPrefs

_NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _make_article(article_id: str, title: str = "default", content: str = "default") -> Article:
    """テスト用 Article を生成するヘルパー。"""
    return Article(
        id=article_id,
        title=title,
        url=f"https://example.com/{article_id}",
        source="example",
        content=content,
        published_at=_NOW,
        fetched_at=_NOW,
    )


# ── T1: _search_articles 純粋関数のユニットテスト ────────────────────────


class TestSearchArticlesPureFunction:
    """_search_articles 純粋関数の振る舞いをテストする。"""

    def test_search_articles_title_partial_match(self):
        """q が title に部分一致した記事を返す。"""
        articles = [
            _make_article("a1", title="Python 入門ガイド"),
            _make_article("a2", title="Java チュートリアル"),
            _make_article("a3", title="Go 並行処理"),
        ]
        query = "Python"

        result = _search_articles(articles, query, set(), set())

        assert len(result) == 1
        assert result[0].id == "a1"

    def test_search_articles_content_partial_match(self):
        """q が content に部分一致した記事を返す（title は未マッチ）。"""
        articles = [
            _make_article("a1", title="タイトル1", content="Python の基礎は重要です"),
            _make_article("a2", title="タイトル2", content="Java について学ぶ"),
        ]
        query = "Python"

        result = _search_articles(articles, query, set(), set())

        assert len(result) == 1
        assert result[0].id == "a1"

    def test_search_articles_case_insensitive(self):
        """大文字小文字を区別しない。"""
        articles = [
            _make_article("a1", title="Python Programming"),
            _make_article("a2", title="Java Tutorial"),
        ]
        query = "python"

        result = _search_articles(articles, query, set(), set())

        assert len(result) == 1
        assert result[0].id == "a1"

    def test_search_articles_japanese_substring(self):
        """日本語の部分一致（Python の `in` で UTF-8 部分一致）。"""
        articles = [
            _make_article("a1", title="機械学習とディープラーニング"),
            _make_article("a2", title="自然言語処理の基礎"),
        ]
        query = "ディープラーニング"

        result = _search_articles(articles, query, set(), set())

        assert len(result) == 1
        assert result[0].id == "a1"

    def test_search_articles_excludes_dismissed(self):
        """dismissed_ids に含まれる記事を除外する。"""
        articles = [
            _make_article("a1", title="Python 入門"),
            _make_article("a2", title="Python 応用"),
        ]
        query = "Python"
        dismissed_ids = {"a1"}

        result = _search_articles(articles, query, set(), dismissed_ids)

        assert len(result) == 1
        assert result[0].id == "a2"

    def test_search_articles_filter_unread_excludes_read_and_dismissed(self):
        """filter='unread' のとき、read_ids と dismissed_ids の両方を除外する。"""
        articles = [
            _make_article("a1", title="Python 入門"),
            _make_article("a2", title="Python 応用"),
            _make_article("a3", title="Python 上級"),
        ]
        query = "Python"
        read_ids = {"a1"}
        dismissed_ids = {"a2"}

        result = _search_articles(articles, query, read_ids, dismissed_ids, filter="unread")

        assert len(result) == 1
        assert result[0].id == "a3"

    def test_search_articles_filter_all_excludes_dismissed_only(self):
        """filter='all'（既定）のとき、dismissed のみ除外する（read は含まれる）。"""
        articles = [
            _make_article("a1", title="Python 入門"),
            _make_article("a2", title="Python 応用"),
            _make_article("a3", title="Python 上級"),
        ]
        query = "Python"
        read_ids = {"a1"}
        dismissed_ids = {"a2"}

        result = _search_articles(articles, query, read_ids, dismissed_ids, filter="all")

        assert len(result) == 2
        assert result[0].id == "a1"  # read だが含まれる
        assert result[1].id == "a3"

    def test_search_articles_no_matches_returns_empty(self):
        """マッチ記事がない場合は空リストを返す。"""
        articles = [
            _make_article("a1", title="Java チュートリアル"),
            _make_article("a2", title="Go 並行処理"),
        ]
        query = "Python"

        result = _search_articles(articles, query, set(), set())

        assert result == []

    def test_search_articles_preserves_order(self):
        """マッチした記事の順序を入力順（published_at DESC）で保持する。"""
        articles = [
            _make_article("a1", title="記事1 Python"),
            _make_article("a2", title="記事2 Java"),
            _make_article("a3", title="記事3 Python"),
        ]
        query = "Python"

        result = _search_articles(articles, query, set(), set())

        # 順序を保持（a1, a3）
        assert [a.id for a in result] == ["a1", "a3"]

    def test_search_articles_query_whitespace_stripped(self):
        """クエリの前後の空白は strip される。"""
        articles = [
            _make_article("a1", title="Python 入門"),
        ]
        query = "  Python  "  # 前後に空白がある

        result = _search_articles(articles, query, set(), set())

        # strip により "Python" として検索される
        assert len(result) == 1
        assert result[0].id == "a1"

    def test_search_articles_whitespace_only_returns_empty(self):
        """空白のみクエリは strip 後 "" になるが、全件マッチさせず空を返す（重大バグ防止）。"""
        articles = [
            _make_article("a1", title="Python"),
            _make_article("a2", title="Java"),
        ]
        for blank in ["   ", "\t", "\n", ""]:
            assert _search_articles(articles, blank, set(), set()) == []

    def test_search_articles_all_matched_with_empty_read_dismissed(self):
        """read_ids と dismissed_ids が空の場合、マッチ記事を全て返す。"""
        articles = [
            _make_article("a1", title="Python 入門"),
            _make_article("a2", title="Python 応用"),
        ]
        query = "Python"

        result = _search_articles(articles, query, set(), set())

        assert len(result) == 2

    def test_search_articles_overlapping_read_and_dismissed_in_unread_filter(self):
        """filter='unread' で read と dismissed に重複があっても正しく除外する。"""
        articles = [
            _make_article("a1", title="記事1"),
            _make_article("a2", title="記事2"),
            _make_article("a3", title="記事3"),
        ]
        query = "記事"
        read_ids = {"a1"}
        dismissed_ids = {"a1", "a2"}  # a1 は両方に含まれる

        result = _search_articles(articles, query, read_ids, dismissed_ids, filter="unread")

        assert len(result) == 1
        assert result[0].id == "a3"


# ── T2: スキーマ ArticleSearchResponse の契約テスト ────────────────────────


class TestArticleSearchResponse:
    """ArticleSearchResponse スキーマの必須フィールド・型を検証する。"""

    def test_article_search_response_has_articles_and_total_count(self):
        """ArticleSearchResponse は articles と total_count を必須フィールドとして持つ。"""
        from api.schemas import ArticleSearchResponse

        response = ArticleSearchResponse(articles=[], total_count=0)

        assert hasattr(response, "articles")
        assert hasattr(response, "total_count")
        assert isinstance(response.articles, list)
        assert isinstance(response.total_count, int)

    def test_article_search_response_articles_type(self):
        """ArticleSearchResponse.articles は list[ArticleResponse] 型。"""
        from api.schemas import ArticleSearchResponse, ArticleResponse

        article = ArticleResponse(
            id="a1",
            title="記事1",
            url="https://example.com/a1",
            source="example",
            score=0.5,
            published_at="2026-06-21T00:00:00+00:00",
            is_read=False,
        )
        response = ArticleSearchResponse(articles=[article], total_count=1)

        assert len(response.articles) == 1
        assert response.articles[0].id == "a1"

    def test_article_search_response_total_count_type(self):
        """ArticleSearchResponse.total_count は int 型で、ページング前の全件数を表す。"""
        from api.schemas import ArticleSearchResponse

        response = ArticleSearchResponse(articles=[], total_count=150)

        assert response.total_count == 150
        assert isinstance(response.total_count, int)


# ── T3: エンドポイント GET /articles/search の統合テスト ────────────────────────
#
# search_articles は Depends(get_user_id) を使う。base api_client fixture が
# get_user_id を "user1" に override するため、feed テストと同じく api_client + mock_db で叩ける。


def _make_prefs(dismissed=None, read=None) -> UserPrefs:
    return UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        dismissed_article_ids=dismissed or [],
        read_article_ids=read or [],
    )


def test_search_requires_api_key(api_client):
    """X-API-Key 無しは 401。"""
    response = api_client.get("/articles/search?q=python")
    assert response.status_code == 401


def test_search_empty_query_returns_422(api_client, mock_db):
    """q 未指定（min_length 違反）は 422。"""
    response = api_client.get("/articles/search?q=", headers={"X-API-Key": "test-key"})
    assert response.status_code == 422


def test_search_returns_matched_articles(api_client, mock_db):
    """q に一致した記事を ArticleResponse[] と total_count で返す。"""
    mock_db.get_user_prefs.return_value = _make_prefs()
    mock_db.get_recommendation.return_value = None
    mock_db.get_recent_articles.return_value = [
        _make_article("a1", title="Python 入門"),
        _make_article("a2", title="Java 入門"),
        _make_article("a3", title="Python 応用", content="python is great"),
    ]

    response = api_client.get("/articles/search?q=python", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    data = response.json()
    ids = [a["id"] for a in data["articles"]]
    assert set(ids) == {"a1", "a3"}  # title または content に python を含む
    assert data["total_count"] == 2


def test_search_whitespace_only_query_returns_empty(api_client, mock_db):
    """空白のみクエリ（min_length は通過）は全件返さず空を返す。"""
    mock_db.get_user_prefs.return_value = _make_prefs()
    mock_db.get_recommendation.return_value = None
    mock_db.get_recent_articles.return_value = [
        _make_article("a1", title="Python"),
        _make_article("a2", title="Java"),
    ]

    response = api_client.get("/articles/search?q=%20%20%20", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    data = response.json()
    assert data["articles"] == []
    assert data["total_count"] == 0


def test_search_excludes_dismissed(api_client, mock_db):
    """dismissed 記事は検索結果から除外される。"""
    mock_db.get_user_prefs.return_value = _make_prefs(dismissed=["a1"])
    mock_db.get_recommendation.return_value = None
    mock_db.get_recent_articles.return_value = [
        _make_article("a1", title="Python A"),
        _make_article("a2", title="Python B"),
    ]

    response = api_client.get("/articles/search?q=python", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    data = response.json()
    assert [a["id"] for a in data["articles"]] == ["a2"]
    assert data["total_count"] == 1


def test_search_pagination_total_count_is_pre_paging(api_client, mock_db):
    """total_count はページング前の全件数、articles は offset/limit でスライスされる。"""
    mock_db.get_user_prefs.return_value = _make_prefs()
    mock_db.get_recommendation.return_value = None
    mock_db.get_recent_articles.return_value = [
        _make_article(f"a{i}", title=f"Python {i}") for i in range(5)
    ]

    response = api_client.get(
        "/articles/search?q=python&limit=2&offset=1", headers={"X-API-Key": "test-key"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 5  # ページング前の全件
    assert len(data["articles"]) == 2  # limit=2


def test_search_is_read_flag(api_client, mock_db):
    """is_read が read_ids に応じて付与される。"""
    mock_db.get_user_prefs.return_value = _make_prefs(read=["a1"])
    mock_db.get_recommendation.return_value = None
    mock_db.get_recent_articles.return_value = [
        _make_article("a1", title="Python A"),
        _make_article("a2", title="Python B"),
    ]

    response = api_client.get("/articles/search?q=python", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    by_id = {a["id"]: a for a in response.json()["articles"]}
    assert by_id["a1"]["is_read"] is True
    assert by_id["a2"]["is_read"] is False


