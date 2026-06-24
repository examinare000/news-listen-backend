"""バッチジョブの環境変数バリデーションのテスト。

USER_ID が未設定の場合はサイレントに "default" で動作せず、
クライアント初期化の前に即座に KeyError で失敗することを確認する。
データ混在バグを防ぐための防御的プログラミング。
"""
import pytest
from unittest.mock import patch


def _env_for_batch_without_user_id(**extras) -> dict:
    """USER_ID を除いた最小限の環境変数セットを返す。
    extras で追加の変数を上書きできる。
    """
    base = {"GEMINI_API_KEY": "test-key", "GCS_BUCKET_NAME": "test-bucket"}
    base.update(extras)
    return base


def test_rss_fetcher_main_raises_key_error_when_user_id_missing():
    """rss_fetcher/main.py の main() は USER_ID 未設定時に KeyError を送出すること。
    クライアント初期化よりも先にチェックされること。
    """
    env = _env_for_batch_without_user_id()
    with patch.dict("os.environ", env, clear=True), \
         patch("shared.firestore_client.firestore.Client"):
        import importlib
        import jobs.rss_fetcher.main as m
        importlib.reload(m)
        with pytest.raises(KeyError, match="USER_ID"):
            m.main()


def test_recommendation_main_raises_key_error_when_user_id_missing():
    """recommendation/main.py の main() は USER_ID 未設定時に KeyError を送出すること。"""
    env = _env_for_batch_without_user_id()
    with patch.dict("os.environ", env, clear=True), \
         patch("shared.firestore_client.firestore.Client"), \
         patch("shared.gemini_client.genai.Client"):
        import importlib
        import jobs.recommendation.main as m
        importlib.reload(m)
        with pytest.raises(KeyError, match="USER_ID"):
            m.main()


def test_podcast_generator_main_raises_key_error_when_user_id_missing():
    """podcast_generator/main.py の main() は USER_ID 未設定時に KeyError を送出すること。"""
    env = _env_for_batch_without_user_id()
    with patch.dict("os.environ", env, clear=True), \
         patch("shared.firestore_client.firestore.Client"), \
         patch("shared.gemini_client.genai.Client"), \
         patch("shared.storage_client.storage.Client"):
        import importlib
        import jobs.podcast_generator.main as m
        importlib.reload(m)
        with pytest.raises(KeyError, match="USER_ID"):
            m.main()


def test_podcast_generator_main_raises_system_exit_for_invalid_difficulty():
    """DIFFICULTY 環境変数に無効な値が設定された場合は SystemExit を送出すること。

    無効な difficulty 値は Podcast(difficulty=...) の ValidationError として後段で発生し、
    except Exception ブロックで吸収されるため全件生成失敗しても正常終了扱いになる。
    起動時バリデーションで早期失敗させて設定ミスを即座に検出する。
    """
    env = {
        "USER_ID": "user1",
        "GEMINI_API_KEY": "test-key",
        "GCS_BUCKET_NAME": "test-bucket",
        "DIFFICULTY": "invalid_level",
    }
    # SystemExit はクライアント初期化前に送出されるのでパッチ不要
    with patch.dict("os.environ", env, clear=True):
        import importlib
        import jobs.podcast_generator.main as m
        importlib.reload(m)
        with pytest.raises(SystemExit):
            m.main()
