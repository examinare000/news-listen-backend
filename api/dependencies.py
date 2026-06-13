"""FastAPI 依存性注入の定義。

FirestoreClient はリクエストごとの再生成を避けるために lru_cache でシングルトン化する。
gRPC コネクションプールの初期化コストはリクエストあたり数十〜数百ms であるため、
ウォームスタートでは同一インスタンスを使い回す。

USER_ID は環境変数から取得し、未設定の場合は HTTP 500 で即座に失敗させる。
サイレントなデフォルト値("default")によるデータ混在バグを防ぐため。
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from fastapi import HTTPException, status

from shared.firestore_client import FirestoreClient
from shared.storage_client import StorageClient

_logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_storage_client() -> StorageClient:
    """StorageClient のシングルトンを返す。

    lru_cache により同一プロセス内で同じインスタンスを再利用する。
    signed URL 生成のたびに新インスタンスを作るコストを避けるため。
    """
    return StorageClient()


@lru_cache(maxsize=1)
def get_firestore_client() -> FirestoreClient:
    """FirestoreClient のシングルトンを返す。

    lru_cache により同一プロセス内で同じインスタンスを再利用する。
    Cloud Run のコールドスタート時のみ新規に初期化される。
    """
    return FirestoreClient()


@lru_cache(maxsize=1)
def get_job_trigger():
    """JobTrigger のシングルトンを返す。

    JOB_TRIGGER_BACKEND 環境変数で起動経路（Cloud Run / ローカルサブプロセス）を切り替える。
    debounce ロックに FirestoreClient を共有するため get_firestore_client() を注入する。
    """
    # 遅延 import: モジュール読み込み時の循環依存を避ける。
    from shared.job_trigger import build_job_trigger

    return build_job_trigger(get_firestore_client())


def get_user_id() -> str:
    """環境変数 USER_ID を返す。未設定の場合は HTTP 500 を送出する。

    os.environ.get("USER_ID", "default") のようなサイレントフォールバックは
    複数ユーザーのデータ混在バグを引き起こすため使用しない。
    エラー詳細には内部の環境変数名を含めず、診断情報はサーバーサイドログのみに記録する。
    """
    user_id = os.environ.get("USER_ID")
    if not user_id:
        # 内部設定情報（環境変数名）を HTTP レスポンスに含めるとセキュリティリスクがある。
        # 診断情報はサーバーサイドログに出力し、クライアントには汎用エラーを返す。
        _logger.error("USER_ID environment variable is not set")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
    return user_id
