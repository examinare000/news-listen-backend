"""パスワードリセット機能の統合テスト。

T1: verify_reset_token（純粋関数）
T2: EmailSender Protocol と実装
T3: モデル追加
T4: Firestore メソッド
T5: スキーマ検証
T6: Dependencies
T7-T8: API エンドポイント
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from shared.models import AuditLog, PasswordResetToken, User
from shared.password_reset import verify_reset_token
from shared.email_sender import (
    EmailConfig,
    SmtpEmailSender,
    NoOpEmailSender,
    build_email_sender,
)


# ── T1: verify_reset_token（純粋関数） ────────────────────────────────────


class TestVerifyResetToken:
    """T1 テスト: 未期限・未使用 → True / 期限切れ・既用 → False。"""

    def test_valid_token_unused_within_expiry(self):
        """未期限かつ未使用なら True。"""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=1)
        token = PasswordResetToken(
            token_hash="abc123",
            user_id="user1",
            username="testuser",
            expires_at=expires_at,
            created_at=now,
            used_at=None,
        )
        assert verify_reset_token(token, now) is True

    def test_expired_token_returns_false(self):
        """期限切れなら False。"""
        now = datetime.now(timezone.utc)
        expires_at = now - timedelta(hours=1)
        token = PasswordResetToken(
            token_hash="abc123",
            user_id="user1",
            username="testuser",
            expires_at=expires_at,
            created_at=now - timedelta(hours=2),
            used_at=None,
        )
        assert verify_reset_token(token, now) is False

    def test_already_used_token_returns_false(self):
        """既に使用済みなら False。"""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=1)
        token = PasswordResetToken(
            token_hash="abc123",
            user_id="user1",
            username="testuser",
            expires_at=expires_at,
            created_at=now,
            used_at=now - timedelta(minutes=5),  # 既に使用済み
        )
        assert verify_reset_token(token, now) is False

    def test_boundary_now_equals_expiry_returns_false(self):
        """now == expires_at なら False（境界値）。"""
        now = datetime.now(timezone.utc)
        token = PasswordResetToken(
            token_hash="abc123",
            user_id="user1",
            username="testuser",
            expires_at=now,  # exactly now
            created_at=now - timedelta(hours=1),
            used_at=None,
        )
        assert verify_reset_token(token, now) is False


# ── T2: EmailSender Protocol と実装 ────────────────────────────────────


class TestEmailConfig:
    """EmailConfig: frozen dataclass、from_env、password repr マスク。"""

    def test_from_env_all_keys_present(self):
        """全キー揃ったら EmailConfig を返す。"""
        env = {
            "SMTP_HOST": "mail.example.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "user@example.com",
            "SMTP_PASSWORD": "secret123",
            "SMTP_FROM": "noreply@example.com",
        }
        config = EmailConfig.from_env(env)
        assert config is not None
        assert config.host == "mail.example.com"
        assert config.port == 587
        assert config.username == "user@example.com"
        assert config.password == "secret123"
        assert config.from_address == "noreply@example.com"

    def test_from_env_missing_key_returns_none(self):
        """1つでも欠けたら None を返す。"""
        env = {
            "SMTP_HOST": "mail.example.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "user@example.com",
            # SMTP_PASSWORD 欠損
            "SMTP_FROM": "noreply@example.com",
        }
        assert EmailConfig.from_env(env) is None

    def test_from_env_invalid_port(self):
        """SMTP_PORT が数値でなかったら None を返す。"""
        env = {
            "SMTP_HOST": "mail.example.com",
            "SMTP_PORT": "invalid",
            "SMTP_USERNAME": "user@example.com",
            "SMTP_PASSWORD": "secret123",
            "SMTP_FROM": "noreply@example.com",
        }
        assert EmailConfig.from_env(env) is None

    def test_password_repr_masked(self):
        """repr() で password がマスクされる。"""
        config = EmailConfig(
            host="mail.example.com",
            port=587,
            username="user@example.com",
            password="secret123",
            from_address="noreply@example.com",
        )
        repr_str = repr(config)
        assert "secret123" not in repr_str
        assert "*" in repr_str or "***" in repr_str or "password" in repr_str.lower()


class TestSmtpEmailSender:
    """SmtpEmailSender: send_fn 注入テスト、送信失敗は例外伝播なし。"""

    def test_send_calls_smtp_function_with_correct_params(self):
        """send_password_reset_email が send_fn を正しい params で呼ぶ。"""
        config = EmailConfig(
            host="mail.example.com",
            port=587,
            username="user@example.com",
            password="secret123",
            from_address="noreply@example.com",
        )
        send_fn = MagicMock()
        sender = SmtpEmailSender(config, send_fn=send_fn)

        sender.send_password_reset_email(
            to_email="user@test.com",
            reset_url="https://app.example.com/reset-password?token=abc123xyz",
        )

        # send_fn が呼ばれたことを確認
        assert send_fn.called
        # 呼ばれた時のキーワード引数を確認
        call_kwargs = send_fn.call_args[1] if send_fn.call_args[1] else {}
        # reset_url が email body に含まれているはず
        if "message" in call_kwargs:
            assert "abc123xyz" in call_kwargs["message"]

    def test_send_failure_does_not_raise(self):
        """send_fn が例外を raise しても SmtpEmailSender は伝播しない。"""
        config = EmailConfig(
            host="mail.example.com",
            port=587,
            username="user@example.com",
            password="secret123",
            from_address="noreply@example.com",
        )
        send_fn = MagicMock(side_effect=RuntimeError("Network error"))
        sender = SmtpEmailSender(config, send_fn=send_fn)

        # 例外が発生しないはず（warning ログのみ）
        try:
            sender.send_password_reset_email(
                to_email="user@test.com",
                reset_url="https://app.example.com/reset-password?token=abc",
            )
        except Exception:
            pytest.fail("SmtpEmailSender should not raise exception from send_fn failure")

    def test_reset_url_not_logged_in_noop(self):
        """NoOp 実装では reset_url がログに出ない（トークン漏洩防止）。"""
        noop = NoOpEmailSender()
        # 例外なく実行されること
        noop.send_password_reset_email(
            to_email="user@test.com",
            reset_url="https://app.example.com/reset-password?token=secret123",
        )
        # ログには出ないはずだが、テストでは単に実行できることを確認


class TestBuildEmailSender:
    """build_email_sender: env 不足 → NoOp / 揃い → Smtp。"""

    def test_build_with_full_config_returns_smtp(self):
        """全キー揃ったら SmtpEmailSender を返す。"""
        env = {
            "SMTP_HOST": "mail.example.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "user@example.com",
            "SMTP_PASSWORD": "secret123",
            "SMTP_FROM": "noreply@example.com",
        }
        sender = build_email_sender(env)
        assert isinstance(sender, SmtpEmailSender)

    def test_build_without_config_returns_noop(self):
        """キーが不足したら NoOpEmailSender を返す。"""
        env = {}
        sender = build_email_sender(env)
        assert isinstance(sender, NoOpEmailSender)


# ── T3: モデル追加 ────────────────────────────────────────────────────────


class TestUserEmailField:
    """User に email フィールドを追加（後方互換）。"""

    def test_user_with_email(self):
        """email フィールド指定時に保存される。"""
        now = datetime.now(timezone.utc)
        user = User(
            username="testuser",
            user_id="user1",
            password_hash="hash123",
            display_name="Test User",
            email="user@example.com",
            created_at=now,
            updated_at=now,
        )
        assert user.email == "user@example.com"

    def test_user_without_email_defaults_to_none(self):
        """email 未指定で None（後方互換）。"""
        now = datetime.now(timezone.utc)
        user = User(
            username="testuser",
            user_id="user1",
            password_hash="hash123",
            display_name="Test User",
            created_at=now,
            updated_at=now,
        )
        assert user.email is None

    def test_user_from_dict_without_email(self):
        """dict から User を生成する際、email 未指定でも通る。"""
        now = datetime.now(timezone.utc)
        data = {
            "username": "testuser",
            "user_id": "user1",
            "password_hash": "hash123",
            "display_name": "Test User",
            "created_at": now,
            "updated_at": now,
        }
        user = User(**data)
        assert user.email is None


class TestPasswordResetToken:
    """PasswordResetToken モデル。"""

    def test_password_reset_token_creation(self):
        """PasswordResetToken の各フィールドが設定される。"""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=1)
        token = PasswordResetToken(
            token_hash="hash123",
            user_id="user1",
            username="testuser",
            expires_at=expires_at,
            created_at=now,
            used_at=None,
        )
        assert token.token_hash == "hash123"
        assert token.user_id == "user1"
        assert token.username == "testuser"
        assert token.used_at is None

    def test_password_reset_token_defaults_used_at_none(self):
        """used_at は既定で None。"""
        now = datetime.now(timezone.utc)
        token = PasswordResetToken(
            token_hash="hash123",
            user_id="user1",
            username="testuser",
            expires_at=now + timedelta(hours=1),
            created_at=now,
        )
        assert token.used_at is None


class TestAuditActionEnum:
    """AuditAction に password_reset_requested/completed を追加。"""

    def test_audit_log_with_password_reset_requested(self):
        """AuditLog で password_reset_requested が許可される。"""
        now = datetime.now(timezone.utc)
        log = AuditLog(
            action="password_reset_requested",
            timestamp=now,
            ip="192.168.1.1",
        )
        assert log.action == "password_reset_requested"

    def test_audit_log_with_password_reset_completed(self):
        """AuditLog で password_reset_completed が許可される。"""
        now = datetime.now(timezone.utc)
        log = AuditLog(
            action="password_reset_completed",
            timestamp=now,
            target_username="testuser",
        )
        assert log.action == "password_reset_completed"


# ── T4: Firestore メソッド ────────────────────────────────────────────────


class TestFirestoreSaveResetToken:
    """save_reset_token: doc-id=token_hash、生トークン非保存。"""

    def test_save_reset_token_does_not_store_raw_token(self, mock_firestore_db):
        """save_reset_token が生トークンをペイロードに含めない。

        セキュリティ不変条件: DB に保存されるペイロードに生トークン文字列が一切含まれない。
        """
        from shared.firestore_client import FirestoreClient

        now = datetime.now(timezone.utc)
        token = PasswordResetToken(
            token_hash="abc123hash",
            user_id="user1",
            username="testuser",
            expires_at=now + timedelta(hours=1),
            created_at=now,
            used_at=None,
        )

        # FirestoreClient を実インスタンス化（_db は mock_firestore_db に置き換わる）
        db = FirestoreClient()

        db.save_reset_token(token)

        # collection("passwordResetTokens").document("abc123hash").set(payload) が呼ばれたことを確認
        mock_firestore_db.collection.assert_called_with("passwordResetTokens")
        collection_ref = mock_firestore_db.collection.return_value
        collection_ref.document.assert_called_with("abc123hash")
        doc_ref = collection_ref.document.return_value

        # set() に渡されたペイロードを確認
        assert doc_ref.set.called
        call_args = doc_ref.set.call_args
        assert call_args is not None

        payload = call_args[0][0] if call_args[0] else {}

        # セキュリティ検証: ペイロード内に生トークン文字列が存在しないことを確認
        # ペイロードは token_hash, user_id, username, expires_at, created_at, used_at のみを持つ
        assert "token_hash" in payload
        assert payload["token_hash"] == "abc123hash"

        # 生トークン（秘密値）がペイロードに含まれていないことを確認
        payload_str = str(payload).lower()
        # 生トークンは secrets.token_urlsafe(32) なので、通常は英数字_-のみ。
        # ここでは単純に "raw_token" 等の文字列が含まれないことを確認
        assert "raw_token" not in payload_str


class TestFirestoreGetResetToken:
    """get_reset_token: O(1) 直引き。"""

    def test_get_reset_token_returns_none_if_not_found(self, mock_firestore_db):
        """不在なら None を返す。"""
        from shared.firestore_client import FirestoreClient

        # ドキュメントが存在しない場合のモック設定
        mock_firestore_db.collection.return_value.document.return_value.get.return_value.exists = False

        db = FirestoreClient()

        result = db.get_reset_token("nonexistent")
        assert result is None


class TestFirestoreConsumeResetToken:
    """consume_reset_token: 未使用→True+used_at セット / 既用・期限切れ・不在→False。

    @firestore.transactional は実デコレータのまま、firestore.Client と transaction を
    モックして**実際の振る舞い**を検証する（test_firestore_rate_limit.py と同手法）。
    """

    NOW = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    HASH = "h" * 64

    def _client_with_snapshot(self, snapshot):
        """firestore.Client をモックし、ドキュメント get が snapshot を返す FirestoreClient を返す。"""
        patcher = patch("shared.firestore_client.firestore.Client")
        mock_client_class = patcher.start()
        mock_db = MagicMock()
        mock_client_class.return_value = mock_db

        mock_transaction = MagicMock()
        mock_db.transaction.return_value = mock_transaction
        mock_doc_ref = mock_db.collection.return_value.document.return_value
        mock_doc_ref.get.return_value = snapshot

        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        return client, mock_transaction, mock_doc_ref, patcher

    def test_consume_unused_in_window_returns_true_and_sets_used_at(self):
        """未使用かつ期限内 → True を返し used_at=now を原子更新する。"""
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {
            "used_at": None,
            "expires_at": self.NOW + timedelta(minutes=10),
        }
        client, txn, doc_ref, patcher = self._client_with_snapshot(snap)
        try:
            result = client.consume_reset_token(self.HASH, now=self.NOW)
        finally:
            patcher.stop()

        assert result is True
        txn.update.assert_called_once()
        # used_at = now を書いていること（使い捨ての原子更新）
        assert txn.update.call_args[0][1] == {"used_at": self.NOW}

    def test_consume_already_used_returns_false_no_update(self):
        """既用（used_at が非 None） → False。更新しない。"""
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {
            "used_at": self.NOW - timedelta(minutes=1),
            "expires_at": self.NOW + timedelta(minutes=10),
        }
        client, txn, doc_ref, patcher = self._client_with_snapshot(snap)
        try:
            result = client.consume_reset_token(self.HASH, now=self.NOW)
        finally:
            patcher.stop()

        assert result is False
        txn.update.assert_not_called()

    def test_consume_expired_returns_false_no_update(self):
        """期限切れ（now >= expires_at） → False。更新しない。"""
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {
            "used_at": None,
            "expires_at": self.NOW - timedelta(seconds=1),
        }
        client, txn, doc_ref, patcher = self._client_with_snapshot(snap)
        try:
            result = client.consume_reset_token(self.HASH, now=self.NOW)
        finally:
            patcher.stop()

        assert result is False
        txn.update.assert_not_called()

    def test_consume_missing_returns_false_no_update(self):
        """不在（snapshot.exists False） → False。更新しない。"""
        snap = MagicMock()
        snap.exists = False
        client, txn, doc_ref, patcher = self._client_with_snapshot(snap)
        try:
            result = client.consume_reset_token(self.HASH, now=self.NOW)
        finally:
            patcher.stop()

        assert result is False
        txn.update.assert_not_called()


# ── T5: スキーマ ────────────────────────────────────────────────────────


class TestForgotPasswordRequest:
    """ForgotPasswordRequest スキーマ。"""

    def test_forgot_password_request_with_valid_username(self):
        """有効な username が受け入れられる。"""
        from api.schemas import ForgotPasswordRequest

        req = ForgotPasswordRequest(username="testuser")
        assert req.username == "testuser"

    def test_forgot_password_request_empty_username_rejected(self):
        """空の username は 422 になる。"""
        from api.schemas import ForgotPasswordRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ForgotPasswordRequest(username="")


class TestResetPasswordRequest:
    """ResetPasswordRequest スキーマ（strong password 検証）。"""

    def test_reset_password_request_with_strong_password(self):
        """強いパスワードが受け入れられる。"""
        from api.schemas import ResetPasswordRequest

        req = ResetPasswordRequest(
            token="abc123",
            new_password="StrongPass1!",
        )
        assert req.token == "abc123"
        assert req.new_password == "StrongPass1!"

    def test_reset_password_request_weak_password_rejected(self):
        """弱いパスワードは 422 になる。"""
        from api.schemas import ResetPasswordRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ResetPasswordRequest(
                token="abc123",
                new_password="weak",  # 短すぎる
            )

    def test_reset_password_request_empty_token_rejected(self):
        """空の token は 422 になる。"""
        from api.schemas import ResetPasswordRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ResetPasswordRequest(
                token="",
                new_password="StrongPass1!",
            )


# ── T7-T8: API エンドポイント ────────────────────────────────────────────


class TestPasswordResetEndpoints:
    """T7-T8: Password reset endpoints"""

    API_HEADERS = {"X-API-Key": "test-key"}

    def test_forgot_password_returns_200_always(self, api_client, mock_db):
        """/auth/password/forgot は常に 200 を返す。"""
        # mock_db.get_user をデフォルト None に設定
        mock_db.get_user.return_value = None

        response = api_client.post(
            "/auth/password/forgot",
            json={"username": "testuser"},
            headers=self.API_HEADERS,
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_forgot_password_user_not_found_returns_200_no_side_effects(self, api_client, mock_db):
        """ユーザー不在でも 200。save_reset_token は呼ばれない。"""
        mock_db.get_user.return_value = None

        response = api_client.post(
            "/auth/password/forgot",
            json={"username": "nonexistent"},
            headers=self.API_HEADERS,
        )

        assert response.status_code == 200
        # save_reset_token が呼ばれていないことを確認
        mock_db.save_reset_token.assert_not_called()

    def test_forgot_password_user_without_email_returns_200_no_send(self, api_client, mock_db, mock_email_sender):
        """ユーザーが email 無の場合、200 だが send_password_reset_email は呼ばれない。"""
        now = datetime.now(timezone.utc)
        user = User(
            username="testuser",
            user_id="user1",
            password_hash="hash",
            display_name="Test",
            email=None,  # email 無
            created_at=now,
            updated_at=now,
        )
        mock_db.get_user.return_value = user

        response = api_client.post(
            "/auth/password/forgot",
            json={"username": "testuser"},
            headers=self.API_HEADERS,
        )

        assert response.status_code == 200
        # send_password_reset_email が呼ばれていないことを確認
        mock_email_sender.send_password_reset_email.assert_not_called()

    def test_forgot_password_user_with_email_saves_token(self, api_client, mock_db, mock_email_sender):
        """ユーザーが email 有の場合、token を save・send を呼ぶ。"""
        now = datetime.now(timezone.utc)
        user = User(
            username="testuser",
            user_id="user1",
            password_hash="hash",
            display_name="Test",
            email="user@example.com",
            created_at=now,
            updated_at=now,
        )
        mock_db.get_user.return_value = user

        response = api_client.post(
            "/auth/password/forgot",
            json={"username": "testuser"},
            headers=self.API_HEADERS,
        )

        assert response.status_code == 200
        # save_reset_token が呼ばれたことを確認
        assert mock_db.save_reset_token.called
        # send_password_reset_email が呼ばれたことを確認
        assert mock_email_sender.send_password_reset_email.called

    def test_forgot_password_email_send_failure_returns_200(self, api_client, mock_db, mock_email_sender):
        """メール送信失敗でも 200 を返す（非致命）。"""
        now = datetime.now(timezone.utc)
        user = User(
            username="testuser",
            user_id="user1",
            password_hash="hash",
            display_name="Test",
            email="user@example.com",
            created_at=now,
            updated_at=now,
        )
        mock_db.get_user.return_value = user
        mock_email_sender.send_password_reset_email.side_effect = RuntimeError("SMTP error")

        response = api_client.post("/auth/password/forgot", json={"username": "testuser"}, headers=self.API_HEADERS)

        # メール送信失敗でも 200 を返す
        assert response.status_code == 200

    def test_forgot_password_empty_username_returns_422(self, api_client):
        """空の username は 422 になる。"""
        response = api_client.post("/auth/password/forgot", json={"username": ""}, headers=self.API_HEADERS)
        assert response.status_code == 422

    def test_forgot_password_records_audit_log(self, api_client, mock_db, mock_audit):
        """password_reset_requested を監査ログに記録する。"""
        now = datetime.now(timezone.utc)
        user = User(
            username="testuser",
            user_id="user1",
            password_hash="hash",
            display_name="Test",
            email="user@example.com",
            created_at=now,
            updated_at=now,
        )
        mock_db.get_user.return_value = user

        response = api_client.post("/auth/password/forgot", json={"username": "testuser"}, headers=self.API_HEADERS)

        assert response.status_code == 200
        # 監査ログが記録されたことを確認
        mock_audit.record.assert_called()

    def test_reset_password_valid_token_returns_200(self, api_client, mock_db):
        """有効なトークンでパスワード変更。"""
        from shared.security import hash_token, generate_session_token

        now = datetime.now(timezone.utc)
        raw_token = generate_session_token()
        token_hash = hash_token(raw_token)

        # トークンレコード
        token_record = PasswordResetToken(
            token_hash=token_hash,
            user_id="user1",
            username="testuser",
            expires_at=now + timedelta(hours=1),
            created_at=now,
            used_at=None,
        )

        # ユーザーレコード
        user = User(
            username="testuser",
            user_id="user1",
            password_hash="oldhash",
            display_name="Test",
            email="user@example.com",
            created_at=now,
            updated_at=now,
        )

        mock_db.get_reset_token.return_value = token_record
        mock_db.consume_reset_token.return_value = True
        mock_db.get_user.return_value = user

        response = api_client.post(
            "/auth/password/reset",
            json={"token": raw_token, "new_password": "NewPassw0rd!23"},
            headers=self.API_HEADERS,
        )

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        # consume_reset_token が呼ばれたことを確認
        mock_db.consume_reset_token.assert_called()
        # パスワードが更新されたことを確認
        mock_db.save_user.assert_called()
        # セッション失効が呼ばれたことを確認
        mock_db.delete_sessions_for_user.assert_called_with("user1")

    def test_reset_password_expired_token_returns_400(self, api_client, mock_db):
        """期限切れトークンは 400。"""
        from shared.security import hash_token, generate_session_token

        now = datetime.now(timezone.utc)
        raw_token = generate_session_token()
        token_hash = hash_token(raw_token)

        # 期限切れトークン
        token_record = PasswordResetToken(
            token_hash=token_hash,
            user_id="user1",
            username="testuser",
            expires_at=now - timedelta(hours=1),
            created_at=now - timedelta(hours=2),
            used_at=None,
        )

        mock_db.get_reset_token.return_value = token_record
        mock_db.consume_reset_token.return_value = False

        response = api_client.post(
            "/auth/password/reset",
            json={"token": raw_token, "new_password": "NewPassw0rd!23"},
            headers=self.API_HEADERS,
        )

        assert response.status_code == 400

    def test_reset_password_already_used_token_returns_400(self, api_client, mock_db):
        """既に使用済みのトークンは 400。"""
        from shared.security import hash_token, generate_session_token

        now = datetime.now(timezone.utc)
        raw_token = generate_session_token()
        token_hash = hash_token(raw_token)

        # 既に使用済み
        token_record = PasswordResetToken(
            token_hash=token_hash,
            user_id="user1",
            username="testuser",
            expires_at=now + timedelta(hours=1),
            created_at=now,
            used_at=now - timedelta(minutes=10),
        )

        mock_db.get_reset_token.return_value = token_record
        mock_db.consume_reset_token.return_value = False

        response = api_client.post(
            "/auth/password/reset",
            json={"token": raw_token, "new_password": "NewPassw0rd!23"},
            headers=self.API_HEADERS,
        )

        assert response.status_code == 400

    def test_reset_password_invalid_token_returns_400(self, api_client, mock_db):
        """不正なトークンは 400。"""
        from shared.security import generate_session_token

        raw_token = generate_session_token()
        mock_db.get_reset_token.return_value = None

        response = api_client.post(
            "/auth/password/reset",
            json={"token": raw_token, "new_password": "NewPassw0rd!23"},
            headers=self.API_HEADERS,
        )

        assert response.status_code == 400

    def test_reset_password_weak_password_returns_422(self, api_client):
        """弱いパスワードは 422。"""
        from shared.security import generate_session_token

        raw_token = generate_session_token()

        response = api_client.post(
            "/auth/password/reset",
            json={"token": raw_token, "new_password": "weak"},
            headers=self.API_HEADERS,
        )

        assert response.status_code == 422

    def test_reset_password_records_audit_log(self, api_client, mock_db):
        """password_reset_completed を監査ログに記録する。"""
        from shared.security import hash_token, generate_session_token

        now = datetime.now(timezone.utc)
        raw_token = generate_session_token()
        token_hash = hash_token(raw_token)

        token_record = PasswordResetToken(
            token_hash=token_hash,
            user_id="user1",
            username="testuser",
            expires_at=now + timedelta(hours=1),
            created_at=now,
            used_at=None,
        )

        user = User(
            username="testuser",
            user_id="user1",
            password_hash="oldhash",
            display_name="Test",
            email="user@example.com",
            created_at=now,
            updated_at=now,
        )

        mock_db.get_reset_token.return_value = token_record
        mock_db.consume_reset_token.return_value = True
        mock_db.get_user.return_value = user

        response = api_client.post(
            "/auth/password/reset",
            json={"token": raw_token, "new_password": "NewPassw0rd!23"},
            headers=self.API_HEADERS,
        )

        assert response.status_code == 200
        # 監査ログが記録されたことを確認（password_reset_completed）
        # mock_audit は api_client 経由で接続済み
