"""py_webauthn ライブラリへの依存を一箇所に集約するサービス層。

このモジュールがアプリ内で唯一 `webauthn` パッケージを import する場所とする。
外部に公開するのは型付き純粋関数のみであり、ライブラリの詳細は隠蔽する。
"""
from __future__ import annotations

import base64

import webauthn
from webauthn.helpers.structs import PublicKeyCredentialDescriptor


# ── ユーティリティ ──────────────────────────────────────────────────────────


def _bytes_to_b64url(data: bytes) -> str:
    """bytes を padding なし base64url 文字列に変換する。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _ids_to_descriptors(credential_ids: list[str]) -> list[PublicKeyCredentialDescriptor]:
    """base64url 文字列のリストを PublicKeyCredentialDescriptor のリストに変換する。"""
    return [
        PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(cid))
        for cid in credential_ids
    ]


# ── sign_count 検証 ─────────────────────────────────────────────────────────


def is_sign_count_valid(stored: int, new: int) -> bool:
    """sign_count の更新が正当かどうかを検証する純粋関数。

    ルール:
    - stored == 0 かつ new == 0 → True (カウント非追跡の認証器)
    - new > stored → True
    - それ以外 → False (リプレイアタックの疑い)
    """
    if stored == 0 and new == 0:
        return True
    return new > stored


# ── Registration ────────────────────────────────────────────────────────────


def generate_registration_options_wrapper(
    rp_id: str,
    rp_name: str,
    user_name: str,
    timeout_ms: int,
    exclude_credential_ids: list[str],
) -> str:
    """登録オプションを生成して JSON 文字列で返す。"""
    options = webauthn.generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_name=user_name,
        timeout=timeout_ms,
        exclude_credentials=_ids_to_descriptors(exclude_credential_ids),
    )
    return webauthn.options_to_json(options)


def verify_registration_response_wrapper(
    credential: dict,
    expected_challenge_b64url: str,
    rp_id: str,
    origins: set[str],
) -> dict:
    """登録レスポンスを検証し、正規化された dict を返す。

    Returns:
        {
            "credential_id": str,  # base64url
            "public_key": str,     # base64url
            "sign_count": int,
            "aaguid": str | None,
            "transports": list[str],
        }
    """
    expected_challenge = webauthn.base64url_to_bytes(expected_challenge_b64url)

    result = webauthn.verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id,
        expected_origin=list(origins),
    )

    # transports は credential の response から取得する
    transports: list[str] = (
        credential.get("response", {}).get("transports", [])
    )

    return {
        "credential_id": _bytes_to_b64url(result.credential_id),
        "public_key": _bytes_to_b64url(result.credential_public_key),
        "sign_count": result.sign_count,
        "aaguid": result.aaguid if result.aaguid else None,
        "transports": transports,
    }


# ── Authentication ───────────────────────────────────────────────────────────


def generate_authentication_options_wrapper(
    rp_id: str,
    timeout_ms: int,
    allow_credential_ids: list[str],
) -> str:
    """認証オプションを生成して JSON 文字列で返す。"""
    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        timeout=timeout_ms,
        allow_credentials=_ids_to_descriptors(allow_credential_ids),
    )
    return webauthn.options_to_json(options)


def verify_authentication_response_wrapper(
    credential: dict,
    expected_challenge_b64url: str,
    rp_id: str,
    origins: set[str],
    credential_public_key_b64url: str,
    credential_current_sign_count: int,
) -> dict:
    """認証レスポンスを検証し、正規化された dict を返す。

    Returns:
        {
            "credential_id": str,  # base64url
            "new_sign_count": int,
        }
    """
    expected_challenge = webauthn.base64url_to_bytes(expected_challenge_b64url)
    public_key_bytes = webauthn.base64url_to_bytes(credential_public_key_b64url)

    result = webauthn.verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id,
        expected_origin=list(origins),
        credential_public_key=public_key_bytes,
        credential_current_sign_count=credential_current_sign_count,
    )

    return {
        "credential_id": _bytes_to_b64url(result.credential_id),
        "new_sign_count": result.new_sign_count,
    }
