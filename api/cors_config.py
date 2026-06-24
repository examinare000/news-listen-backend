"""CORS ミドルウェアの設定を構成する。

build_cors_options() は環境変数から FastAPI の CORSMiddleware に渡すオプション
辞書を構成する純粋関数。
"""
from typing import Mapping


def build_cors_options(env: Mapping[str, str]) -> dict:
    """環境変数から CORS オプション辞書を構成する。

    Args:
        env: 環境変数の Mapping（通常は os.environ）

    Returns:
        FastAPI の CORSMiddleware に渡すオプション辞書。以下を含む:
        - allow_origins: 許可するオリジンのリスト。"*" は使用しない。
        - allow_credentials: True。
        - allow_methods: GET, POST, PUT, PATCH, DELETE, OPTIONS。
        - allow_headers: X-API-Key, Authorization, Content-Type。

    環境変数:
        - CORS_ALLOWED_ORIGINS: カンマ区切りのオリジン。
          未設定/空 → [] （全オリジン拒否、安全側デフォルト）
          例: "https://a.example, https://b.example"
    """
    # カンマ区切りのオリジンをパースし、各フラグメントを .strip()
    origins_str = env.get("CORS_ALLOWED_ORIGINS", "").strip()
    if origins_str:
        allow_origins = [o.strip() for o in origins_str.split(",")]
        # 空のフラグメント（複数カンマなど）を削除
        allow_origins = [o for o in allow_origins if o]
    else:
        allow_origins = []

    return {
        "allow_origins": allow_origins,
        "allow_credentials": True,
        # PATCH/PUT は実際に使用中（PATCH /auth/me, PUT /admin/featured-sites/{id} 等）。
        # 省くとブラウザのプリフライトで該当メソッドが拒否されるため必ず含める。
        "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        "allow_headers": ["X-API-Key", "Authorization", "Content-Type", "X-CSRF-Token"],
    }
