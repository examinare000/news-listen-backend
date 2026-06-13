"""テスト共有フィクスチャ。"""
import pytest
from unittest.mock import MagicMock, patch
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
def api_client(mock_db, mock_storage, mock_job_trigger):
    """API_KEY と USER_ID を設定した TestClient。

    FirestoreClient / StorageClient / JobTrigger は dependency_overrides 経由で
    モックに差し替えられる。テスト内で mock_db / mock_storage のメソッドを設定してから
    api_client を呼ぶこと。

    例:
        def test_foo(api_client, mock_db, mock_storage):
            mock_db.get_podcast.return_value = pod
            mock_storage.generate_audio_url.return_value = "https://..."
            response = api_client.get("/podcasts/abc", ...)
    """
    with patch.dict("os.environ", {"API_KEY": "test-key", "USER_ID": "user1"}):
        import importlib
        import api.main as m
        from api.dependencies import (
            get_firestore_client,
            get_job_trigger,
            get_storage_client,
        )
        importlib.reload(m)
        # lru_cache をバイパスして各テストに独立したモックを注入する
        m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
        m.app.dependency_overrides[get_storage_client] = lambda: mock_storage
        m.app.dependency_overrides[get_job_trigger] = lambda: mock_job_trigger
        yield TestClient(m.app)
        m.app.dependency_overrides.clear()
