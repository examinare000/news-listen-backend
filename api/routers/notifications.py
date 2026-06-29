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
from pydantic import BaseModel, Field

from api.dependencies import get_firestore_client, get_user_id
from shared.firestore_client import FirestoreClient
from shared.models import ApnsDeviceToken, PushSubscription
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


class _DeviceTokenRequest(BaseModel):
    # APNs デバイストークンは 16 進文字列。登録時に形式を検証して、URL パス（/3/device/<token>）への
    # 不正文字混入（パストラバーサル・クエリ注入）やゴミ値の保存を入口で弾く。
    device_token: str = Field(min_length=64, max_length=200, pattern=r"^[0-9a-fA-F]+$")


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


@router.post("/device-tokens", status_code=status.HTTP_201_CREATED)
def register_device_token(
    body: _DeviceTokenRequest,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    """iOS APNs デバイストークンを登録する（冪等: 同一 token は upsert）。"""
    token = ApnsDeviceToken(
        user_id=user_id,
        device_token=body.device_token,
        created_at=datetime.now(timezone.utc),
    )
    db.save_apns_device_token(token)
    return {"status": "registered"}


@router.delete("/device-tokens")
def unregister_device_token(
    token: str = Query(..., description="解除する APNs デバイストークン"),
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    """iOS APNs デバイストークンを解除する（冪等: 不在でも 200）。

    token はクエリパラメータで渡す（既存 DELETE /notifications/subscriptions?endpoint= と同じ規約）。
    """
    db.delete_apns_device_token(user_id, token)
    return {"status": "unregistered"}
