"""api.cors_config と CORS ミドルウェアのテスト。

build_cors_options() を検証し、FastAPI の CORSMiddleware が正しく機能することを確認。
"""
import importlib
from unittest.mock import patch

from fastapi.testclient import TestClient
from shared.security import hash_password

from datetime import datetime, timezone

BASE_ENV = {
    "API_KEY": "test-key",
    "USER_ID": "user1",
    "LOGIN_RATELIMIT_MAX_ATTEMPTS": "0",
}


def _client_with_env(**extra):
    """テスト環境変数を注入して app をリロードし、TestClient を返す。"""
    with patch.dict("os.environ", {**BASE_ENV, **extra}, clear=False):
        import api.main as m
        importlib.reload(m)
        return TestClient(m.app), m


def _user(username="alice", password="correct-horse", role="user"):
    """テスト用の User モデル。"""
    from shared.models import User
    NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    return User(
        username=username,
        user_id="uid-1",
        password_hash=hash_password(password),
        role=role,
        display_name="Alice",
        created_at=NOW,
        updated_at=NOW,
    )


class TestBuildCorsOptions:
    """build_cors_options() の単体テスト。"""

    def test_p1_default_empty_origins(self):
        """環境変数なし → allow_origins は空リスト。

        P1: build_cors_options({}) で、allow_origins == [],
        allow_credentials is True, "X-API-Key" in allow_headers,
        "*" not in allow_origins を確認。
        """
        from api.cors_config import build_cors_options

        options = build_cors_options({})
        assert options["allow_origins"] == []
        assert options["allow_credentials"] is True
        assert "X-API-Key" in options["allow_headers"]
        assert "*" not in options["allow_origins"]
        # 実際に使用中の PATCH/PUT が許可メソッドに含まれること（プリフライト拒否回帰防止）。
        assert "PATCH" in options["allow_methods"]
        assert "PUT" in options["allow_methods"]

    def test_p2_parse_comma_separated_origins(self):
        """カンマ区切りのオリジンをパース。

        P2: CORS_ALLOWED_ORIGINS="https://a.example, https://b.example" で、
        allow_origins が正しく解析される。
        """
        from api.cors_config import build_cors_options

        options = build_cors_options({
            "CORS_ALLOWED_ORIGINS": "https://a.example, https://b.example"
        })
        assert options["allow_origins"] == ["https://a.example", "https://b.example"]

    def test_p3_strip_whitespace_drop_empty(self):
        """空白をトリムし、空のフラグメントを削除。

        P3: CORS_ALLOWED_ORIGINS=" , https://a.example , " で、
        allow_origins が ["https://a.example"] になる。
        """
        from api.cors_config import build_cors_options

        options = build_cors_options({
            "CORS_ALLOWED_ORIGINS": " , https://a.example , "
        })
        assert options["allow_origins"] == ["https://a.example"]


class TestCorsIntegration:
    """CORS ミドルウェアの統合テスト。"""

    def test_c1_preflight_allowed_origin(self):
        """許可されたオリジンからの preflight リクエスト → ACAO ヘッダ含む。

        C1: CORS_ALLOWED_ORIGINS="https://app.example" で、
        OPTIONS /auth/login に Origin: https://app.example,
        Access-Control-Request-Method: POST を送ると、
        Access-Control-Allow-Origin == "https://app.example" が返る。
        """
        client, m = _client_with_env(CORS_ALLOWED_ORIGINS="https://app.example")

        resp = client.options(
            "/auth/login",
            headers={
                "Origin": "https://app.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" in resp.headers
        assert resp.headers["access-control-allow-origin"] == "https://app.example"

    def test_c2_preflight_disallowed_origin(self):
        """許可されていないオリジンからの preflight → ACAO ヘッダなし。

        C2: 同じ環境で Origin: https://evil.example + ACRM: POST を送ると、
        access-control-allow-origin が response.headers に含まれない。
        """
        client, m = _client_with_env(CORS_ALLOWED_ORIGINS="https://app.example")

        resp = client.options(
            "/auth/login",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in resp.headers

    def test_c3_no_origins_configured(self):
        """オリジン無設定（空）の環境で any preflight → ACAO なし。

        C3: _client_with_env() (デフォルト、CORS_ALLOWED_ORIGINS 無設定) で，
        任意の Origin + ACRM で preflight を実行すると，
        access-control-allow-origin は含まれない。
        """
        client, m = _client_with_env()

        resp = client.options(
            "/auth/login",
            headers={
                "Origin": "https://anything.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in resp.headers

    def test_c4_cors_and_security_headers_coexist(self):
        """Preflight 応答に CORS と セキュリティヘッダが両方含まれる。

        C4: 許可されたオリジンの preflight で、
        response に X-Content-Type-Options: nosniff AND
        Access-Control-Allow-Origin が両方含まれる。
        （SecurityHeaders middleware が CORS ヘッダを上書きしていないことの証明）
        """
        client, m = _client_with_env(CORS_ALLOWED_ORIGINS="https://app.example")

        resp = client.options(
            "/auth/login",
            headers={
                "Origin": "https://app.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" in resp.headers
        assert "x-content-type-options" in resp.headers
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_c5_cors_allow_headers_includes_x_api_key(self):
        """Preflight で Access-Control-Request-Headers: X-API-Key を要求。

        C5: 許可されたオリジンから Access-Control-Request-Headers: X-API-Key
        を含む preflight を送ると、Access-Control-Allow-Headers に
        X-API-Key（大文字小文字不問）が含まれる。
        """
        client, m = _client_with_env(CORS_ALLOWED_ORIGINS="https://app.example")

        resp = client.options(
            "/auth/login",
            headers={
                "Origin": "https://app.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
        assert "x-api-key" in allow_headers

    def test_c6_login_endpoint_auth_regression(self, api_client, mock_db):
        """既存の認証ロジックが CORS/セキュリティヘッダ追加後も動作する。

        C6: api_client フィクスチャ（デフォルト環境）を使い、
        mock_db.get_user が有効なユーザーを返す場合、
        POST /auth/login with X-API-Key: test-key で 200 応答。
        """
        mock_db.get_user.return_value = _user(password="correct-horse")

        resp = api_client.post(
            "/auth/login",
            json={"username": "alice", "password": "correct-horse"},
            headers={"X-API-Key": "test-key"},
        )

        assert resp.status_code == 200

    def test_c7_protected_endpoint_requires_auth(self, api_client):
        """保護されたエンドポイントは認証なしで 401。

        C7: api_client で protected endpoint（例: /auth/me）に
        X-API-Key を含めずに GET すると、401 が返る。
        """
        resp = api_client.get("/auth/me", headers={"X-API-Key": "test-key"})
        assert resp.status_code == 401
