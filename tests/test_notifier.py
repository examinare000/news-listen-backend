"""Web Push 通知送信機のテスト。

VapidConfig / WebPushNotifier / NoOpNotifier の動作と統合テストをカバー。
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from shared.models import ApnsDeviceToken, PushSubscription
from shared.notifier import (
    ApnsConfig,
    ApnsNotifier,
    CompositeNotifier,
    NoOpNotifier,
    VapidConfig,
    WebPushNotifier,
    build_notifier,
)

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


# ---------- VapidConfig ----------


class TestVapidConfig:
    def test_from_env_with_all_vars_returns_config(self):
        """環境変数に VAPID_* が全て設定されていれば VapidConfig を返す"""
        env = {
            "VAPID_PRIVATE_KEY": "priv_key_value",
            "VAPID_PUBLIC_KEY": "pub_key_value",
            "VAPID_CLAIMS_EMAIL": "push@example.com",
        }
        config = VapidConfig.from_env(env)
        assert config is not None
        assert config.private_key == "priv_key_value"
        assert config.public_key == "pub_key_value"
        assert config.claims_email == "push@example.com"

    def test_from_env_with_missing_vars_returns_none(self):
        """環境変数に VAPID_* が不足していれば None を返す"""
        env = {
            "VAPID_PRIVATE_KEY": "priv",
            "VAPID_PUBLIC_KEY": "pub",
            # VAPID_CLAIMS_EMAIL が無い
        }
        config = VapidConfig.from_env(env)
        assert config is None

    def test_from_env_with_empty_env_returns_none(self):
        """空の環境変数辞書は None を返す"""
        env = {}
        config = VapidConfig.from_env(env)
        assert config is None

    def test_from_env_with_empty_string_values_returns_none(self):
        """VAPID_* が空文字列なら None を返す（偽値判定）"""
        env = {
            "VAPID_PRIVATE_KEY": "",
            "VAPID_PUBLIC_KEY": "",
            "VAPID_CLAIMS_EMAIL": "",
        }
        config = VapidConfig.from_env(env)
        assert config is None

    def test_vapid_config_is_frozen(self):
        """VapidConfig が immutable であること"""
        config = VapidConfig(
            private_key="priv",
            public_key="pub",
            claims_email="email@example.com",
        )
        with pytest.raises(AttributeError):
            config.private_key = "modified"


# ---------- WebPushNotifier ----------


class TestWebPushNotifier:
    def test_notify_completion_no_op_when_vapid_is_none(self):
        """VAPID が None なら notify_completion は no-op"""
        mock_db = MagicMock()
        notifier = WebPushNotifier(db=mock_db, vapid=None)

        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
        )

        # DB が呼ばれない
        mock_db.get_push_subscriptions.assert_not_called()

    def test_notify_completion_no_op_when_no_subscriptions(self):
        """購読が無ければ webpush_fn は呼ばれない"""
        mock_db = MagicMock()
        mock_db.get_push_subscriptions.return_value = []
        mock_webpush = MagicMock()

        vapid = VapidConfig(
            private_key="priv",
            public_key="pub",
            claims_email="email@example.com",
        )
        notifier = WebPushNotifier(db=mock_db, vapid=vapid, webpush_fn=mock_webpush)

        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
        )

        mock_db.get_push_subscriptions.assert_called_once_with("user1")
        mock_webpush.assert_not_called()

    def test_notify_completion_calls_webpush_fn_once_per_subscription(self):
        """購読が複数あれば webpush_fn は購読数分呼ばれる"""
        sub1 = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/1",
            p256dh="key1",
            auth="auth1",
            created_at=NOW,
        )
        sub2 = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/2",
            p256dh="key2",
            auth="auth2",
            created_at=NOW,
        )

        mock_db = MagicMock()
        mock_db.get_push_subscriptions.return_value = [sub1, sub2]
        mock_webpush = MagicMock()

        vapid = VapidConfig(
            private_key="priv",
            public_key="pub",
            claims_email="email@example.com",
        )
        notifier = WebPushNotifier(db=mock_db, vapid=vapid, webpush_fn=mock_webpush)

        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
        )

        assert mock_webpush.call_count == 2

    def test_notify_completion_passes_correct_subscription_info(self):
        """webpush_fn に正しい subscription_info が渡されること"""
        sub = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/abc",
            p256dh="dh_key_base64",
            auth="auth_secret_base64",
            created_at=NOW,
        )

        mock_db = MagicMock()
        mock_db.get_push_subscriptions.return_value = [sub]
        mock_webpush = MagicMock()

        vapid = VapidConfig(
            private_key="priv",
            public_key="pub",
            claims_email="email@example.com",
        )
        notifier = WebPushNotifier(db=mock_db, vapid=vapid, webpush_fn=mock_webpush)

        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
        )

        call_args = mock_webpush.call_args
        assert call_args[1]["subscription_info"]["endpoint"] == "https://push.example.com/abc"
        assert call_args[1]["subscription_info"]["keys"]["p256dh"] == "dh_key_base64"
        assert call_args[1]["subscription_info"]["keys"]["auth"] == "auth_secret_base64"

    def test_notify_completion_passes_correct_vapid_claims(self):
        """webpush_fn に正しい vapid_claims が渡されること"""
        sub = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/abc",
            p256dh="key",
            auth="auth",
            created_at=NOW,
        )

        mock_db = MagicMock()
        mock_db.get_push_subscriptions.return_value = [sub]
        mock_webpush = MagicMock()

        vapid = VapidConfig(
            private_key="priv",
            public_key="pub",
            claims_email="admin@example.com",
        )
        notifier = WebPushNotifier(db=mock_db, vapid=vapid, webpush_fn=mock_webpush)

        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
        )

        call_args = mock_webpush.call_args
        assert call_args[1]["vapid_claims"]["sub"] == "mailto:admin@example.com"

    def test_notify_completion_exception_with_410_deletes_subscription(self):
        """webpush_fn が 410 Gone を返す例外を出せば、購読を削除する"""
        sub = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/expired",
            p256dh="key",
            auth="auth",
            created_at=NOW,
        )

        mock_db = MagicMock()
        mock_db.get_push_subscriptions.return_value = [sub]

        # 410 status_code を持つ例外を構築
        mock_response = MagicMock()
        mock_response.status_code = 410
        exc = Exception("Gone")
        exc.response = mock_response

        mock_webpush = MagicMock(side_effect=exc)

        vapid = VapidConfig(
            private_key="priv",
            public_key="pub",
            claims_email="email@example.com",
        )
        notifier = WebPushNotifier(db=mock_db, vapid=vapid, webpush_fn=mock_webpush)

        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
        )

        mock_db.delete_push_subscription.assert_called_once_with("user1", "https://push.example.com/expired")

    def test_notify_completion_exception_with_404_deletes_subscription(self):
        """webpush_fn が 404 Not Found を返す例外を出せば、購読を削除する"""
        sub = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/notfound",
            p256dh="key",
            auth="auth",
            created_at=NOW,
        )

        mock_db = MagicMock()
        mock_db.get_push_subscriptions.return_value = [sub]

        # 404 status_code を持つ例外を構築
        mock_response = MagicMock()
        mock_response.status_code = 404
        exc = Exception("Not Found")
        exc.response = mock_response

        mock_webpush = MagicMock(side_effect=exc)

        vapid = VapidConfig(
            private_key="priv",
            public_key="pub",
            claims_email="email@example.com",
        )
        notifier = WebPushNotifier(db=mock_db, vapid=vapid, webpush_fn=mock_webpush)

        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
        )

        mock_db.delete_push_subscription.assert_called_once_with("user1", "https://push.example.com/notfound")

    def test_notify_completion_exception_generic_logs_warning_and_continues(self):
        """webpush_fn が generic exception を出せば warning ログを出し、他の購読を処理する"""
        sub1 = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/1",
            p256dh="key",
            auth="auth",
            created_at=NOW,
        )
        sub2 = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/2",
            p256dh="key",
            auth="auth",
            created_at=NOW,
        )

        mock_db = MagicMock()
        mock_db.get_push_subscriptions.return_value = [sub1, sub2]

        # 最初の呼び出しで例外、2 回目は成功
        mock_webpush = MagicMock(side_effect=[Exception("Network error"), None])

        vapid = VapidConfig(
            private_key="priv",
            public_key="pub",
            claims_email="email@example.com",
        )
        notifier = WebPushNotifier(db=mock_db, vapid=vapid, webpush_fn=mock_webpush)

        # 例外を出さずに完了する
        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
        )

        # 購読削除は呼ばれない（generic exception は削除しない）
        mock_db.delete_push_subscription.assert_not_called()
        # webpush_fn は 2 回呼ばれている（continue したため）
        assert mock_webpush.call_count == 2

    def test_notify_completion_with_data_payload(self):
        """data フィールドが含まれたペイロードが正しく構築されること"""
        sub = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/abc",
            p256dh="key",
            auth="auth",
            created_at=NOW,
        )

        mock_db = MagicMock()
        mock_db.get_push_subscriptions.return_value = [sub]
        mock_webpush = MagicMock()

        vapid = VapidConfig(
            private_key="priv",
            public_key="pub",
            claims_email="email@example.com",
        )
        notifier = WebPushNotifier(db=mock_db, vapid=vapid, webpush_fn=mock_webpush)

        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
            data={"podcast_id": "pod1", "article_id": "art1"},
        )

        call_args = mock_webpush.call_args
        import json
        payload = json.loads(call_args[1]["data"])
        assert payload["title"] == "Title"
        assert payload["body"] == "Body"
        assert payload["podcast_id"] == "pod1"
        assert payload["article_id"] == "art1"


# ---------- NoOpNotifier ----------


class TestNoOpNotifier:
    def test_notify_completion_is_noop(self):
        """NoOpNotifier.notify_completion は何もしない"""
        notifier = NoOpNotifier()
        # 例外を出さずに正常に完了する
        notifier.notify_completion(
            "user1",
            title="Title",
            body="Body",
            data={"key": "value"},
        )


# ---------- build_notifier ----------


class TestBuildNotifier:
    def test_build_notifier_with_vapid_returns_web_push_notifier(self):
        """VAPID 環境変数が設定されていれば WebPushNotifier を返す"""
        mock_db = MagicMock()
        env = {
            "VAPID_PRIVATE_KEY": "priv",
            "VAPID_PUBLIC_KEY": "pub",
            "VAPID_CLAIMS_EMAIL": "email@example.com",
        }
        notifier = build_notifier(mock_db, env)
        assert isinstance(notifier, WebPushNotifier)

    def test_build_notifier_without_vapid_returns_noop_notifier(self):
        """VAPID 環境変数が未設定なら NoOpNotifier を返す"""
        mock_db = MagicMock()
        env = {}
        notifier = build_notifier(mock_db, env)
        assert isinstance(notifier, NoOpNotifier)

    def test_build_notifier_with_incomplete_vapid_returns_noop_notifier(self):
        """VAPID 環境変数が不完全なら NoOpNotifier を返す"""
        mock_db = MagicMock()
        env = {
            "VAPID_PRIVATE_KEY": "priv",
            # 他が不足している
        }
        notifier = build_notifier(mock_db, env)
        assert isinstance(notifier, NoOpNotifier)

    def test_build_notifier_with_apns_only_returns_apns_notifier(self):
        """APNS 環境変数のみ設定なら ApnsNotifier を単独で返す"""
        mock_db = MagicMock()
        env = {
            "APNS_PRIVATE_KEY": "dummy-p8-key-material",
            "APNS_KEY_ID": "KEY123",
            "APNS_TEAM_ID": "TEAM123",
            "APNS_BUNDLE_ID": "com.example.app",
        }
        notifier = build_notifier(mock_db, env)
        assert isinstance(notifier, ApnsNotifier)

    def test_build_notifier_with_both_returns_composite(self):
        """VAPID と APNS の両方が設定なら CompositeNotifier を返す"""
        mock_db = MagicMock()
        env = {
            "VAPID_PRIVATE_KEY": "priv",
            "VAPID_PUBLIC_KEY": "pub",
            "VAPID_CLAIMS_EMAIL": "email@example.com",
            "APNS_PRIVATE_KEY": "dummy-p8-key-material",
            "APNS_KEY_ID": "KEY123",
            "APNS_TEAM_ID": "TEAM123",
            "APNS_BUNDLE_ID": "com.example.app",
        }
        notifier = build_notifier(mock_db, env)
        assert isinstance(notifier, CompositeNotifier)
        # ラッパー型だけでなく、両プロバイダが実際に束ねられていることを検証する
        # （片方が build_notifier から脱落しても isinstance は通ってしまうため）。
        assert len(notifier._notifiers) == 2
        assert any(isinstance(n, WebPushNotifier) for n in notifier._notifiers)
        assert any(isinstance(n, ApnsNotifier) for n in notifier._notifiers)


# ---------- ApnsConfig ----------


class TestApnsConfig:
    def test_from_env_with_all_vars_returns_config(self):
        """APNS_* が全て設定されていれば ApnsConfig を返す"""
        env = {
            "APNS_PRIVATE_KEY": "p8_contents",
            "APNS_KEY_ID": "KEY123",
            "APNS_TEAM_ID": "TEAM123",
            "APNS_BUNDLE_ID": "com.example.app",
        }
        config = ApnsConfig.from_env(env)
        assert config is not None
        assert config.private_key == "p8_contents"
        assert config.key_id == "KEY123"
        assert config.team_id == "TEAM123"
        assert config.bundle_id == "com.example.app"
        # APNS_USE_SANDBOX 未指定なら本番扱い
        assert config.use_sandbox is False

    def test_from_env_with_sandbox_flag(self):
        """APNS_USE_SANDBOX=true なら sandbox 扱い"""
        env = {
            "APNS_PRIVATE_KEY": "p8",
            "APNS_KEY_ID": "K",
            "APNS_TEAM_ID": "T",
            "APNS_BUNDLE_ID": "b",
            "APNS_USE_SANDBOX": "true",
        }
        config = ApnsConfig.from_env(env)
        assert config is not None
        assert config.use_sandbox is True

    def test_from_env_with_missing_vars_returns_none(self):
        """APNS_* が不足していれば None を返す"""
        env = {
            "APNS_PRIVATE_KEY": "p8",
            "APNS_KEY_ID": "K",
            # TEAM / BUNDLE 欠落
        }
        assert ApnsConfig.from_env(env) is None

    def test_from_env_empty_returns_none(self):
        assert ApnsConfig.from_env({}) is None


# ---------- ApnsNotifier ----------


def _apns_config(use_sandbox: bool = False) -> ApnsConfig:
    return ApnsConfig(
        private_key="p8_contents",
        key_id="KEY123",
        team_id="TEAM123",
        bundle_id="com.example.app",
        use_sandbox=use_sandbox,
    )


def _apns_token(token: str = "devicetoken123", user_id: str = "user1") -> ApnsDeviceToken:
    return ApnsDeviceToken(
        user_id=user_id,
        device_token=token,
        created_at=NOW,
    )


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class TestApnsNotifier:
    def test_no_op_when_no_tokens(self):
        """デバイストークンが無ければ HTTP 送信しない"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = []
        http = MagicMock()
        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(),
            http_post_fn=http, token_provider=lambda: "jwt",
        )

        notifier.notify_completion("user1", title="T", body="B")

        mock_db.get_apns_device_tokens.assert_called_once_with("user1")
        http.assert_not_called()

    def test_sends_once_per_token(self):
        """トークン数分だけ送信する"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = [_apns_token("t1"), _apns_token("t2")]
        http = MagicMock(return_value=_FakeResponse(200))
        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(),
            http_post_fn=http, token_provider=lambda: "jwt",
        )

        notifier.notify_completion("user1", title="T", body="B")

        assert http.call_count == 2

    def test_request_has_correct_url_headers_and_payload(self):
        """URL・authorization・apns-topic・aps ペイロードが正しい"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = [_apns_token("devtok")]
        captured = {}

        def fake_post(url, headers, payload):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            return _FakeResponse(200)

        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(),
            http_post_fn=fake_post, token_provider=lambda: "the-jwt",
        )

        notifier.notify_completion(
            "user1", title="完了", body="生成しました",
            data={"podcast_id": "pod1"},
        )

        assert captured["url"].endswith("/3/device/devtok")
        assert captured["url"].startswith("https://api.push.apple.com")
        assert captured["headers"]["authorization"] == "bearer the-jwt"
        assert captured["headers"]["apns-topic"] == "com.example.app"
        # alert 通知であることを示すヘッダ。回帰すると配信が壊れるため明示検証する。
        assert captured["headers"]["apns-push-type"] == "alert"
        import json
        body = json.loads(captured["payload"])
        assert body["aps"]["alert"]["title"] == "完了"
        assert body["aps"]["alert"]["body"] == "生成しました"
        assert body["podcast_id"] == "pod1"

    def test_uses_sandbox_host_when_configured(self):
        """sandbox 設定時は sandbox ホストへ送る"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = [_apns_token("devtok")]
        captured = {}

        def fake_post(url, headers, payload):
            captured["url"] = url
            return _FakeResponse(200)

        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(use_sandbox=True),
            http_post_fn=fake_post, token_provider=lambda: "jwt",
        )

        notifier.notify_completion("user1", title="T", body="B")

        assert captured["url"].startswith("https://api.sandbox.push.apple.com")

    def test_410_deletes_token(self):
        """410 応答なら失効トークンを削除する"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = [_apns_token("expired")]
        http = MagicMock(return_value=_FakeResponse(410, {"reason": "Unregistered"}))
        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(),
            http_post_fn=http, token_provider=lambda: "jwt",
        )

        notifier.notify_completion("user1", title="T", body="B")

        mock_db.delete_apns_device_token.assert_called_once_with("user1", "expired")

    def test_bad_device_token_deletes_token(self):
        """400 BadDeviceToken なら失効トークンを削除する"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = [_apns_token("bad")]
        http = MagicMock(return_value=_FakeResponse(400, {"reason": "BadDeviceToken"}))
        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(),
            http_post_fn=http, token_provider=lambda: "jwt",
        )

        notifier.notify_completion("user1", title="T", body="B")

        mock_db.delete_apns_device_token.assert_called_once_with("user1", "bad")

    def test_other_error_does_not_delete_token(self):
        """その他のエラー(例: 503)は削除せず継続する"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = [_apns_token("t1"), _apns_token("t2")]
        http = MagicMock(return_value=_FakeResponse(503, {"reason": "ServiceUnavailable"}))
        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(),
            http_post_fn=http, token_provider=lambda: "jwt",
        )

        notifier.notify_completion("user1", title="T", body="B")

        mock_db.delete_apns_device_token.assert_not_called()
        assert http.call_count == 2

    def test_exception_is_non_fatal_and_continues(self):
        """送信例外は握り潰し、他トークンの処理を続ける"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = [_apns_token("t1"), _apns_token("t2")]
        http = MagicMock(side_effect=[Exception("network"), _FakeResponse(200)])
        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(),
            http_post_fn=http, token_provider=lambda: "jwt",
        )

        # 例外を出さずに完了する
        notifier.notify_completion("user1", title="T", body="B")

        assert http.call_count == 2

    def test_none_status_is_treated_as_failure_not_success(self):
        """status_code が取得できない応答は成功扱いせず削除もしない（可視化）。"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = [_apns_token("t1")]

        class _NoStatus:
            def json(self):
                return {}

        http = MagicMock(return_value=_NoStatus())
        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(),
            http_post_fn=http, token_provider=lambda: "jwt",
        )

        notifier.notify_completion("user1", title="T", body="B")

        mock_db.delete_apns_device_token.assert_not_called()

    def test_auth_token_failure_is_non_fatal(self):
        """JWT 署名失敗（不正な .p8 等）は例外を投げず no-op で終わる（非致命契約）。"""
        mock_db = MagicMock()
        mock_db.get_apns_device_tokens.return_value = [_apns_token("t1")]
        http = MagicMock()

        def boom():
            raise ValueError("invalid key")

        notifier = ApnsNotifier(
            db=mock_db, config=_apns_config(),
            http_post_fn=http, token_provider=boom,
        )

        # 例外を出さずに完了し、送信もしない。
        notifier.notify_completion("user1", title="T", body="B")
        http.assert_not_called()


# ---------- ApnsNotifier 本番 JWT 署名・キャッシュ経路（注入なし） ----------


def _ec_p256_private_key_pem() -> str:
    """テスト用に本物の P-256 秘密鍵を PEM(.p8 相当) で生成する。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class TestApnsAuthTokenSigning:
    def test_real_es256_token_has_expected_header_and_claims(self):
        """token_provider 無しの本番経路で ES256/kid/iss/iat を持つ JWT を生成する。"""
        import jwt

        config = ApnsConfig(
            private_key=_ec_p256_private_key_pem(),
            key_id="KEY123",
            team_id="TEAM123",
            bundle_id="com.example.app",
            use_sandbox=False,
        )
        notifier = ApnsNotifier(db=MagicMock(), config=config, now_fn=lambda: NOW)

        token = notifier._auth_token()

        header = jwt.get_unverified_header(token)
        assert header["alg"] == "ES256"
        assert header["kid"] == "KEY123"
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["iss"] == "TEAM123"
        assert claims["iat"] == int(NOW.timestamp())

    def test_token_is_cached_within_ttl(self):
        """TTL 内の再取得は同一トークンを返す（再署名しない）。"""
        config = ApnsConfig(
            private_key=_ec_p256_private_key_pem(),
            key_id="K", team_id="T", bundle_id="b", use_sandbox=False,
        )
        notifier = ApnsNotifier(db=MagicMock(), config=config, now_fn=lambda: NOW)

        first = notifier._auth_token()
        second = notifier._auth_token()
        assert first == second

    def test_token_is_regenerated_after_ttl(self):
        """TTL を超えると新しいトークンを生成する。"""
        from datetime import timedelta

        config = ApnsConfig(
            private_key=_ec_p256_private_key_pem(),
            key_id="K", team_id="T", bundle_id="b", use_sandbox=False,
        )
        times = [NOW, NOW + timedelta(hours=2)]
        notifier = ApnsNotifier(db=MagicMock(), config=config, now_fn=lambda: times.pop(0))

        first = notifier._auth_token()
        second = notifier._auth_token()
        assert first != second


# ---------- CompositeNotifier ----------


class TestCompositeNotifier:
    def test_calls_all_notifiers(self):
        """全ての notifier を呼ぶ"""
        n1 = MagicMock()
        n2 = MagicMock()
        composite = CompositeNotifier([n1, n2])

        composite.notify_completion("user1", title="T", body="B", data={"k": "v"})

        n1.notify_completion.assert_called_once_with("user1", title="T", body="B", data={"k": "v"})
        n2.notify_completion.assert_called_once_with("user1", title="T", body="B", data={"k": "v"})

    def test_one_failure_does_not_stop_others(self):
        """1つが例外でも他の notifier は呼ばれる"""
        n1 = MagicMock()
        n1.notify_completion.side_effect = Exception("boom")
        n2 = MagicMock()
        composite = CompositeNotifier([n1, n2])

        # 例外を出さずに完了する
        composite.notify_completion("user1", title="T", body="B")

        n2.notify_completion.assert_called_once()
