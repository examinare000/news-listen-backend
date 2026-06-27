"""FirestoreClient WebAuthn メソッドのテスト。

T3: save_credential / get_credentials_by_user / get_credential_by_id /
    update_sign_count / delete_credential / save_challenge / consume_challenge
の 7 メソッドを Red-Green-Refactor で検証する。

テストパターン:
- mock_firestore_db fixture（conftest.py）: `patch("shared.firestore_client.firestore.Client")` でモック
- consume_challenge は @firestore.transactional を使うため、
  `patch("shared.firestore_client.firestore.Client")` で firestore.Client をモックし
  transactional は実デコレータのまま動かす（test_password_reset.py と同手法）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from shared.models import WebAuthnChallenge, WebAuthnCredential
from shared.security import hash_token

# ---------------------------------------------------------------------------
# テスト用データ
# ---------------------------------------------------------------------------

NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)

CRED = WebAuthnCredential(
    credential_id="cred_abc123",
    user_id="user1",
    username="testuser",
    public_key="pubkey_xyz",
    sign_count=0,
    transports=["internal"],
    aaguid=None,
    name="My Passkey",
    created_at=NOW,
    last_used_at=None,
)

CHALLENGE = WebAuthnChallenge(
    challenge_id="chall-id-001",
    challenge="challenge_base64url",
    user_id="user1",
    type="registration",
    expires_at=NOW + timedelta(minutes=5),
    created_at=NOW,
)


# ---------------------------------------------------------------------------
# ヘルパー: firestore.Client をモックして FirestoreClient を生成
# ---------------------------------------------------------------------------

def _make_client_with_db():
    """patch("shared.firestore_client.firestore.Client") でモックした FirestoreClient を返す。

    Returns (client, mock_db, patcher) — テスト終了後に patcher.stop() を呼ぶこと。
    """
    patcher = patch("shared.firestore_client.firestore.Client")
    mock_client_class = patcher.start()
    mock_db = MagicMock()
    mock_client_class.return_value = mock_db
    from shared.firestore_client import FirestoreClient
    client = FirestoreClient()
    return client, mock_db, patcher


# ===========================================================================
# save_credential
# ===========================================================================

class TestSaveCredential:
    """save_credential が正しいパス・データで Firestore set を呼ぶ。"""

    def test_saves_to_hashed_doc_id(self, mock_firestore_db):
        """doc_id = hash_token(credential_id)、full replace set (merge なし)。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_doc_ref = MagicMock()
        mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

        client.save_credential(CRED)

        expected_doc_id = hash_token(CRED.credential_id)
        mock_firestore_db.collection.assert_called_with("credentials")
        mock_firestore_db.collection.return_value.document.assert_called_with(expected_doc_id)
        mock_doc_ref.set.assert_called_once()
        # merge=True は渡さない（full replace）
        call_args = mock_doc_ref.set.call_args
        assert call_args[1].get("merge") is not True

    def test_saves_model_dump_data(self, mock_firestore_db):
        """model_dump(mode='json') の内容を保存する。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_doc_ref = MagicMock()
        mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

        client.save_credential(CRED)

        data = mock_doc_ref.set.call_args[0][0]
        assert data["credential_id"] == CRED.credential_id
        assert data["user_id"] == CRED.user_id
        assert data["sign_count"] == CRED.sign_count


# ===========================================================================
# get_credentials_by_user
# ===========================================================================

class TestGetCredentialsByUser:
    """get_credentials_by_user が user_id でクエリし WebAuthnCredential リストを返す。"""

    def test_returns_list_of_credentials(self, mock_firestore_db):
        """マッチするドキュメントがあれば WebAuthnCredential リストを返す。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()

        doc = MagicMock()
        doc.to_dict.return_value = CRED.model_dump(mode="json")
        mock_firestore_db.collection.return_value.where.return_value.stream.return_value = [doc]

        result = client.get_credentials_by_user("user1")

        assert len(result) == 1
        assert isinstance(result[0], WebAuthnCredential)
        assert result[0].user_id == "user1"

    def test_returns_empty_list_when_no_credentials(self, mock_firestore_db):
        """マッチするドキュメントがなければ空リストを返す（例外なし）。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_firestore_db.collection.return_value.where.return_value.stream.return_value = []

        result = client.get_credentials_by_user("user_no_creds")

        assert result == []

    def test_queries_correct_collection_and_field(self, mock_firestore_db):
        """credentials コレクションを user_id == user_id でクエリする。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_firestore_db.collection.return_value.where.return_value.stream.return_value = []

        client.get_credentials_by_user("user1")

        mock_firestore_db.collection.assert_called_with("credentials")
        mock_firestore_db.collection.return_value.where.assert_called_with("user_id", "==", "user1")


# ===========================================================================
# get_credential_by_id
# ===========================================================================

class TestGetCredentialById:
    """get_credential_by_id が doc_id=hash(credential_id) で直引きする。"""

    def test_returns_none_when_not_found(self, mock_firestore_db):
        """ドキュメント不在なら None を返す。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = client.get_credential_by_id("nonexistent")

        assert result is None

    def test_returns_credential_when_found(self, mock_firestore_db):
        """ドキュメントがあれば WebAuthnCredential を返す。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = CRED.model_dump(mode="json")
        mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = client.get_credential_by_id(CRED.credential_id)

        assert isinstance(result, WebAuthnCredential)
        assert result.credential_id == CRED.credential_id

    def test_uses_hashed_doc_id(self, mock_firestore_db):
        """doc_id は hash_token(credential_id_b64url)。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc

        client.get_credential_by_id("cred_abc123")

        expected_doc_id = hash_token("cred_abc123")
        mock_firestore_db.collection.return_value.document.assert_called_with(expected_doc_id)


# ===========================================================================
# update_sign_count
# ===========================================================================

class TestUpdateSignCount:
    """update_sign_count が sign_count と last_used_at を update する。"""

    def test_calls_update_with_correct_data(self, mock_firestore_db):
        """sign_count と last_used_at.isoformat() を update に渡す。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_doc_ref = MagicMock()
        mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

        last_used = datetime(2026, 6, 26, 15, 0, 0, tzinfo=timezone.utc)
        client.update_sign_count(CRED.credential_id, new_sign_count=5, last_used_at=last_used)

        expected_doc_id = hash_token(CRED.credential_id)
        mock_firestore_db.collection.return_value.document.assert_called_with(expected_doc_id)
        mock_doc_ref.update.assert_called_once_with({
            "sign_count": 5,
            "last_used_at": last_used.isoformat(),
        })


# ===========================================================================
# delete_credential
# ===========================================================================

class TestDeleteCredential:
    """delete_credential の所有権検証・冪等性。"""

    def _setup_doc(self, mock_firestore_db, exists: bool, owner_user_id: str | None = None):
        mock_doc = MagicMock()
        mock_doc.exists = exists
        if exists:
            mock_doc.to_dict.return_value = {"user_id": owner_user_id}
        mock_firestore_db.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock_doc

    def test_deletes_when_user_id_matches(self, mock_firestore_db):
        """所有ユーザーが一致すれば削除する。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        self._setup_doc(mock_firestore_db, exists=True, owner_user_id="user1")

        client.delete_credential("user1", CRED.credential_id)

        mock_firestore_db.collection.return_value.document.return_value.delete.assert_called_once()

    def test_does_not_delete_when_user_id_mismatch(self, mock_firestore_db):
        """所有ユーザーが不一致なら削除しない。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        self._setup_doc(mock_firestore_db, exists=True, owner_user_id="other_user")

        client.delete_credential("user1", CRED.credential_id)

        mock_firestore_db.collection.return_value.document.return_value.delete.assert_not_called()

    def test_no_error_when_doc_not_found(self, mock_firestore_db):
        """ドキュメント不在でも例外を出さない（冪等）。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        self._setup_doc(mock_firestore_db, exists=False)

        # 例外が出なければ OK
        client.delete_credential("user1", CRED.credential_id)

        mock_firestore_db.collection.return_value.document.return_value.delete.assert_not_called()

    def test_uses_hashed_doc_id(self, mock_firestore_db):
        """doc_id は hash_token(credential_id_b64url)。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        self._setup_doc(mock_firestore_db, exists=False)

        client.delete_credential("user1", "cred_abc123")

        expected_doc_id = hash_token("cred_abc123")
        mock_firestore_db.collection.return_value.document.assert_called_with(expected_doc_id)


# ===========================================================================
# save_challenge
# ===========================================================================

class TestSaveChallenge:
    """save_challenge が webauthnChallenges/{challenge_id} に保存する。"""

    def test_saves_to_challenge_id_doc(self, mock_firestore_db):
        """doc_id = challenge.challenge_id で webauthnChallenges に保存。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_doc_ref = MagicMock()
        mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

        client.save_challenge(CHALLENGE)

        mock_firestore_db.collection.assert_called_with("webauthnChallenges")
        mock_firestore_db.collection.return_value.document.assert_called_with(CHALLENGE.challenge_id)
        mock_doc_ref.set.assert_called_once()

    def test_saves_model_dump_data(self, mock_firestore_db):
        """model_dump(mode='json') の内容を保存する。"""
        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        mock_doc_ref = MagicMock()
        mock_firestore_db.collection.return_value.document.return_value = mock_doc_ref

        client.save_challenge(CHALLENGE)

        data = mock_doc_ref.set.call_args[0][0]
        assert data["challenge_id"] == CHALLENGE.challenge_id
        assert data["challenge"] == CHALLENGE.challenge
        assert data["type"] == "registration"


# ===========================================================================
# consume_challenge
# ===========================================================================

class TestConsumeChallenge:
    """consume_challenge: @firestore.transactional を使ったワンタイム消費。

    consume_reset_token と同じパターンで検証:
    - `patch("shared.firestore_client.firestore.Client")` で firestore.Client をモック
    - transactional は実デコレータのまま（transaction オブジェクトをモック）
    """

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
        return client, mock_db, mock_transaction, mock_doc_ref, patcher

    def test_valid_challenge_returns_webauthn_challenge_and_deletes(self):
        """有効かつ未失効のチャレンジ → WebAuthnChallenge を返し doc を削除。

        to_dict は save_challenge の model_dump(mode="json") 保存形式（ISO 文字列）を返す。
        """
        snap = MagicMock()
        snap.exists = True
        snap.id = CHALLENGE.challenge_id
        # 本番の保存形式: model_dump(mode="json") → expires_at は ISO 文字列
        snap.to_dict.return_value = WebAuthnChallenge(
            challenge_id=CHALLENGE.challenge_id,
            challenge=CHALLENGE.challenge,
            user_id=CHALLENGE.user_id,
            type="registration",
            expires_at=NOW + timedelta(minutes=5),
            created_at=NOW,
        ).model_dump(mode="json")
        client, mock_db, txn, doc_ref, patcher = self._client_with_snapshot(snap)
        try:
            result = client.consume_challenge(CHALLENGE.challenge_id, now=NOW)
        finally:
            patcher.stop()

        assert result is not None
        assert isinstance(result, WebAuthnChallenge)
        assert result.challenge_id == CHALLENGE.challenge_id
        # doc は削除される
        txn.delete.assert_called_once()

    def test_expired_challenge_returns_none_and_deletes(self):
        """期限切れ（now >= expires_at） → None を返し doc を削除。

        to_dict は save_challenge の model_dump(mode="json") 保存形式（ISO 文字列）を返す。
        """
        snap = MagicMock()
        snap.exists = True
        snap.id = CHALLENGE.challenge_id
        # 過去の expires_at を ISO 文字列で注入（本番保存形式に合わせる）
        snap.to_dict.return_value = WebAuthnChallenge(
            challenge_id=CHALLENGE.challenge_id,
            challenge=CHALLENGE.challenge,
            user_id=CHALLENGE.user_id,
            type="registration",
            expires_at=NOW - timedelta(seconds=1),
            created_at=NOW - timedelta(minutes=10),
        ).model_dump(mode="json")
        client, mock_db, txn, doc_ref, patcher = self._client_with_snapshot(snap)
        try:
            result = client.consume_challenge(CHALLENGE.challenge_id, now=NOW)
        finally:
            patcher.stop()

        assert result is None
        txn.delete.assert_called_once()

    def test_missing_challenge_returns_none_no_delete(self):
        """ドキュメント不在 → None を返す。delete は呼ばない。"""
        snap = MagicMock()
        snap.exists = False
        client, mock_db, txn, doc_ref, patcher = self._client_with_snapshot(snap)
        try:
            result = client.consume_challenge("nonexistent", now=NOW)
        finally:
            patcher.stop()

        assert result is None
        txn.delete.assert_not_called()

    def test_second_consume_returns_none(self):
        """初回消費後（doc が消えた状態）に再呼び出し → None。"""
        # 1回目: doc あり → None / WebAuthnChallenge を返す（ISO 文字列形式）
        snap_first = MagicMock()
        snap_first.exists = True
        snap_first.id = CHALLENGE.challenge_id
        snap_first.to_dict.return_value = WebAuthnChallenge(
            challenge_id=CHALLENGE.challenge_id,
            challenge=CHALLENGE.challenge,
            user_id=CHALLENGE.user_id,
            type="registration",
            expires_at=NOW + timedelta(minutes=5),
            created_at=NOW,
        ).model_dump(mode="json")
        # 2回目: doc なし
        snap_second = MagicMock()
        snap_second.exists = False

        patcher = patch("shared.firestore_client.firestore.Client")
        mock_client_class = patcher.start()
        mock_db = MagicMock()
        mock_client_class.return_value = mock_db
        mock_transaction = MagicMock()
        mock_db.transaction.return_value = mock_transaction
        mock_doc_ref = mock_db.collection.return_value.document.return_value
        # get() を2回呼ぶとそれぞれ別の snapshot を返す
        mock_doc_ref.get.side_effect = [snap_first, snap_second]

        from shared.firestore_client import FirestoreClient
        client = FirestoreClient()
        try:
            result1 = client.consume_challenge(CHALLENGE.challenge_id, now=NOW)
            result2 = client.consume_challenge(CHALLENGE.challenge_id, now=NOW)
        finally:
            patcher.stop()

        assert result1 is not None
        assert result2 is None
