"""通知関連 API ルーター。

GET  /notifications/vapid-public-key  — VAPID 公開鍵を返す（未設定時 404）
POST /notifications/subscriptions    — Web Push 購読を登録（冪等）
DELETE /notifications/subscriptions  — Web Push 購読を解除（冪等）
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.dependencies import get_firestore_client, get_user_id
from shared.firestore_client import FirestoreClient
from shared.models import PushSubscription
from shared.notifier import VapidConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications")


class _PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class _SubscribeRequest(BaseModel):
    endpoint: str
    keys: _PushSubscriptionKeys
    expirationTime: str | None = None  # W3C 仕様に合わせて受け取るが無視する


@router.get("/vapid-public-key")
def get_vapid_public_key():
    """VAPID 公開鍵を返す。未設定時は 404。"""
    vapid = VapidConfig.from_env(os.environ)
    if vapid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="VAPID not configured",
        )
    return {"public_key": vapid.public_key}


@router.post("/subscriptions", status_code=status.HTTP_201_CREATED)
def subscribe(
    body: _SubscribeRequest,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    """Web Push 購読を登録する（冪等: 同一 endpoint は upsert）。"""
    sub = PushSubscription(
        user_id=user_id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        created_at=datetime.now(timezone.utc),
    )
    db.save_push_subscription(sub)
    return {"status": "subscribed"}


@router.delete("/subscriptions")
def unsubscribe(
    endpoint: str = Query(..., description="解除する Web Push 購読の endpoint URL"),
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    """Web Push 購読を解除する（冪等: 不在でも 200）。

    endpoint はクエリパラメータで渡す（既存 DELETE /settings/sources?url= と同じ規約）。
    """
    db.delete_push_subscription(user_id, endpoint)
    return {"status": "unsubscribed"}
