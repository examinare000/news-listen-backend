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
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

logger = logging.getLogger(__name__)

# APNs（HTTP/2）の送信先ホスト。sandbox は開発ビルド（aps-environment=development）向け。
APNS_PRODUCTION_HOST = "https://api.push.apple.com"
APNS_SANDBOX_HOST = "https://api.sandbox.push.apple.com"
# プロバイダトークン（JWT）の再利用上限。APNs 仕様の 1 時間より短く 50 分で再生成する。
_APNS_TOKEN_TTL_SECONDS = 50 * 60


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


@dataclass(frozen=True)
class ApnsConfig:
    """APNs（token-based provider）の設定。

    private_key は .p8 認証キーの本文（PEM）。鍵値そのものはログに出さない。
    """

    private_key: str
    key_id: str
    team_id: str
    bundle_id: str
    use_sandbox: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ApnsConfig | None:
        """環境変数から ApnsConfig を生成する。

        APNS_PRIVATE_KEY / APNS_KEY_ID / APNS_TEAM_ID / APNS_BUNDLE_ID が
        すべて設定されていなければ None を返す（no-op モード）。
        APNS_USE_SANDBOX が真値なら sandbox ホストを使う。
        """
        private_key = env.get("APNS_PRIVATE_KEY", "")
        key_id = env.get("APNS_KEY_ID", "")
        team_id = env.get("APNS_TEAM_ID", "")
        bundle_id = env.get("APNS_BUNDLE_ID", "")
        if not (private_key and key_id and team_id and bundle_id):
            return None
        use_sandbox = env.get("APNS_USE_SANDBOX", "").strip().lower() in ("1", "true", "yes")
        return cls(
            private_key=private_key,
            key_id=key_id,
            team_id=team_id,
            bundle_id=bundle_id,
            use_sandbox=use_sandbox,
        )


class ApnsNotifier:
    """iOS APNs 通知送信機（token-based provider / HTTP/2）。

    Web Push の `WebPushNotifier` と同じ `Notifier` プロトコルを満たす。
    実通信（httpx）と JWT 署名（PyJWT/ES256）は関数注入でき、テスト時の
    実通信・暗号依存をゼロにする。

    Args:
        db: FirestoreClient（トークン取得・削除に使用）
        config: ApnsConfig（None なら no-op）
        http_post_fn: ``(url, headers, payload) -> response`` 相当（テストで差し替え可能）。
            response は ``status_code`` と ``json()`` を持つことを期待する。
        token_provider: プロバイダトークン(JWT)を返す関数（テストで差し替え可能）。
        now_fn: 現在時刻を返す関数（トークンキャッシュ判定・テスト用）。
    """

    def __init__(
        self,
        db: Any,
        config: ApnsConfig | None,
        *,
        http_post_fn: Callable[..., Any] | None = None,
        token_provider: Callable[[], str] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._config = config
        self._http_post_fn = http_post_fn
        self._token_provider = token_provider
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._cached_token: str | None = None
        self._cached_token_at: datetime | None = None

    @property
    def _host(self) -> str:
        if self._config is not None and self._config.use_sandbox:
            return APNS_SANDBOX_HOST
        return APNS_PRODUCTION_HOST

    def notify_completion(
        self,
        user_id: str,
        *,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Podcast 生成完了を APNs で通知する。

        - 設定なし → no-op
        - トークンなし → no-op
        - 送信失敗 → warning のみ、例外を再 raise しない（非致命）
        - 410 / BadDeviceToken / Unregistered → 失効トークンを削除
        """
        if self._config is None:
            return

        tokens = self._db.get_apns_device_tokens(user_id)
        if not tokens:
            return

        # JWT 署名の失敗（不正な .p8 等）は warning に留め非致命とする（クラス契約どおり）。
        try:
            bearer = self._auth_token()
        except Exception as exc:
            logger.warning(
                "Failed to build APNs provider token for user_id=%s: %s",
                user_id,
                type(exc).__name__,
            )
            return

        payload_dict: dict[str, Any] = {
            "aps": {"alert": {"title": title, "body": body}, "sound": "default"},
        }
        if data:
            payload_dict.update(data)
        payload = json.dumps(payload_dict)
        headers = {
            "authorization": f"bearer {bearer}",
            "apns-topic": self._config.bundle_id,
            "apns-push-type": "alert",
        }

        # HTTP/2 コネクションをトークン間で再利用する（APNs は長寿命多重化接続を前提）。
        post, closer = self._make_poster()
        try:
            for token in tokens:
                url = f"{self._host}/3/device/{token.device_token}"
                try:
                    response = post(url, headers, payload)
                except Exception as exc:
                    logger.warning(
                        "Failed to send APNs notification to user_id=%s: %s",
                        user_id,
                        type(exc).__name__,
                    )
                    continue

                status_code = getattr(response, "status_code", None)
                if status_code is not None and 200 <= status_code < 300:
                    continue
                # 失効トークン（端末がアプリ削除等）→ 掃除する。
                if status_code == 410 or self._reason(response) in ("BadDeviceToken", "Unregistered"):
                    logger.warning(
                        "APNs device token invalid (HTTP %s), removing: user_id=%s",
                        status_code,
                        user_id,
                    )
                    self._db.delete_apns_device_token(user_id, token.device_token)
                else:
                    # None（応答不正）を含むその他の失敗。削除せず可視化する。
                    logger.warning(
                        "APNs send failed for user_id=%s (HTTP %s)",
                        user_id,
                        status_code,
                    )
        finally:
            closer()

    def _make_poster(self) -> tuple[Callable[..., Any], Callable[[], None]]:
        """送信関数とクローザを返す。注入があればそれを使い、無ければ HTTP/2 クライアントを1個張って再利用する。"""
        if self._http_post_fn is not None:
            return self._http_post_fn, lambda: None
        # 実運用時のみ httpx をインポート（HTTP/2 必須）。
        import httpx

        client = httpx.Client(http2=True)

        def post(url: str, headers: dict[str, str], payload: str) -> Any:
            return client.post(url, headers=headers, content=payload)

        return post, client.close

    def _auth_token(self) -> str:
        """プロバイダトークン(JWT/ES256)を返す。注入があればそれを使い、無ければ生成・キャッシュする。"""
        if self._token_provider is not None:
            return self._token_provider()

        now = self._now_fn()
        if (
            self._cached_token is not None
            and self._cached_token_at is not None
            and (now - self._cached_token_at).total_seconds() < _APNS_TOKEN_TTL_SECONDS
        ):
            return self._cached_token

        # 実運用時のみ PyJWT をインポート（テスト環境では token_provider 注入で不要）。
        import jwt

        assert self._config is not None
        token = jwt.encode(
            {"iss": self._config.team_id, "iat": int(now.timestamp())},
            self._config.private_key,
            algorithm="ES256",
            headers={"kid": self._config.key_id},
        )
        self._cached_token = token
        self._cached_token_at = now
        return token

    @staticmethod
    def _reason(response: Any) -> str | None:
        """APNs エラー応答ボディの reason を取り出す（取得不能なら None）。"""
        try:
            return response.json().get("reason")
        except Exception:
            return None


class CompositeNotifier:
    """複数の Notifier を束ねて順に呼ぶ。1 つの失敗が他を止めない（非致命）。"""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = list(notifiers)

    def notify_completion(
        self,
        user_id: str,
        *,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        for notifier in self._notifiers:
            try:
                notifier.notify_completion(user_id, title=title, body=body, data=data)
            except Exception as exc:
                logger.warning(
                    "Notifier %s failed: %s",
                    type(notifier).__name__,
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

    - VAPID 設定あり → WebPushNotifier を有効化
    - APNS 設定あり → ApnsNotifier を有効化
    - 両方あり → CompositeNotifier で並行送信
    - どちらも無ければ NoOpNotifier（ローカル/テスト安全）

    単一のみ有効なときはその Notifier をそのまま返す（既存挙動との互換）。
    """
    notifiers: list[Notifier] = []

    vapid = VapidConfig.from_env(env)
    if vapid is not None:
        notifiers.append(WebPushNotifier(db=db, vapid=vapid))

    apns = ApnsConfig.from_env(env)
    if apns is not None:
        notifiers.append(ApnsNotifier(db=db, config=apns))

    if not notifiers:
        return NoOpNotifier()
    if len(notifiers) == 1:
        return notifiers[0]
    return CompositeNotifier(notifiers)
