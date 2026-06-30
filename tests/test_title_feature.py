"""title フィールド新設 + 冒頭挨拶修正 の Red→Green テスト。

変更1: _SCRIPT_PROMPT / _DIGEST_SCRIPT_PROMPT の JAPANESE_INTRO 指示文修正。
変更2: PodcastScript.title フィールド、_parse_script の 3 セクション対応、
        Podcast.title / PodcastCache.title、firestore_client、api/schemas への配線。
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)


# ============================================================
# 変更1: プロンプト文言（冒頭挨拶）
# ============================================================


def test_script_prompt_contains_greeting_instruction():
    """_SCRIPT_PROMPT の JAPANESE_INTRO 指示に「こんにちは。」で始まる旨が含まれること。"""
    from jobs.podcast_generator.script_generator import _SCRIPT_PROMPT

    assert "こんにちは" in _SCRIPT_PROMPT


def test_script_prompt_contains_no_weekly_news_persona():
    """_SCRIPT_PROMPT が「週刊ニュース番組」ペルソナを明示的に否定していること。"""
    from jobs.podcast_generator.script_generator import _SCRIPT_PROMPT

    assert "週刊ニュース番組のような番組設定・演出は不要" in _SCRIPT_PROMPT


def test_digest_script_prompt_contains_greeting_instruction():
    """_DIGEST_SCRIPT_PROMPT の JAPANESE_INTRO 指示に「こんにちは。」で始まる旨が含まれること。"""
    from jobs.podcast_generator.script_generator import _DIGEST_SCRIPT_PROMPT

    assert "こんにちは" in _DIGEST_SCRIPT_PROMPT


def test_digest_script_prompt_contains_no_weekly_news_persona():
    """_DIGEST_SCRIPT_PROMPT が「週刊ニュース番組」ペルソナを明示的に否定していること。"""
    from jobs.podcast_generator.script_generator import _DIGEST_SCRIPT_PROMPT

    assert "週刊ニュース番組のような番組設定・演出は不要" in _DIGEST_SCRIPT_PROMPT


def test_script_prompt_date_str_placeholder_preserved():
    """{date_str} プレースホルダが _SCRIPT_PROMPT に残っていること。"""
    from jobs.podcast_generator.script_generator import _SCRIPT_PROMPT

    assert "{date_str}" in _SCRIPT_PROMPT


def test_digest_script_prompt_date_str_placeholder_preserved():
    """{date_str} プレースホルダが _DIGEST_SCRIPT_PROMPT に残っていること。"""
    from jobs.podcast_generator.script_generator import _DIGEST_SCRIPT_PROMPT

    assert "{date_str}" in _DIGEST_SCRIPT_PROMPT


# ============================================================
# 変更2-A: PodcastScript.title フィールド
# ============================================================


def test_podcast_script_has_title_field():
    """PodcastScript が title フィールドを持つこと。"""
    from jobs.podcast_generator.script_generator import PodcastScript

    script = PodcastScript(
        title="タイトルテスト",
        japanese_intro="イントロ",
        english_body="Body",
    )
    assert script.title == "タイトルテスト"


# ============================================================
# 変更2-B: _parse_script の 3 セクション対応
# ============================================================


def test_parse_script_extracts_title_when_present():
    """===TITLE=== がある場合、title を正しく抽出すること。"""
    from jobs.podcast_generator.script_generator import ScriptGenerator

    gen = ScriptGenerator(gemini_client=MagicMock())
    raw = (
        "===TITLE===\n"
        "AIが変える医療現場\n"
        "===JAPANESE_INTRO===\n"
        "こんにちは。2026年6月30日の出来事です。\n"
        "===ENGLISH_BODY===\n"
        "Today we talk about AI in healthcare."
    )
    script = gen._parse_script(raw)

    assert script.title == "AIが変える医療現場"
    assert "こんにちは" in script.japanese_intro
    assert "AI in healthcare" in script.english_body


def test_parse_script_returns_empty_title_when_title_marker_absent():
    """===TITLE=== がない場合、title="" を返すこと（後方互換フォールバック）。"""
    from jobs.podcast_generator.script_generator import ScriptGenerator

    gen = ScriptGenerator(gemini_client=MagicMock())
    raw = (
        "===JAPANESE_INTRO===\n"
        "イントロ\n"
        "===ENGLISH_BODY===\n"
        "Body text here."
    )
    script = gen._parse_script(raw)

    assert script.title == ""
    assert script.japanese_intro == "イントロ"
    assert script.english_body == "Body text here."


def test_parse_script_fallback_when_no_markers():
    """マーカーが全く無い場合、title="" かつ body にそのまま入れること。"""
    from jobs.podcast_generator.script_generator import ScriptGenerator

    gen = ScriptGenerator(gemini_client=MagicMock())
    raw = "This is some raw output without markers."
    script = gen._parse_script(raw)

    assert script.title == ""
    assert script.english_body == raw
    assert script.japanese_intro == ""


def test_parse_script_strips_whitespace_from_title():
    """title の前後の空白が strip されること。"""
    from jobs.podcast_generator.script_generator import ScriptGenerator

    gen = ScriptGenerator(gemini_client=MagicMock())
    raw = (
        "===TITLE===\n"
        "  タイトルに空白  \n"
        "===JAPANESE_INTRO===\n"
        "イントロ\n"
        "===ENGLISH_BODY===\n"
        "Body"
    )
    script = gen._parse_script(raw)
    assert script.title == "タイトルに空白"


# ============================================================
# 回帰テスト: _parse_script のグレースフル縮退
# ============================================================


def test_parse_script_graceful_degradation_when_title_marker_in_body():
    """シナリオ A: 本文中に ===TITLE=== が含まれる出力（TITLE セクションは省略）は
    ValueError を出さずグレースフルに縮退すること。

    Gemini が「The format uses ===TITLE=== to label sections.」のような本文を返した場合、
    raw 中に 3 マーカーが揃って見えるが ===TITLE=== は本文混入であり、
    ===JAPANESE_INTRO=== より後に出現する。title="" で縮退し body に内容が入る。
    """
    from jobs.podcast_generator.script_generator import ScriptGenerator

    gen = ScriptGenerator(gemini_client=MagicMock())
    raw = (
        "===JAPANESE_INTRO===\n"
        "イントロテキスト。\n"
        "===ENGLISH_BODY===\n"
        "The format uses ===TITLE=== to label sections."
    )
    # ValueError を raise しないこと
    script = gen._parse_script(raw)

    assert script.title == ""
    assert script.english_body != ""  # body に内容が入ること
    assert "===TITLE===" in script.english_body  # 本文がそのまま body に入ること


def test_parse_script_graceful_degradation_when_markers_out_of_order():
    """シナリオ B: ===ENGLISH_BODY=== が ===JAPANESE_INTRO=== より前に来る順序違反は
    ValueError を出さずグレースフルに縮退すること。

    両マーカーが存在しても順序が逆なら構造不正とみなし、raw 全体を body に割り当てる。
    """
    from jobs.podcast_generator.script_generator import ScriptGenerator

    gen = ScriptGenerator(gemini_client=MagicMock())
    raw = (
        "===TITLE===\n"
        "タイトル\n"
        "===ENGLISH_BODY===\n"
        "Body text\n"
        "===JAPANESE_INTRO===\n"
        "イントロ"
    )
    # ValueError を raise しないこと
    script = gen._parse_script(raw)

    assert script.title == ""
    assert script.english_body == raw  # body に raw 全体が入ること
    assert script.japanese_intro == ""


# ============================================================
# 変更2-C: generate / generate_digest がタイトルを返すこと
# ============================================================


def _make_article():
    from shared.models import Article

    return Article(
        id="a1",
        title="Test",
        url="https://example.com/a1",
        source="hn",
        content="Test content.",
        published_at=NOW,
        fetched_at=NOW,
    )


def test_generate_returns_script_with_title():
    """generate() がモック Gemini 出力からタイトルを含む PodcastScript を返すこと。"""
    mock_gemini = MagicMock()
    mock_gemini.generate_text.return_value = (
        "===TITLE===\n"
        "Rustが注目される理由\n"
        "===JAPANESE_INTRO===\n"
        "こんにちは。2026-06-30です。今回取り上げるニュースはRustについてです。\n"
        "===ENGLISH_BODY===\n"
        "Rust is a systems programming language."
    )

    from jobs.podcast_generator.script_generator import ScriptGenerator

    gen = ScriptGenerator(gemini_client=mock_gemini)
    script = gen.generate(
        main_article=_make_article(),
        related_articles=[],
        difficulty="toeic_900",
        date_str="2026-06-30",
    )

    assert script.title == "Rustが注目される理由"
    assert script.japanese_intro != ""
    assert script.english_body != ""


def test_generate_digest_returns_script_with_title():
    """generate_digest() がモック Gemini 出力からタイトルを含む PodcastScript を返すこと。"""
    mock_gemini = MagicMock()
    mock_gemini.generate_text.return_value = (
        "===TITLE===\n"
        "本日のダイジェスト\n"
        "===JAPANESE_INTRO===\n"
        "こんにちは。2026-06-30のダイジェストです。\n"
        "===ENGLISH_BODY===\n"
        "Today's digest covers several topics."
    )

    from jobs.podcast_generator.script_generator import ScriptGenerator

    gen = ScriptGenerator(gemini_client=mock_gemini)
    script = gen.generate_digest(
        articles=[_make_article()],
        difficulty="toeic_900",
        date_str="2026-06-30",
    )

    assert script.title == "本日のダイジェスト"
    assert script.japanese_intro != ""


# ============================================================
# 変更2-D: Podcast.title フィールド
# ============================================================


def test_podcast_model_has_title_field():
    """Podcast が title フィールドを持ち、デフォルト "" であること。"""
    from shared.models import Podcast

    pod = Podcast(
        id="p1",
        type="single",
        article_ids=["a1"],
        difficulty="toeic_900",
        audio_url="url",
        japanese_intro_text="イントロ",
        duration_seconds=100,
        status="completed",
        created_at=NOW,
        user_id="u1",
    )
    # default = "" で後方互換
    assert pod.title == ""


def test_podcast_model_accepts_title():
    """Podcast に title を渡したとき、正しく格納されること。"""
    from shared.models import Podcast

    pod = Podcast(
        id="p1",
        type="single",
        article_ids=["a1"],
        difficulty="toeic_900",
        audio_url="url",
        japanese_intro_text="イントロ",
        duration_seconds=100,
        status="completed",
        created_at=NOW,
        user_id="u1",
        title="テストタイトル",
    )
    assert pod.title == "テストタイトル"


# ============================================================
# 変更2-D: PodcastCache.title フィールド
# ============================================================


def test_podcast_cache_model_has_title_field():
    """PodcastCache が title フィールドを持ち、デフォルト None であること。"""
    from shared.models import PodcastCache

    cache = PodcastCache(
        cache_key="ck",
        article_id="a1",
        difficulty="toeic_900",
        language="ja-en",
        status="processing",
        created_at=NOW,
    )
    assert cache.title is None


def test_podcast_cache_accepts_title():
    """PodcastCache に title を渡したとき、正しく格納されること。"""
    from shared.models import PodcastCache

    cache = PodcastCache(
        cache_key="ck",
        article_id="a1",
        difficulty="toeic_900",
        language="ja-en",
        status="completed",
        audio_url="url",
        japanese_intro_text="イントロ",
        duration_seconds=100,
        created_at=NOW,
        title="キャッシュタイトル",
    )
    assert cache.title == "キャッシュタイトル"


# ============================================================
# 変更2-E: PodcastResponse.from_podcast で title をマッピング
# ============================================================


def test_podcast_response_from_podcast_maps_title():
    """PodcastResponse.from_podcast() が podcast.title を title フィールドにマッピングすること。"""
    from shared.models import Podcast
    from api.schemas import PodcastResponse

    pod = Podcast(
        id="p1",
        type="single",
        article_ids=["a1"],
        difficulty="toeic_900",
        audio_url="url",
        japanese_intro_text="イントロ",
        duration_seconds=100,
        status="completed",
        created_at=NOW,
        user_id="u1",
        title="マッピングタイトル",
    )
    resp = PodcastResponse.from_podcast(pod)
    assert resp.title == "マッピングタイトル"


def test_podcast_response_from_podcast_maps_empty_title():
    """title が空文字のとき、from_podcast() でも空文字が返ること（後方互換）。"""
    from shared.models import Podcast
    from api.schemas import PodcastResponse

    pod = Podcast(
        id="p1",
        type="single",
        article_ids=["a1"],
        difficulty="toeic_900",
        audio_url="url",
        japanese_intro_text="イントロ",
        duration_seconds=100,
        status="completed",
        created_at=NOW,
        user_id="u1",
    )
    resp = PodcastResponse.from_podcast(pod)
    assert resp.title == ""


# ============================================================
# 変更2-F: firestore_client — promote_user_podcast で title が書き込まれること
# ============================================================


def test_promote_user_podcast_includes_title_in_update(mock_firestore_db):
    """promote_user_podcast() が title を update ペイロードに含めること。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()

    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": "processing"}
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.document.return_value = ref

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        client.promote_user_podcast(
            podcast_id="pod1",
            status="completed",
            audio_url="url",
            japanese_intro_text="イントロ",
            duration_seconds=120,
            title="テストタイトル",
        )

    transaction = mock_firestore_db.transaction.return_value
    transaction.update.assert_called_once()
    update_data = transaction.update.call_args[0][1]
    assert "title" in update_data
    assert update_data["title"] == "テストタイトル"


def test_promote_user_podcast_title_default_empty_string(mock_firestore_db):
    """title 未指定時のデフォルトは "" であること。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()

    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": "processing"}
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.document.return_value = ref

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        client.promote_user_podcast(
            podcast_id="pod1",
            status="completed",
            audio_url="url",
            japanese_intro_text="イントロ",
            duration_seconds=120,
        )

    transaction = mock_firestore_db.transaction.return_value
    update_data = transaction.update.call_args[0][1]
    assert update_data["title"] == ""


def test_try_acquire_user_podcast_includes_title_default_in_processing_row(mock_firestore_db):
    """try_acquire_user_podcast が processing 行に title="" を書き込むこと。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()

    snapshot = MagicMock()
    snapshot.exists = False
    ref = MagicMock()
    ref.get.return_value = snapshot
    mock_firestore_db.collection.return_value.document.return_value = ref
    mock_firestore_db.transaction.return_value = MagicMock()

    with patch("shared.firestore_client.firestore.transactional", lambda f: f):
        client.try_acquire_user_podcast("u1", "a1", "toeic_900", "ja-en")

    transaction = mock_firestore_db.transaction.return_value
    transaction.set.assert_called_once()
    set_data = transaction.set.call_args[0][1]
    assert "title" in set_data
    assert set_data["title"] == ""


# ============================================================
# 変更2-G: firestore_client — save_podcast_cache で title が書き込まれること
# ============================================================


def test_save_podcast_cache_includes_title_in_payload(mock_firestore_db):
    """save_podcast_cache() が title を Firestore ペイロードに含めること。"""
    from shared.firestore_client import FirestoreClient
    from shared.models import PodcastCache

    client = FirestoreClient()
    cache = PodcastCache(
        cache_key="ck",
        article_id="a1",
        difficulty="toeic_900",
        language="ja-en",
        status="completed",
        audio_url="url",
        japanese_intro_text="イントロ",
        duration_seconds=100,
        created_at=NOW,
        title="キャッシュタイトル",
    )
    mock_doc_ref = MagicMock()
    mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

    client.save_podcast_cache(cache)

    payload = mock_doc_ref.set.call_args[0][0]
    assert "title" in payload
    assert payload["title"] == "キャッシュタイトル"


def test_get_podcast_cache_restores_title(mock_firestore_db):
    """get_podcast_cache() が Firestore doc の title を PodcastCache.title に復元すること。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = "ck"
    mock_doc.to_dict.return_value = {
        "article_id": "a1",
        "difficulty": "toeic_900",
        "language": "ja-en",
        "status": "completed",
        "audio_url": "url",
        "japanese_intro_text": "イントロ",
        "duration_seconds": 100,
        "created_at": "2026-06-30T12:00:00+00:00",
        "title": "取得タイトル",
    }
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    result = client.get_podcast_cache("ck")

    assert result.title == "取得タイトル"


def test_get_podcast_cache_title_defaults_to_none_when_absent(mock_firestore_db):
    """既存 Firestore doc に title フィールドが無い場合、title=None となること（後方互換）。"""
    from shared.firestore_client import FirestoreClient

    client = FirestoreClient()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = "ck"
    mock_doc.to_dict.return_value = {
        "article_id": "a1",
        "difficulty": "toeic_900",
        "language": "ja-en",
        "status": "completed",
        "audio_url": "url",
        "japanese_intro_text": "イントロ",
        "duration_seconds": 100,
        "created_at": "2026-06-30T12:00:00+00:00",
        # title フィールド意図的に欠落
    }
    mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

    result = client.get_podcast_cache("ck")

    assert result.title is None
