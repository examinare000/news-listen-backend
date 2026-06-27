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
def mock_email_sender():
    """API ルーターテスト用の EmailSender モック。

    send_password_reset_email() を呼び出してメール送信が試みられたことを検証する。
    """
    mock = MagicMock()
    return mock


@pytest.fixture
def current_session():
    """API テスト用の固定 Session（get_current_user override で使用）。

    articles/settings エンドポイントで get_current_user を使用するため、
    その override 用に Session オブジェクトを提供する。
    """
    from datetime import datetime, timezone
    from shared.models import Session

    return Session(
        session_id="test-session-123",
        user_id="user1",
        username="testuser",
        role="user",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def api_client_with_auth(api_client, current_session):
    """get_current_user を override した api_client（articles/settings テスト用）。

    api_client の dependency_overrides に get_current_user override を追加し、
    認証が必要なエンドポイントでも 401 を返さないようにする。
    """
    from api.dependencies import get_current_user
    import api.main as m

    # dependency_overrides に get_current_user を追加
    m.app.dependency_overrides[get_current_user] = lambda: current_session
    yield api_client
    # クリーンアップは api_client フィクスチャ内で行われるため、ここでは不要


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


class _TestClientWithDefaultHeaders(TestClient):
    """X-API-Key をデフォルトで付与する TestClient。"""

    def request(self, *args, **kwargs):
        """リクエストメソッドをオーバーライド。headers に API-Key を追加。"""
        headers = kwargs.get("headers", {})
        if isinstance(headers, dict):
            # 既に X-API-Key がなければ追加
            if "X-API-Key" not in headers:
                headers["X-API-Key"] = "test-key"
            kwargs["headers"] = headers
        return super().request(*args, **kwargs)


@pytest.fixture
def api_client(mock_db, mock_storage, mock_job_trigger, mock_audit, mock_email_sender):
    """API_KEY と USER_ID を設定した TestClient。

    FirestoreClient / StorageClient / JobTrigger / AuditLogger / EmailSender は
    dependency_overrides 経由でモックに差し替えられる。テスト内で mock_db / mock_storage
    のメソッドを設定してから api_client を呼ぶこと。

    例:
        def test_foo(api_client, mock_db, mock_storage, mock_audit):
            mock_db.get_podcast.return_value = pod
            mock_storage.generate_audio_url.return_value = "https://..."
            response = api_client.get("/podcasts/abc", ...)
            # 監査ログが記録されたことを確認
            mock_audit.record.assert_called()

    X-API-Key はデフォルトで "test-key" が自動付与される（ヘッダー override で置き換え可）。
    LOGIN_RATELIMIT_MAX_ATTEMPTS / PASSWORD_RESET_RATELIMIT_MAX_REQUESTS はデフォルト 0（無効化）。
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
            "PASSWORD_RESET_RATELIMIT_MAX_REQUESTS": "0",
            "CSRF_PROTECTION_ENABLED": "false",
            "PASSWORD_RESET_URL_BASE": "https://test.example.com/reset-password",
        },
    ):
        import importlib

        # WHY: 他テスト（test_api_auth が api.routers.auth を、test_dependencies が
        # api.dependencies を importlib.reload する）により、ルーターの Depends が参照する
        # 依存関数オブジェクトと、本 fixture が import して dependency_overrides のキーにする
        # 関数オブジェクトの同一性がズレることがある。ズレると override が外れ、ルーターが
        # 実 FirestoreClient を構築して DefaultCredentialsError になる（full-suite でのみ再現）。
        # main 再ロード前にルーター群を再ロードして、現行 api.dependencies へ再束縛し同一性を揃える。
        # その前に api.ratelimit を再ロードする。ルーターの Depends(rate_limit(...)) が生成する
        # _rate_limit_dep は api.ratelimit の module-global get_firestore_client を Depends に使うため、
        # ここを現行 api.dependencies へ揃えないと、その sub-dependency だけ override が外れて実 client
        # を構築してしまう（ルーター再ロードはこの後なので、再生成される _rate_limit_dep も正準化される）。
        import api.ratelimit
        importlib.reload(api.ratelimit)

        from api.routers import (
            admin,
            articles,
            auth,
            feed,
            notifications,
            passkey as passkey_router,
            podcasts,
            settings as settings_router,
        )
        for _router_mod in (admin, articles, auth, feed, notifications, passkey_router, podcasts, settings_router):
            importlib.reload(_router_mod)

        import api.main as m
        from api.dependencies import (
            get_firestore_client,
            get_job_trigger,
            get_storage_client,
            get_user_id,
            get_audit_logger,
            get_email_sender,
        )

        importlib.reload(m)
        # lru_cache をクリア
        get_firestore_client.cache_clear()
        get_job_trigger.cache_clear()
        get_audit_logger.cache_clear()
        get_email_sender.cache_clear()
        get_storage_client.cache_clear()

        # lru_cache をバイパスして各テストに独立したモックを注入する
        m.app.dependency_overrides[get_firestore_client] = lambda: mock_db
        m.app.dependency_overrides[get_storage_client] = lambda: mock_storage
        m.app.dependency_overrides[get_job_trigger] = lambda: mock_job_trigger
        m.app.dependency_overrides[get_audit_logger] = lambda: mock_audit
        m.app.dependency_overrides[get_email_sender] = lambda: mock_email_sender
        # get_user_id はセッション由来へ変更されたため、既存ルーターテストでは固定 user_id を注入する。
        # 認証フロー自体（get_current_user／get_session）を検証するテストは mock_db.get_session を
        # 設定し、本オーバーライドに依存しない auth/admin エンドポイントを直接叩く。
        m.app.dependency_overrides[get_user_id] = lambda: "user1"
        yield _TestClientWithDefaultHeaders(m.app)
        m.app.dependency_overrides.clear()
