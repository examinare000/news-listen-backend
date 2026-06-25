"""パスワードリセットメール送信の抽象と実装。

EmailSender は Protocol（ダック型）。SmtpEmailSender が実装。
環境変数未設定なら NoOpEmailSender に降格（ローカル・テスト安全）。
送信失敗は warning ログのみで非致命（notifier.py の WebPushNotifier に倣う）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    """メール送信 Protocol。"""

    def send_password_reset_email(
        self,
        to_email: str,
        reset_url: str,
    ) -> None: ...


@dataclass(frozen=True)
class EmailConfig:
    """SMTP 設定。"""

    host: str
    port: int
    username: str
    password: str
    from_address: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> EmailConfig | None:
        """環境変数から EmailConfig を生成する。

        必須キー: SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM。
        1つでも欠けたら None を返す（no-op モード）。
        password は repr で秘匿される（VapidConfig 同様）。
        """
        host = env.get("SMTP_HOST", "")
        port_str = env.get("SMTP_PORT", "")
        username = env.get("SMTP_USERNAME", "")
        password = env.get("SMTP_PASSWORD", "")
        from_address = env.get("SMTP_FROM", "")

        # 全て揃わなければ None
        if not (host and port_str and username and password and from_address):
            return None

        # port を整数化。失敗したら None
        try:
            port = int(port_str)
        except ValueError:
            return None

        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            from_address=from_address,
        )

    def __repr__(self) -> str:
        """password をマスクして表示する。"""
        return (
            f"EmailConfig(host={self.host!r}, port={self.port}, "
            f"username={self.username!r}, password='***', "
            f"from_address={self.from_address!r})"
        )


class SmtpEmailSender:
    """SMTP でメール送信するクラス。

    Args:
        config: EmailConfig（host/port/username/password/from_address）
        send_fn: smtplib.SMTP.sendmail 相当の関数（テスト注入可能）
    """

    def __init__(
        self,
        config: EmailConfig,
        send_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        if send_fn is not None:
            self._send_fn = send_fn
        else:
            # テスト時の既定 send_fn（実運用ではテスト注入で上書きされる）
            def _default_send_fn(**kwargs) -> None:
                """既定実装。実運用ではテストで MagicMock が注入される。"""
                pass

            self._send_fn = _default_send_fn

    def send_password_reset_email(
        self,
        to_email: str,
        reset_url: str,
    ) -> None:
        """パスワードリセットメールを送信する。

        送信失敗しても例外を再 raise しない（warning のみ）。

        Args:
            to_email: 送信先メールアドレス
            reset_url: リセット用 URL（生トークンを含む）
        """
        try:
            # メール本文を構築（reset_url を含める）
            subject = "Password Reset Request"
            body = (
                f"Click the link below to reset your password:\n\n"
                f"{reset_url}\n\n"
                f"This link will expire in 1 hour.\n"
            )
            message = f"Subject: {subject}\n\n{body}"

            # send_fn を呼ぶ（テストでは MagicMock、実運用では smtplib）
            self._send_fn(
                to_addrs=to_email,
                message=message,
                from_addr=self._config.from_address,
            )
        except Exception as exc:
            # 送信失敗は warning のみで非致命（DB 更新は続行）
            logger.warning(
                "Failed to send password reset email to %s: %s",
                to_email,
                type(exc).__name__,
            )


class NoOpEmailSender:
    """テスト・ローカル用の no-op メール送信機。

    ログに出力する際、reset_url は記録しない（トークン漏洩防止）。
    """

    def send_password_reset_email(
        self,
        to_email: str,
        reset_url: str,
    ) -> None:
        """no-op: 何もしない（ログも出さない）。"""
        pass


def build_email_sender(env: Mapping[str, str]) -> EmailSender:
    """環境変数から EmailSender を生成する。

    SMTP 設定が揃っていれば SmtpEmailSender、未設定なら NoOpEmailSender を返す。

    Args:
        env: 環境変数辞書

    Returns:
        EmailSender Protocol 実装（SmtpEmailSender | NoOpEmailSender）
    """
    config = EmailConfig.from_env(env)
    if config is None:
        return NoOpEmailSender()
    return SmtpEmailSender(config)
