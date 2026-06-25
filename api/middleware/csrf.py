"""
CSRF（Cross-Site Request Forgery）保護ミドルウェア

CSRF トークンの検証を行い、安全でないリクエスト（POST/PUT/DELETE等）に対して
トークンの一致を確認する。安全メソッド（GET/HEAD/OPTIONS）や Bearer トークンがある
リクエスト、または免除対象パスは CSRF チェックをスキップする。
"""
import hmac
import secrets
from dataclasses import dataclass
from typing import Mapping


def tokens_match(cookie_token: str, header_token: str) -> bool:
    """
    Cookie から取得したトークンと Header から取得したトークンが一致するか検証する。

    hmac.compare_digest を使うことで、タイミング攻撃を防止する。
    空文字が与えられた場合は False を返す（両方空でも不一致扱い）。

    Args:
        cookie_token: Cookie から取得したトークン値
        header_token: X-CSRF-Token ヘッダから取得したトークン値

    Returns:
        両トークンが同じ内容なら True、異なるか空文字列が含まれていれば False
    """
    # 空文字列は不一致扱い
    if not cookie_token or not header_token:
        return False

    return hmac.compare_digest(cookie_token, header_token)


def generate_csrf_token() -> str:
    """
    CSRF 保護用のセキュアなトークンを生成する。

    secrets.token_urlsafe() で URL-safe な Base64 エンコード文字列を生成する。

    Returns:
        ランダムに生成された CSRF トークン
    """
    return secrets.token_urlsafe(32)


def is_csrf_exempt(method: str, has_bearer: bool, path: str, exempt_paths: set[str]) -> bool:
    """
    リクエストが CSRF 保護チェックから免除されるかを判定する。

    以下のいずれかに該当すれば免除される:
    - メソッドが安全メソッド（GET/HEAD/OPTIONS）
    - Authorization ヘッダに Bearer トークンが存在
    - パスが免除対象リスト（exempt_paths）に含まれている

    メソッドは大文字小文字不問で判定される。

    Args:
        method: HTTP メソッド（GET/POST/PUT等、大文字小文字不問）
        has_bearer: Authorization ヘッダに "Bearer " で始まるトークンが存在するか
        path: リクエストパス
        exempt_paths: CSRF チェック免除対象のパスセット

    Returns:
        リクエストが CSRF チェックから免除されるなら True、チェック必須なら False
    """
    method_upper = method.upper()

    # 安全メソッド（キャッシュ可、副作用なし）は常に免除
    if method_upper in ("GET", "HEAD", "OPTIONS"):
        return True

    # Bearer トークンがあれば免除（API 認証が既に検証済み）
    if has_bearer:
        return True

    # パスが免除リストに含まれていれば免除
    if path in exempt_paths:
        return True

    return False


@dataclass
class CsrfConfig:
    """
    CSRF 保護の設定

    Attributes:
        enabled: CSRF 保護が有効か否か
        exempt_paths: CSRF チェック免除対象のパスセット
    """
    enabled: bool
    exempt_paths: set[str]


def build_csrf_config(env: Mapping[str, str]) -> CsrfConfig:
    """
    環境変数から CSRF 設定を構築する。

    環境変数:
    - CSRF_PROTECTION_ENABLED: "true", "yes", "1" で有効（大文字小文字不問）、
                               未設定またはそれ以外で無効
    - CSRF_EXEMPT_PATHS: カンマ区切りのパスリスト（デフォルト: "/auth/login"）

    Args:
        env: 環境変数マッピング

    Returns:
        構築された CsrfConfig
    """
    # CSRF_PROTECTION_ENABLED を解析
    enabled_str = env.get("CSRF_PROTECTION_ENABLED", "").lower()
    enabled = enabled_str in ("true", "yes", "1")

    # CSRF_EXEMPT_PATHS を解析（カンマ区切り、前後空白と空フラグメントを除去）
    paths = [p.strip() for p in env.get("CSRF_EXEMPT_PATHS", "").split(",")]
    exempt_paths = {p for p in paths if p}
    # WHY: env が未設定や空白のみ（例 " " や ","）の場合に空集合へ縮退すると、
    #       /auth/login まで CSRF 必須になり「トークン未取得 → ログイン不能」のロックアウトを招く。
    #       明示的に空でない値が与えられた時だけ上書きし、それ以外は既定を保証する。
    #       password/forgot・password/reset は**未認証**（セッション Cookie 無し）の事前認証
    #       エンドポイントで、リセットトークン自体が CSRF 相当の保護を担う。double-submit
    #       cookie を要求するとログアウト中のユーザーがリセットできなくなるため login と同様に免除する。
    if not exempt_paths:
        exempt_paths = {"/auth/login", "/auth/password/forgot", "/auth/password/reset"}

    return CsrfConfig(enabled=enabled, exempt_paths=exempt_paths)


def _extract_cookie_value(headers, name: str) -> str | None:
    """ASGI raw headers から指定 Cookie の値を取り出す（無ければ None）。

    WHY: HTTP/2 等では Cookie ヘッダが複数本に分割されうるため最初の1本で打ち切らない。
         前後空白を除去し、RFC 6265 のダブルクォート包みも剥がしてヘッダ値と突合可能にする。
    """
    prefix = f"{name}="
    for header_name, header_value in headers:
        if header_name.lower() != b"cookie":
            continue
        cookie_str = header_value.decode("utf-8", errors="ignore")
        for cookie_part in cookie_str.split(";"):
            cookie_part = cookie_part.strip()
            if cookie_part.startswith(prefix):
                value = cookie_part[len(prefix):].strip()
                if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                    value = value[1:-1]
                return value
    return None


class CsrfMiddleware:
    """
    ASGI ミドルウェア: CSRF トークンの検証を行う

    enabled=False の場合は全リクエストをスキップ。
    HTTP スコープのみ処理し、WebSocket 等は pass-through する。
    """

    def __init__(self, app, config: CsrfConfig):
        """
        ミドルウェアを初期化する。

        Args:
            app: 下流の ASGI アプリケーション
            config: CSRF 設定
        """
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        """
        ASGI インターフェース。HTTP リクエストの CSRF トークンを検証する。

        Args:
            scope: ASGI スコープ
            receive: ASGI receive callable
            send: ASGI send callable
        """
        # HTTP スコープ以外は pass-through
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # CSRF 保護が無効なら pass-through
        if not self.config.enabled:
            await self.app(scope, receive, send)
            return

        # リクエスト情報を抽出
        method = scope.get("method", "")
        path = scope.get("path", "")
        headers = scope.get("headers", [])

        # Authorization ヘッダから Bearer トークンの有無を判定。
        # WHY: 認証スキーム名は RFC 7235 上 case-insensitive。dependencies._extract_session_token
        #       は "bearer " を小文字化して判定するため、ここも揃える。揃えないと小文字 bearer を
        #       送る iOS クライアントが CSRF 免除されず 403 になる。
        has_bearer = False
        for header_name, header_value in headers:
            if header_name.lower() == b"authorization":
                has_bearer = header_value.lower().startswith(b"bearer ")
                break

        # CSRF チェック免除かどうかを判定
        if is_csrf_exempt(method, has_bearer, path, self.config.exempt_paths):
            await self.app(scope, receive, send)
            return

        # Cookie / ヘッダから csrf_token を抽出（複数 Cookie ヘッダ・クォート包みに対応）
        cookie_token = _extract_cookie_value(headers, "csrf_token")

        header_token = None
        for header_name, header_value in headers:
            if header_name.lower() == b"x-csrf-token":
                header_token = header_value.decode("utf-8", errors="ignore").strip()
                break

        # トークンを検証
        if not tokens_match(cookie_token or "", header_token or ""):
            # CSRF トークン不一致または欠落 → 403 Forbidden を送信
            await self._send_csrf_error(send)
            return

        # トークン検証成功 → 下流に処理を委譲
        await self.app(scope, receive, send)

    async def _send_csrf_error(self, send):
        """
        CSRF エラー（403 Forbidden）を送信する。

        レスポンス本文にはトークン値を含めない（セキュリティ）。

        Args:
            send: ASGI send callable
        """
        body = b'{"detail":"CSRF token missing or invalid"}'

        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })

        await send({
            "type": "http.response.body",
            "body": body,
        })
