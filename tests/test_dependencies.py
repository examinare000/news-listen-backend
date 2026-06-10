"""api.dependencies のユニットテスト。"""
import pytest
from unittest.mock import patch
from fastapi import HTTPException


def test_get_firestore_client_returns_same_instance():
    """get_firestore_client() は同一インスタンスを返すこと（キャッシュ動作）。
    gRPC コネクションプールの再初期化を防ぐため lru_cache を使用する。
    """
    with patch("shared.firestore_client.firestore.Client"):
        import importlib
        import api.dependencies as deps
        importlib.reload(deps)  # キャッシュリセット

        client1 = deps.get_firestore_client()
        client2 = deps.get_firestore_client()
        assert client1 is client2


def test_get_user_id_returns_env_value():
    """USER_ID 環境変数が設定されている場合はその値を返す。"""
    with patch.dict("os.environ", {"USER_ID": "user123"}):
        from api.dependencies import get_user_id
        assert get_user_id() == "user123"


def test_get_user_id_raises_http_500_when_not_set():
    """USER_ID 環境変数が未設定の場合は HTTP 500 を送出する。
    サイレントなデフォルト値("default")でデータ混在バグが起きるのを防ぐ。
    """
    import os
    env = {k: v for k, v in os.environ.items() if k != "USER_ID"}
    with patch.dict("os.environ", env, clear=True):
        from api.dependencies import get_user_id
        with pytest.raises(HTTPException) as exc_info:
            get_user_id()
        assert exc_info.value.status_code == 500
