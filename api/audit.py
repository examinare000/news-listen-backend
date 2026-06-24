"""監査ログの記録を担当する AuditLogger。

ベストエフォート設計：Firestore への追記失敗時も、本来の操作（ログイン・CRUD）は
成功として扱う。失敗は error ログとして記録される。
"""
import logging
from datetime import datetime, timezone
from typing import Callable

from shared.models import AuditAction, AuditLog, Session
from shared.firestore_client import FirestoreClient

logger = logging.getLogger(__name__)


class AuditLogger:
    """監査ログの記録を担当する。

    依存性注入で FirestoreClient と clock 関数（時刻取得）を受け取り、
    テストで時刻を制御可能にしている。
    """

    def __init__(
        self,
        firestore_client: FirestoreClient,
        clock: Callable[[], datetime] | None = None,
    ):
        """初期化。

        Args:
            firestore_client: Firestore クライアント
            clock: 時刻取得関数（デフォルト: datetime.now(timezone.utc)）
        """
        self._db = firestore_client
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def record(
        self,
        action: AuditAction,
        actor: Session | None = None,
        target_username: str | None = None,
        ip: str | None = None,
        details: dict | None = None,
    ) -> None:
        """監査ログを記録する。

        Firestore への追記失敗時は error ログを出すが、呼び出し元へは
        例外を投げない（ベストエフォート）。

        Args:
            action: アクション（login_success / user_create / ... など）
            actor: アクターのセッション（ユーザー情報を含む）
            target_username: 対象ユーザー名（削除・更新対象など）
            ip: クライアント IP（生値）
            details: 追加情報（dict）
        """
        try:
            audit = AuditLog(
                action=action,
                timestamp=self._clock(),
                actor_user_id=actor.user_id if actor else None,
                actor_username=actor.username if actor else None,
                target_username=target_username,
                ip=ip,
                details=details,
            )
            self._db.append_audit_log(audit)
        except Exception as e:
            # ベストエフォート：監査ログ記録失敗時は error ログのみ出力し、
            # 呼び出し元の処理を続行させる（本操作の成功を妨げない）。
            # 機微情報の漏洩を避けるため action のみを記録する。
            logger.error("監査ログの記録に失敗しました action=%s", action, exc_info=e)
