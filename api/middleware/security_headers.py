"""セキュリティヘッダ ASGI ミドルウェア。

build_security_headers() は環境変数から CSP, X-Frame-Options, X-Content-Type-Options,
Referrer-Policy, Strict-Transport-Security を構成する純粋関数。

SecurityHeadersMiddleware は ASGI raw middleware で、http.response.start の際に
これらのヘッダを応答に追加する。既存ヘッダ（CORS など）を上書きしない。
"""
from typing import Any, Awaitable, Callable, Mapping


def build_security_headers(env: Mapping[str, str]) -> dict[str, str]:
    """環境変数からセキュリティヘッダ辞書を構成する。

    Args:
        env: 環境変数の Mapping（通常は os.environ）

    Returns:
        セキュリティヘッダの dict。キーはヘッダ名、値は値。

    環境変数の構成:
        - SECURITY_CSP: Content-Security-Policy の値。
          デフォルト: "default-src 'none'; frame-ancestors 'none'"
        - SECURITY_X_FRAME_OPTIONS: X-Frame-Options の値。
          デフォルト: "DENY"
        - SECURITY_X_CONTENT_TYPE_OPTIONS: X-Content-Type-Options の値。
          デフォルト: "nosniff"
        - SECURITY_REFERRER_POLICY: Referrer-Policy の値。
          デフォルト: "no-referrer"
        - SECURITY_HSTS_ENABLED: HSTS を有効にするか（"true"/"1"/"yes" で真）。
          デフォルト: 無効（ローカル HTTP を壊さないため）
        - SECURITY_HSTS_MAX_AGE: HSTS max-age 秒。
          デフォルト: 31536000（1年）
    """
    headers: dict[str, str] = {}

    # CSP
    headers["Content-Security-Policy"] = env.get(
        "SECURITY_CSP", "default-src 'none'; frame-ancestors 'none'"
    )

    # X-Frame-Options
    headers["X-Frame-Options"] = env.get("SECURITY_X_FRAME_OPTIONS", "DENY")

    # X-Content-Type-Options
    headers["X-Content-Type-Options"] = env.get("SECURITY_X_CONTENT_TYPE_OPTIONS", "nosniff")

    # Referrer-Policy
    headers["Referrer-Policy"] = env.get("SECURITY_REFERRER_POLICY", "no-referrer")

    # HSTS（有効化時のみ）
    hsts_enabled = env.get("SECURITY_HSTS_ENABLED", "").lower() in ("1", "true", "yes")
    if hsts_enabled:
        # 空文字が設定された場合も既定値にフォールバック（max-age=; の不正値を防ぐ）。
        max_age = env.get("SECURITY_HSTS_MAX_AGE") or "31536000"
        headers["Strict-Transport-Security"] = f"max-age={max_age}; includeSubDomains"

    return headers


class SecurityHeadersMiddleware:
    """セキュリティヘッダを応答に追加する raw ASGI ミドルウェア。

    http.response.start で以下の処理を行う:
    - self._headers に含まれるヘッダを、既に存在しないもの限定で追加する。
    - 既存ヘッダ（CORS など）を上書きしない（case-insensitive で判定）。
    - HTTP スコープのみ処理；WebSocket など他のスコープは pass-through。

    Attributes:
        _app: 次のミドルウェア／アプリケーション。
        _headers: セキュリティヘッダの dict（キー=ヘッダ名）。
    """

    def __init__(self, app: Callable, headers: dict[str, str]):
        """初期化。

        Args:
            app: ASGI アプリケーション。
            headers: セキュリティヘッダの dict。
        """
        self._app = app
        self._headers = headers

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """ASGI ミドルウェア呼び出し。

        HTTP スコープの場合、http.response.start で send をラップし
        セキュリティヘッダを追加する。
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            """http.response.start で送信されるメッセージにヘッダを追加。"""
            if message["type"] == "http.response.start":
                # 既に存在するヘッダ名を小文字で取得（大文字小文字不問で判定）
                existing_headers_lower = {
                    name.decode().lower() for name, _ in message.get("headers", [])
                }

                # self._headers から、まだ存在しないヘッダだけ追加
                for header_name, header_value in self._headers.items():
                    if header_name.lower() not in existing_headers_lower:
                        message.setdefault("headers", []).append(
                            (header_name.lower().encode(), header_value.encode())
                        )

            await send(message)

        await self._app(scope, receive, send_with_headers)
