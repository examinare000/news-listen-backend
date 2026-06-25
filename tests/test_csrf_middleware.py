"""
CSRF ミドルウェアのテスト
"""
import pytest

from api.middleware.csrf import (
    tokens_match,
    generate_csrf_token,
    is_csrf_exempt,
    CsrfConfig,
    build_csrf_config,
    CsrfMiddleware,
)


# ============================================================================
# T1: tokens_match
# ============================================================================

class TestTokensMatch:
    """cookie と header のトークンが一致するか検証する関数"""

    def test_tokens_match_identical(self):
        """同じ値が与えられたら True を返す"""
        token = "test_token_abc123"
        assert tokens_match(token, token) is True

    def test_tokens_match_different(self):
        """異なる値が与えられたら False を返す"""
        assert tokens_match("token_a", "token_b") is False

    def test_tokens_match_empty_cookie(self):
        """cookie が空文字列なら False を返す"""
        assert tokens_match("", "valid_token") is False

    def test_tokens_match_empty_header(self):
        """header が空文字列なら False を返す"""
        assert tokens_match("valid_token", "") is False

    def test_tokens_match_both_empty(self):
        """両方とも空文字列なら False を返す（一致していても不許可）"""
        assert tokens_match("", "") is False


# ============================================================================
# T2: generate_csrf_token
# ============================================================================

class TestGenerateCsrfToken:
    """CSRF トークンを生成する関数"""

    def test_generate_csrf_token_returns_str(self):
        """戻り値は str 型である"""
        token = generate_csrf_token()
        assert isinstance(token, str)

    def test_generate_csrf_token_unique(self):
        """2回呼び出すと異なる値を返す"""
        token1 = generate_csrf_token()
        token2 = generate_csrf_token()
        assert token1 != token2


# ============================================================================
# T3: is_csrf_exempt
# ============================================================================

class TestIsCsrfExempt:
    """リクエストが CSRF 保護から免除されるかを判定する関数"""

    def test_exempt_get(self):
        """GET メソッドは免除される"""
        assert is_csrf_exempt("GET", has_bearer=False, path="/foo", exempt_paths=set()) is True

    def test_exempt_head(self):
        """HEAD メソッドは免除される"""
        assert is_csrf_exempt("HEAD", has_bearer=False, path="/foo", exempt_paths=set()) is True

    def test_exempt_options(self):
        """OPTIONS メソッドは免除される"""
        assert is_csrf_exempt("OPTIONS", has_bearer=False, path="/foo", exempt_paths=set()) is True

    def test_exempt_bearer(self):
        """Bearer トークンがあれば POST でも免除される（メソッドに関わらず）"""
        assert is_csrf_exempt("POST", has_bearer=True, path="/foo", exempt_paths=set()) is True

    def test_exempt_path(self):
        """exempt_paths に含まれるパスへの POST は免除される"""
        exempt_paths = {"/auth/login", "/health"}
        assert is_csrf_exempt("POST", has_bearer=False, path="/auth/login", exempt_paths=exempt_paths) is True
        assert is_csrf_exempt("POST", has_bearer=False, path="/health", exempt_paths=exempt_paths) is True

    def test_not_exempt_post(self):
        """POST でかつ Bearer がなく exempt_paths にも含まれなければ非免除"""
        assert is_csrf_exempt("POST", has_bearer=False, path="/foo", exempt_paths=set()) is False

    def test_not_exempt_case_insensitive(self):
        """メソッドは大文字小文字不問で判定（post でも非免除）"""
        # 小文字メソッドでも非免除
        assert is_csrf_exempt("post", has_bearer=False, path="/foo", exempt_paths=set()) is False


# ============================================================================
# T4: CsrfConfig と build_csrf_config
# ============================================================================

class TestCsrfConfig:
    """CSRF 設定を表現する dataclass"""

    def test_csrf_config_default(self):
        """環境変数未設定時のデフォルト値"""
        env = {}
        config = build_csrf_config(env)
        assert config.enabled is False
        # 未認証の事前認証エンドポイント（login + password reset）は CSRF 既定免除。
        assert config.exempt_paths == {
            "/auth/login",
            "/auth/password/forgot",
            "/auth/password/reset",
        }

    def test_csrf_config_enabled_true(self):
        """CSRF_PROTECTION_ENABLED=true で enabled=True"""
        env = {"CSRF_PROTECTION_ENABLED": "true"}
        config = build_csrf_config(env)
        assert config.enabled is True

    def test_csrf_config_enabled_yes(self):
        """CSRF_PROTECTION_ENABLED=yes で enabled=True"""
        env = {"CSRF_PROTECTION_ENABLED": "yes"}
        config = build_csrf_config(env)
        assert config.enabled is True

    def test_csrf_config_enabled_1(self):
        """CSRF_PROTECTION_ENABLED=1 で enabled=True"""
        env = {"CSRF_PROTECTION_ENABLED": "1"}
        config = build_csrf_config(env)
        assert config.enabled is True

    def test_csrf_config_enabled_false_default(self):
        """CSRF_PROTECTION_ENABLED が false/0/その他なら enabled=False"""
        for value in ["false", "0", "no", "invalid"]:
            env = {"CSRF_PROTECTION_ENABLED": value}
            config = build_csrf_config(env)
            assert config.enabled is False

    def test_csrf_config_enabled_case_insensitive(self):
        """大文字小文字不問で判定"""
        for value in ["TRUE", "True", "YES", "Yes"]:
            env = {"CSRF_PROTECTION_ENABLED": value}
            config = build_csrf_config(env)
            assert config.enabled is True

    def test_csrf_config_exempt_paths(self):
        """CSRF_EXEMPT_PATHS がカンマ区切りで設定される"""
        env = {"CSRF_EXEMPT_PATHS": "/auth/login,/health,/metrics"}
        config = build_csrf_config(env)
        assert config.exempt_paths == {"/auth/login", "/health", "/metrics"}

    def test_csrf_config_exempt_paths_single(self):
        """CSRF_EXEMPT_PATHS が1つのみの場合"""
        env = {"CSRF_EXEMPT_PATHS": "/health"}
        config = build_csrf_config(env)
        assert config.exempt_paths == {"/health"}

    def test_csrf_config_exempt_trailing_comma(self):
        """CSRF_EXEMPT_PATHS の末尾にカンマがあれば空フラグメントは除去"""
        env = {"CSRF_EXEMPT_PATHS": "/auth/login,/health,"}
        config = build_csrf_config(env)
        # 空フラグメント "" は除去され、{"/auth/login", "/health"} のみ
        assert config.exempt_paths == {"/auth/login", "/health"}

    def test_csrf_config_exempt_leading_trailing_spaces(self):
        """パスの前後の空白は除去"""
        env = {"CSRF_EXEMPT_PATHS": " /auth/login , /health "}
        config = build_csrf_config(env)
        assert config.exempt_paths == {"/auth/login", "/health"}

    def test_csrf_config_exempt_blank_falls_back_to_default(self):
        """空白のみ・カンマのみの値は既定 /auth/login へ縮退する（ログイン不能の防止）。

        WHY: CSRF_EXEMPT_PATHS=" " や "," で空集合になると /auth/login まで CSRF 必須となり、
        トークン未取得のクライアントがログインできなくなる（ロックアウト）。
        """
        default_exempt = {
            "/auth/login",
            "/auth/password/forgot",
            "/auth/password/reset",
        }
        for value in ["   ", ",", " , "]:
            config = build_csrf_config({"CSRF_EXEMPT_PATHS": value})
            assert config.exempt_paths == default_exempt, f"value={value!r}"


# ============================================================================
# T5: CsrfMiddleware
# ============================================================================

class TestCsrfMiddleware:
    """CSRF 保護ミドルウェアの ASGI インターフェース"""

    @pytest.mark.asyncio
    async def test_middleware_disabled_pass_through(self):
        """enabled=False なら全リクエストが pass-through される"""
        config = CsrfConfig(enabled=False, exempt_paths=set())

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CsrfMiddleware(app, config)

        # POST リクエスト（csrf_token/X-CSRF-Token なし）でも通過するべき
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        # レスポンスが 403 でなく 200 であることを確認
        assert received_responses[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_middleware_get_pass_through(self):
        """GET リクエストは通過する（CSRF チェックなし）"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CsrfMiddleware(app, config)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/test",
            "headers": [],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        assert received_responses[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_middleware_bearer_pass_through(self):
        """POST + Bearer トークン → 通過（cookie/header がなくても）"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CsrfMiddleware(app, config)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [(b"authorization", b"Bearer eyJhbGciOi...")],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        assert received_responses[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_middleware_exempt_path_post(self):
        """exempt_paths に含まれるパスへの POST は通過"""
        config = CsrfConfig(enabled=True, exempt_paths={"/auth/login", "/health"})

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CsrfMiddleware(app, config)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        assert received_responses[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_middleware_missing_cookie_403(self):
        """cookie がない → 403 Forbidden"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            # ここに到達すべきでない
            pytest.fail("app() should not be called")

        middleware = CsrfMiddleware(app, config)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [(b"x-csrf-token", b"some_token")],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        assert received_responses[0]["status"] == 403

    @pytest.mark.asyncio
    async def test_middleware_missing_header_403(self):
        """X-CSRF-Token ヘッダがない → 403 Forbidden"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            pytest.fail("app() should not be called")

        middleware = CsrfMiddleware(app, config)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [(b"cookie", b"csrf_token=some_token")],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        assert received_responses[0]["status"] == 403

    @pytest.mark.asyncio
    async def test_middleware_mismatch_403(self):
        """cookie と header のトークンが不一致 → 403 Forbidden"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            pytest.fail("app() should not be called")

        middleware = CsrfMiddleware(app, config)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [
                (b"cookie", b"csrf_token=token_a"),
                (b"x-csrf-token", b"token_b"),
            ],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        assert received_responses[0]["status"] == 403

    @pytest.mark.asyncio
    async def test_middleware_match_pass_through(self):
        """cookie と header のトークンが一致 → 通過"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CsrfMiddleware(app, config)

        token = "valid_csrf_token_123"
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [
                (b"cookie", f"csrf_token={token}".encode()),
                (b"x-csrf-token", token.encode()),
            ],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        assert received_responses[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_middleware_403_body_no_token(self):
        """403 レスポンス本文にトークン値を含まない"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            pytest.fail("app() should not be called")

        middleware = CsrfMiddleware(app, config)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        # 403 スタータス
        assert received_responses[0]["status"] == 403

        # ボディを確認
        body = received_responses[1]["body"]
        body_str = body.decode("utf-8")

        # トークン値を含まない（detail メッセージのみ）
        assert "csrf_token" not in body_str
        assert "token" not in body_str.lower() or "CSRF token" in body_str  # "CSRF token" は許可
        assert "detail" in body_str

    @pytest.mark.asyncio
    async def test_middleware_lowercase_bearer_pass_through(self):
        """小文字 'bearer ' でも免除される（RFC 7235・dependencies と整合）。

        WHY: 認証スキーム名は case-insensitive。揃えないと小文字 bearer を送る
        クライアントが CSRF 免除されず 403 になる。
        """
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CsrfMiddleware(app, config)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [(b"authorization", b"bearer eyJhbGciOi...")],
        }
        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)
        assert received_responses[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_middleware_token_in_second_cookie_header(self):
        """Cookie ヘッダが複数本に分かれていても csrf_token を拾える（HTTP/2 分割対策）。"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CsrfMiddleware(app, config)
        token = "valid_csrf_token_123"
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [
                (b"cookie", b"nl_session=abc"),
                (b"cookie", f"csrf_token={token}".encode()),
                (b"x-csrf-token", token.encode()),
            ],
        }
        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)
        assert received_responses[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_middleware_quoted_cookie_value_matches(self):
        """RFC 6265 のダブルクォート包み cookie 値もヘッダ値と一致判定できる。"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CsrfMiddleware(app, config)
        token = "valid_csrf_token_123"
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [
                (b"cookie", f'csrf_token="{token}"'.encode()),
                (b"x-csrf-token", token.encode()),
            ],
        }
        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "http.disconnect"}

        await middleware(scope, mock_receive, mock_send)
        assert received_responses[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_middleware_websocket_pass_through(self):
        """WebSocket スコープは pass-through（http でないため）"""
        config = CsrfConfig(enabled=True, exempt_paths=set())

        async def app(scope, receive, send):
            await send({"type": "websocket.accept"})

        middleware = CsrfMiddleware(app, config)

        scope = {
            "type": "websocket",
            "path": "/ws/test",
            "headers": [],
        }

        received_responses = []

        async def mock_send(message):
            received_responses.append(message)

        async def mock_receive():
            return {"type": "websocket.disconnect"}

        await middleware(scope, mock_receive, mock_send)

        # app() が呼ばれたことを確認（pass-through）
        assert len(received_responses) > 0
