"""WebAuthn 設定を環境変数から読み込むデータクラス。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass
class WebAuthnConfig:
    rp_id: str
    rp_name: str
    origins: set[str]
    timeout_ms: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "WebAuthnConfig | None":
        """環境変数マッピングから WebAuthnConfig を生成する。

        WEBAUTHN_RP_ID が未設定または空の場合は None を返す。
        """
        rp_id = env.get("WEBAUTHN_RP_ID", "").strip()
        if not rp_id:
            return None

        rp_name = env.get("WEBAUTHN_RP_NAME", "News Listen")

        raw_origins = env.get("WEBAUTHN_ORIGIN", "")
        origins: set[str] = {
            o.strip() for o in raw_origins.split(",") if o.strip()
        }

        timeout_ms = int(env.get("WEBAUTHN_TIMEOUT_MS", "60000"))

        return cls(
            rp_id=rp_id,
            rp_name=rp_name,
            origins=origins,
            timeout_ms=timeout_ms,
        )
