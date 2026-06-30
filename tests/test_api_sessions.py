"""issue #84: 本人のセッション管理エンドポイントのテスト。

GET /auth/sessions          自分の有効セッション一覧（current 判定）
DELETE /auth/sessions/{id}  個別失効（所有権検証・他人は 404）
POST /auth/sessions/revoke-others  現在以外を一括失効

api_client は get_firestore_client を mock_db に差し替える。get_current_user は本物が
動くため、mock_db.get_session を設定し Authorization: Bearer でトークンを渡す。
"""
from datetime import datetime, timedelta, timezone

from shared.models import Session
from shared.security import hash_token

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
CURRENT_TOKEN = "tok-current-raw"
CURRENT_SID = hash_token(CURRENT_TOKEN)
AUTH = {"Authorization": f"Bearer {CURRENT_TOKEN}"}


def _session(session_id, *, device="iPhone", last_used=None) -> Session:
    return Session(
        session_id=session_id,
        user_id="uid-1",
        username="alice",
        role="user",
        created_at=NOW,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        device_label=device,
        ip_hash="iphash",
        last_used_at=last_used or NOW,
    )


def _authenticate(mock_db):
    """現在のセッションを get_session で解決できるようにする。"""
    mock_db.get_session.return_value = _session(CURRENT_SID)


class TestListSessions:
    def test_lists_sessions_and_marks_current(self, api_client, mock_db):
        _authenticate(mock_db)
        mock_db.list_sessions_for_user.return_value = [
            _session(CURRENT_SID, device="Chrome on macOS"),
            _session("sid-other", device="Safari on iOS"),
        ]

        resp = api_client.get("/auth/sessions", headers=AUTH)

        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 2
        by_id = {s["id"]: s for s in sessions}
        assert by_id[CURRENT_SID]["current"] is True
        assert by_id["sid-other"]["current"] is False
        # デバイス名・作成/最終利用が返る
        assert by_id[CURRENT_SID]["device_label"] == "Chrome on macOS"
        assert by_id[CURRENT_SID]["created_at"]
        assert by_id[CURRENT_SID]["last_used_at"]
        # IP ハッシュなど内部情報は返さない
        assert "ip_hash" not in by_id[CURRENT_SID]
        mock_db.list_sessions_for_user.assert_called_once_with("uid-1")

    def test_requires_authentication(self, api_client, mock_db):
        mock_db.get_session.return_value = None
        resp = api_client.get("/auth/sessions", headers={"Authorization": "Bearer bad"})
        assert resp.status_code == 401


class TestRevokeSession:
    def test_revokes_own_session(self, api_client, mock_db, mock_audit):
        _authenticate(mock_db)
        mock_db.revoke_session.return_value = True

        resp = api_client.delete("/auth/sessions/sid-other", headers=AUTH)

        assert resp.status_code == 200
        mock_db.revoke_session.assert_called_once_with("sid-other", "uid-1")
        mock_audit.record.assert_called()
        assert mock_audit.record.call_args.kwargs["action"] == "session_revoke"

    def test_other_users_session_returns_404(self, api_client, mock_db):
        _authenticate(mock_db)
        # 所有権なし・不在は revoke_session が False を返す
        mock_db.revoke_session.return_value = False

        resp = api_client.delete("/auth/sessions/not-mine", headers=AUTH)

        assert resp.status_code == 404

    def test_requires_authentication(self, api_client, mock_db):
        mock_db.get_session.return_value = None
        resp = api_client.delete("/auth/sessions/x", headers={"Authorization": "Bearer bad"})
        assert resp.status_code == 401


class TestRevokeOthers:
    def test_revokes_all_except_current(self, api_client, mock_db, mock_audit):
        _authenticate(mock_db)
        mock_db.delete_sessions_for_user_except.return_value = 3

        resp = api_client.post("/auth/sessions/revoke-others", headers=AUTH)

        assert resp.status_code == 200
        assert resp.json()["revoked_count"] == 3
        # 現在のセッションはリクエスト由来で算出し、クライアント値を信用しない
        mock_db.delete_sessions_for_user_except.assert_called_once_with("uid-1", CURRENT_SID)
        mock_audit.record.assert_called()
        assert mock_audit.record.call_args.kwargs["action"] == "session_revoke"

    def test_requires_authentication(self, api_client, mock_db):
        mock_db.get_session.return_value = None
        resp = api_client.post(
            "/auth/sessions/revoke-others", headers={"Authorization": "Bearer bad"}
        )
        assert resp.status_code == 401
