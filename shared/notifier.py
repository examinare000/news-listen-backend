"""通知送信の抽象と Web Push 実装。

WebPushNotifier は pywebpush を直接知る唯一の場所。
webpush_fn を関数注入することでテスト時の実通信をゼロにする。
VAPID 鍵未設定なら no-op（ローカル/テストで安全）。
送信失敗（購読期限切れを含む）は warning ログのみで非致命。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def notify_completion(
        self,
        user_id: str,
        *,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class VapidConfig:
    private_key: str
    public_key: str
    claims_email: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> VapidConfig | None:
        """環境変数から VapidConfig を生成する。

        3 つすべての VAPID_* キーが設定されていなければ None を返す（no-op モード）。
        鍵値そのものはログに出さない（セキュリティ要件）。
        """
        private_key = env.get("VAPID_PRIVATE_KEY", "")
        public_key = env.get("VAPID_PUBLIC_KEY", "")
        claims_email = env.get("VAPID_CLAIMS_EMAIL", "")
        if not (private_key and public_key and claims_email):
            return None
        return cls(
            private_key=private_key,
            public_key=public_key,
            claims_email=claims_email,
        )


class WebPushNotifier:
    """Web Push 通知送信機。

    Args:
        db: FirestoreClient（購読取得・削除に使用）
        vapid: VapidConfig（None なら no-op）
        webpush_fn: pywebpush.webpush 相当の関数（テストで差し替え可能）
    """

    def __init__(
        self,
        db: Any,
        vapid: VapidConfig | None,
        webpush_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._db = db
        self._vapid = vapid
        if webpush_fn is not None:
            self._webpush_fn = webpush_fn
        elif vapid is not None:
            # 実運用時のみ pywebpush をインポート（テスト環境では不要）
            from pywebpush import webpush as _webpush
            self._webpush_fn = _webpush
        else:
            self._webpush_fn = None

    def notify_completion(
        self,
        user_id: str,
        *,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Podcast 生成完了を Web Push で通知する。

        - VAPID 未設定 → no-op（ローカル/テスト安全）
        - 購読なし → no-op
        - 送信失敗 → warning のみ、例外を再 raise しない（非致命）
        - 410/404 → 購読削除（失効購読の掃除）
        """
        if self._vapid is None:
            return

        subs = self._db.get_push_subscriptions(user_id)
        if not subs:
            return

        payload = json.dumps({"title": title, "body": body, **(data or {})})

        for sub in subs:
            try:
                self._webpush_fn(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                    },
                    data=payload,
                    vapid_private_key=self._vapid.private_key,
                    vapid_claims={
                        "sub": f"mailto:{self._vapid.claims_email}",
                    },
                )
            except Exception as exc:
                # 410 Gone / 404 Not Found → 失効購読を削除する
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code in (404, 410):
                    logger.warning(
                        "Push subscription expired (HTTP %s), removing: user_id=%s",
                        status_code,
                        user_id,
                    )
                    self._db.delete_push_subscription(user_id, sub.endpoint)
                else:
                    logger.warning(
                        "Failed to send push notification to user_id=%s: %s",
                        user_id,
                        type(exc).__name__,
                    )


class NoOpNotifier:
    """テスト・ローカル用の no-op 通知送信機。"""

    def notify_completion(
        self,
        user_id: str,
        *,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        pass


def build_notifier(db: Any, env: Mapping[str, str]) -> Notifier:
    """環境変数から Notifier を生成する。

    VAPID 鍵が設定されていれば WebPushNotifier、未設定なら NoOpNotifier を返す。
    """
    vapid = VapidConfig.from_env(env)
    if vapid is None:
        return NoOpNotifier()
    return WebPushNotifier(db=db, vapid=vapid)
