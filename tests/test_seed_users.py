"""scripts.seed_users の冪等性・初期投入テスト。"""
from unittest.mock import MagicMock, patch

from shared.models import User
from shared.security import verify_password

_ENV = {
    "INITIAL_ADMIN_USERNAME": "Examinare000",
    "INITIAL_ADMIN_PASS": "admin-pass",
    "INITIAL_USER_USERNAME": "nltestuser0",
    "INITIAL_USER_PASSWORD": "testpass01",
}


def _existing_user(username="x") -> User:
    from datetime import datetime, timezone

    now = datetime(2026, 6, 22, tzinfo=timezone.utc)
    return User(
        username=username,
        user_id="uid",
        password_hash="$2b$12$h",
        role="user",
        display_name=username,
        created_at=now,
        updated_at=now,
    )


def test_seeds_admin_and_user_when_absent():
    from scripts.seed_users import seed

    db = MagicMock()
    db.get_user.return_value = None
    with patch.dict("os.environ", {**_ENV, "USER_ID": ""}, clear=False):
        created = seed(db)

    assert created == 2
    saved = [c.args[0] for c in db.save_user.call_args_list]
    by_role = {u.role: u for u in saved}
    assert by_role["admin"].username == "examinare000"  # 正規化
    assert by_role["user"].username == "nltestuser0"
    # パスワードはハッシュ化される
    assert verify_password("admin-pass", by_role["admin"].password_hash)


def test_idempotent_skips_existing_users():
    from scripts.seed_users import seed

    db = MagicMock()
    db.get_user.return_value = _existing_user()
    with patch.dict("os.environ", _ENV, clear=False):
        created = seed(db)

    assert created == 0
    db.save_user.assert_not_called()


def test_test_user_inherits_legacy_user_id():
    from scripts.seed_users import seed

    db = MagicMock()
    db.get_user.return_value = None
    with patch.dict("os.environ", {**_ENV, "USER_ID": "legacy-uid-123"}, clear=False):
        seed(db)

    saved = [c.args[0] for c in db.save_user.call_args_list]
    test_user = next(u for u in saved if u.role == "user")
    assert test_user.user_id == "legacy-uid-123"


def test_missing_env_seeds_nothing():
    from scripts.seed_users import seed

    db = MagicMock()
    db.get_user.return_value = None
    cleared = {k: "" for k in _ENV}
    with patch.dict("os.environ", cleared, clear=False):
        created = seed(db)

    assert created == 0
    db.save_user.assert_not_called()
