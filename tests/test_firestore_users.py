"""FirestoreClient のユーザー／セッション CRUD のテスト。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from shared.models import Session, User

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def _user(username="alice", role="user") -> User:
    return User(
        username=username,
        user_id="uid-1",
        password_hash="$2b$12$hash",
        role=role,
        display_name="Alice",
        created_at=NOW,
        updated_at=NOW,
    )


def _doc(doc_id, data, exists=True):
    doc = MagicMock()
    doc.id = doc_id
    doc.exists = exists
    doc.to_dict.return_value = data
    return doc


class TestUsers:
    def test_save_user_excludes_username_from_payload(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        ref = MagicMock()
        mock_firestore_db.collection.return_value.document.return_value = ref

        FirestoreClient().save_user(_user())

        mock_firestore_db.collection.assert_called_with("users")
        mock_firestore_db.collection.return_value.document.assert_called_with("alice")
        saved = ref.set.call_args[0][0]
        assert "username" not in saved  # username は doc-id
        assert saved["password_hash"] == "$2b$12$hash"
        assert saved["user_id"] == "uid-1"

    def test_get_user_returns_none_when_absent(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        ref = MagicMock()
        ref.get.return_value = _doc("alice", None, exists=False)
        mock_firestore_db.collection.return_value.document.return_value = ref

        assert FirestoreClient().get_user("alice") is None

    def test_get_user_reconstructs_username_from_doc_id(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        data = _user().model_dump(mode="json")
        data.pop("username")
        ref = MagicMock()
        ref.get.return_value = _doc("alice", data)
        mock_firestore_db.collection.return_value.document.return_value = ref

        user = FirestoreClient().get_user("alice")
        assert user.username == "alice"
        assert user.role == "user"

    def test_list_users(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        d1 = _user("alice").model_dump(mode="json")
        d1.pop("username")
        mock_firestore_db.collection.return_value.order_by.return_value.stream.return_value = [
            _doc("alice", d1),
        ]
        users = FirestoreClient().list_users()
        assert [u.username for u in users] == ["alice"]

    def test_get_user_by_user_id(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        d1 = _user("alice").model_dump(mode="json")
        d1.pop("username")
        chain = mock_firestore_db.collection.return_value.where.return_value.limit.return_value
        chain.stream.return_value = [_doc("alice", d1)]
        user = FirestoreClient().get_user_by_user_id("uid-1")
        assert user.username == "alice"

    def test_delete_user(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        ref = MagicMock()
        mock_firestore_db.collection.return_value.document.return_value = ref
        FirestoreClient().delete_user("alice")
        ref.delete.assert_called_once()


def _session(expires_at) -> Session:
    return Session(
        session_id="hashed-token",
        user_id="uid-1",
        username="alice",
        role="user",
        created_at=NOW,
        expires_at=expires_at,
    )


class TestSessions:
    def test_create_session_excludes_session_id(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        ref = MagicMock()
        mock_firestore_db.collection.return_value.document.return_value = ref

        FirestoreClient().create_session(_session(NOW + timedelta(hours=1)))

        mock_firestore_db.collection.assert_called_with("sessions")
        mock_firestore_db.collection.return_value.document.assert_called_with("hashed-token")
        saved = ref.set.call_args[0][0]
        assert "session_id" not in saved
        assert saved["user_id"] == "uid-1"

    def test_get_session_returns_valid_session(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        data = _session(NOW + timedelta(days=999)).model_dump(mode="json")
        data.pop("session_id")
        ref = MagicMock()
        ref.get.return_value = _doc("hashed-token", data)
        mock_firestore_db.collection.return_value.document.return_value = ref

        session = FirestoreClient().get_session("hashed-token")
        assert session is not None
        assert session.user_id == "uid-1"
        ref.delete.assert_not_called()

    def test_get_session_returns_none_and_deletes_when_expired(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        # 実時刻に依存しないよう、明確に過去の固定日時を使う。
        data = _session(datetime(2000, 1, 1, tzinfo=timezone.utc)).model_dump(mode="json")
        data.pop("session_id")
        ref = MagicMock()
        ref.get.return_value = _doc("hashed-token", data)
        mock_firestore_db.collection.return_value.document.return_value = ref

        assert FirestoreClient().get_session("hashed-token") is None
        ref.delete.assert_called_once()  # 期限切れは遅延削除

    def test_get_session_returns_none_when_absent(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        ref = MagicMock()
        ref.get.return_value = _doc("x", None, exists=False)
        mock_firestore_db.collection.return_value.document.return_value = ref

        assert FirestoreClient().get_session("x") is None

    def test_delete_sessions_for_user_deletes_all(self, mock_firestore_db):
        from shared.firestore_client import FirestoreClient

        d1, d2 = MagicMock(), MagicMock()
        mock_firestore_db.collection.return_value.where.return_value.stream.return_value = [d1, d2]

        count = FirestoreClient().delete_sessions_for_user("uid-1")

        assert count == 2
        d1.reference.delete.assert_called_once()
        d2.reference.delete.assert_called_once()
