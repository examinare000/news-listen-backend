"""初期ユーザー（管理者・テストユーザー）を users コレクションへ投入するスクリプト。

`.env` の以下を読み取り、未登録のユーザーのみ作成する（冪等）:
- INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASS  → role=admin
- INITIAL_USER_USERNAME / INITIAL_USER_PASSWORD → role=user

テストユーザーの user_id は、既存 PoC データを引き継ぐため `USER_ID` 環境変数が
あればその値を流用する（無ければ新規採番）。

実行（GOOGLE_APPLICATION_CREDENTIALS / プロジェクト設定が必要）:

    cd backend
    python -m scripts.seed_users

冪等: 既存ユーザーはスキップする。パスワードを再設定したい場合は管理 API
（PATCH /admin/users/{username}）を使う（既存パスワードを意図せず上書きしない）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from shared.firestore_client import FirestoreClient
from shared.models import User, UserRole
from shared.security import hash_password
from shared.utils import normalize_username


def _ensure_user(
    db: FirestoreClient,
    username: str,
    password: str,
    role: UserRole,
    user_id: str | None = None,
) -> bool:
    """未登録なら作成し True を返す。既存ならスキップして False を返す。"""
    norm = normalize_username(username)
    if db.get_user(norm) is not None:
        print(f"skip (exists): {norm}")
        return False
    now = datetime.now(timezone.utc)
    user = User(
        username=norm,
        user_id=user_id or uuid.uuid4().hex,
        password_hash=hash_password(password),
        role=role,
        display_name=norm,
        created_at=now,
        updated_at=now,
    )
    db.save_user(user)
    print(f"seeded: {norm} (role={role}, user_id={user.user_id})")
    return True


def seed(db: FirestoreClient | None = None) -> int:
    """初期ユーザーを投入し、新規作成した件数を返す。"""
    db = db or FirestoreClient()
    created = 0

    admin_username = os.environ.get("INITIAL_ADMIN_USERNAME")
    admin_pass = os.environ.get("INITIAL_ADMIN_PASS")
    if admin_username and admin_pass:
        if _ensure_user(db, admin_username, admin_pass, role="admin"):
            created += 1
    else:
        print("skip admin: INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASS が未設定")

    user_username = os.environ.get("INITIAL_USER_USERNAME")
    user_password = os.environ.get("INITIAL_USER_PASSWORD")
    if user_username and user_password:
        # 既存 PoC データ継承: USER_ID があればテストユーザーの user_id に流用する。
        legacy_user_id = os.environ.get("USER_ID") or None
        if _ensure_user(
            db, user_username, user_password, role="user", user_id=legacy_user_id
        ):
            created += 1
    else:
        print("skip user: INITIAL_USER_USERNAME / INITIAL_USER_PASSWORD が未設定")

    return created


if __name__ == "__main__":
    count = seed()
    print(f"done: {count} users seeded")
