"""テスト共有フィクスチャ。"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_firestore_db():
    """Firestore クライアントのモック（バッチジョブのテスト用）。"""
    with patch("shared.firestore_client.firestore.Client") as mock_client_class:
        mock_db = MagicMock()
        mock_client_class.return_value = mock_db
        yield mock_db


@pytest.fixture
def mock_db():
    """API ルーターテスト用の FirestoreClient モック。
    api_client フィクスチャと組み合わせて dependency_overrides で注入される。
    """
    return MagicMock()


@pytest.fixture
def mock_storage():
    """API ルーターテスト用の StorageClient モック。

    generate_audio_url() はデフォルトで https:// URL を返す。
    podcasts エンドポイントは GCS blob path を署名付き URL に変換する責務があるため、
    テストでは変換が行われたことを検証できる。
    """
    mock = MagicMock()
    mock.generate_audio_url.return_value = (
        "https://storage.googleapis.com/test-bucket/test-signed-url"
    )
    return mock


@pytest.fixture
def mock_job_trigger():
    """API ルーターテスト用の JobTrigger モック。

    star/dismiss が BackgroundTasks 経由でジョブを起動することを検証する。
    trigger() はデフォルトで True を返す。
    """
    mock = MagicMock()
    mock.trigger.return_value = True
    return mock


@pytest.fixture
def mock_audit():
    """API ルーターテスト用の AuditLogger モック。

    record() を呼び出して監査ログが記録されたことを検証する。
    ベストエフォート設計なので、失敗時の動作をテストする際は
    mock_audit.record.side_effect で例外を設定できる。
    """
    mock = MagicMock()
    return mock


@pytest.fixture
def podcast_generator_mocks():
    """podcast_generator/main.py テスト用の全外部依存モック。

    get_user_podcast_for_article はデフォルトで None を返す。
    promote_user_podcast は MagicMock（デフォルト no-op）。
    既存テストの後方互換性を保ちながら、promote フローの新テストを実行する。

    注: このフィクスチャは tests/test_podcast_generator_main.py の
    mocks フィクスチャと重複するため、別ファイルで promote 機能を
    テストするときにのみ使用する。既存テストは現行の mocks を継続利用。
    """
    from unittest.mock import patch, MagicMock

    with patch.dict("os.environ", {
        "USER_ID": "user1",
        "GCS_BUCKET_NAME": "test-bucket",
        "GEMINI_API_KEY": "test-key",
        "DIFFICULTY": "toeic_900",
    }), \
         patch("jobs.podcast_generator.main.FirestoreClient") as MockDb:

        db = MockDb.return_value
        # WHY: get_user_podcast_for_article をデフォルト None にすることで、
        # 既存テストが promote 機能なしで従来どおり save_podcast へフォールバックする。
        db.get_user_podcast_for_article.return_value = None
        # WHY: promote_user_podcast は MagicMock（デフォルト no-op）。
        # promote フローをテストするとき、各テストで return_value を上書きする。
        db.promote_user_podcast = MagicMock()

        yield db


@pytest.fixture
def api_client(mock_db, mock_storage, mock_job_trigger, mock_audit):
    """API_KEY と USER_ID を設定した TestClient。

    FirestoreClient / StorageClient / JobTrigger / AuditLogger は dependency_overrides 経由で
    モックに差し替えられる。テスト内で mock_db / mock_storage のメソッドを設定してから
    api_client を呼ぶこと。

    例:
        def test_foo(api_client, mock_db, mock_storage, mock_audit):
            mock_db.get_podcast.return_value = pod
            mock_storage.generate_audio_url.return_value = "https://..."
            response = api_client.get("/podcasts/abc", ...)
            # 監査ログが記録されたことを確認
            mock_audit.record.assert_called()

    LOGIN_RATELIMIT_MAX_ATTEMPTS はデフォルト 0（無効化）。
    テスト側で明示的に有効化する場合は patch.dict で環境変数をセットして
    モジュールをリロードすること。
    """
    with patch.dict(
        "os.environ",
        {
            "API_KEY": "test-key",
            "USER_ID": "user1",
            "LOGIN_RATELIMIT_MAX_ATTEMPTS": "0",
            "API_RATELIMIT_MAX_REQUESTS": "0",
            "STAR_RATELIMIT_MAX_REQUESTS": "0",
        },
    ):
        import importlib

        import api.main as m
        from api.dependencies import (
            get_firestore_client,
            get_job_trigger,
            get_storage_client,
            get_user_id,
            get_audit_logger,
        )
        importlib.reload(m)
        # lru_cache をバイパスして各テストに独立したモックを注入する
        m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
        m.app.dependency_overrides[get_storage_client] = lambda: mock_storage
        m.app.dependency_overrides[get_job_trigger] = lambda: mock_job_trigger
        m.app.dependency_overrides[get_audit_logger] = lambda: mock_audit
        # get_user_id はセッション由来へ変更されたため、既存ルーターテストでは固定 user_id を注入する。
        # 認証フロー自体（get_current_user／get_session）を検証するテストは mock_db.get_session を
        # 設定し、本オーバーライドに依存しない auth/admin エンドポイントを直接叩く。
        m.app.dependency_overrides[get_user_id] = lambda: "user1"
        yield TestClient(m.app)
        m.app.dependency_overrides.clear()
