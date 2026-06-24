"""Web Push 通知送信機のテスト。

VapidConfig / WebPushNotifier / NoOpNotifier の動作と統合テストをカバー。
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from shared.models import PushSubscription
from shared.notifier import (
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
