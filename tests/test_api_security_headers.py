"""api.middleware.security_headers のテスト。

build_security_headers() を検証し、SecurityHeadersMiddleware の動作を確認する。
"""
import importlib
from unittest.mock import patch

from fastapi.testclient import TestClient

BASE_ENV = {
    "API_KEY": "test-key",
    "USER_ID": "user1",
    "LOGIN_RATELIMIT_MAX_ATTEMPTS": "0",
}


def _client_with_env(**extra):
    """テスト環境変数を注入して app をリロードし、TestClient を返す。

    conftest の api_client フィクスチャとは異なり、カスタム CORS/CSP/HSTS
    環境変数が必要なテストはこのヘルパーを使う。
    """
    with patch.dict("os.environ", {**BASE_ENV, **extra}, clear=False):
        import api.main as m
        importlib.reload(m)
        return TestClient(m.app), m


class TestBuildSecurityHeaders:
    """build_security_headers() の単体テスト。"""

    def test_h1_default_headers(self):
        """デフォルト環境（env 空）で標準的なセキュリティヘッダを返す。

        H1: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy は
        デフォルト値を、HSTS は含まない（ローカル HTTP 安全側）。
        """
        from api.middleware.security_headers import build_security_headers

        headers = build_security_headers({})
        assert headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "Strict-Transport-Security" not in headers

    def test_h2_csp_override(self):
        """SECURITY_CSP 環境変数で CSP をオーバーライド。

        H2: SECURITY_CSP が指定されている場合、デフォルトではなく
        その値を使用する。
        """
        from api.middleware.security_headers import build_security_headers

        headers = build_security_headers({"SECURITY_CSP": "default-src 'self'"})
        assert headers["Content-Security-Policy"] == "default-src 'self'"

    def test_h3_hsts_enabled_with_custom_max_age(self):
        """HSTS を有効化し、カスタム max-age を指定。

        H3: SECURITY_HSTS_ENABLED=true かつ SECURITY_HSTS_MAX_AGE=60 で、
        Strict-Transport-Security ヘッダが正しい値を持つ。
        """
        from api.middleware.security_headers import build_security_headers

        headers = build_security_headers({
            "SECURITY_HSTS_ENABLED": "true",
            "SECURITY_HSTS_MAX_AGE": "60",
        })
        assert headers["Strict-Transport-Security"] == "max-age=60; includeSubDomains"

    def test_h4_hsts_disabled(self):
        """HSTS を明示的に無効化。

        H4: SECURITY_HSTS_ENABLED=false の場合、Strict-Transport-Security
        ヘッダは含まない。
        """
        from api.middleware.security_headers import build_security_headers

        headers = build_security_headers({"SECURITY_HSTS_ENABLED": "false"})
        assert "Strict-Transport-Security" not in headers


class TestSecurityHeadersMiddleware:
    """SecurityHeadersMiddleware の統合テスト（TestClient 経由）。"""

    def test_h5_health_endpoint_default_headers(self):
        """デフォルト環境で /health GET → セキュリティヘッダ含む、HSTS なし。

        H5: _client_with_env() でリロードした app に対して GET /health を
        実行すると、CSP, X-Frame-Options, X-Content-Type-Options,
        Referrer-Policy が response に含まれ、Strict-Transport-Security は含まない。
        """
        client, m = _client_with_env()

        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Referrer-Policy"] == "no-referrer"
        assert "Strict-Transport-Security" not in resp.headers

    def test_h7_hsts_enabled_via_env(self):
        """HSTS 有効化環境で /health → Strict-Transport-Security ヘッダ含む。

        H7: SECURITY_HSTS_ENABLED=true, SECURITY_HSTS_MAX_AGE=120 で、
        /health の response に max-age=120; includeSubDomains を含む。
        """
        client, m = _client_with_env(
            SECURITY_HSTS_ENABLED="true",
            SECURITY_HSTS_MAX_AGE="120",
        )

        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.headers["Strict-Transport-Security"] == "max-age=120; includeSubDomains"

    def test_h8_csp_override_via_env(self):
        """CSP をカスタム値に上書きしてリロード、/health でそれを確認。

        H8: SECURITY_CSP="default-src 'self'" で環境変数をセットして
        app をリロードしたら、/health response の CSP がそれに変わっている。
        """
        client, m = _client_with_env(SECURITY_CSP="default-src 'self'")

        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.headers["Content-Security-Policy"] == "default-src 'self'"
