"""T1: WebAuthnCredential / WebAuthnChallenge モデルと AuditAction 拡張のテスト。"""
from datetime import datetime, timezone
import typing


def test_webauthn_credential_required_fields():
    """WebAuthnCredential が必須フィールドで生成できる。"""
    from shared.models import WebAuthnCredential

    now = datetime.now(timezone.utc)
    cred = WebAuthnCredential(
        credential_id="abc123-b64url",
        user_id="user1",
        username="testuser",
        public_key="<pubkey-bytes-b64>",
        sign_count=0,
        transports=["internal"],
        aaguid=None,
        name=None,
        created_at=now,
        last_used_at=None,
    )
    assert cred.credential_id == "abc123-b64url"
    assert cred.user_id == "user1"
    assert cred.username == "testuser"
    assert cred.sign_count == 0
    assert cred.transports == ["internal"]
    assert cred.aaguid is None
    assert cred.name is None
    assert cred.last_used_at is None


def test_webauthn_credential_optional_fields():
    """WebAuthnCredential の optional フィールドが設定できる。"""
    from shared.models import WebAuthnCredential

    now = datetime.now(timezone.utc)
    cred = WebAuthnCredential(
        credential_id="cred-id",
        user_id="user2",
        username="user2",
        public_key="<pubkey>",
        sign_count=10,
        transports=["usb", "nfc"],
        aaguid="00000000-0000-0000-0000-000000000000",
        name="My Security Key",
        created_at=now,
        last_used_at=now,
    )
    assert cred.aaguid == "00000000-0000-0000-0000-000000000000"
    assert cred.name == "My Security Key"
    assert cred.last_used_at == now


def test_webauthn_challenge_registration_type():
    """WebAuthnChallenge が registration type で生成できる。"""
    from shared.models import WebAuthnChallenge

    now = datetime.now(timezone.utc)
    ch = WebAuthnChallenge(
        challenge_id="tok123",
        challenge="challenge_b64url",
        user_id=None,
        type="registration",
        expires_at=now,
        created_at=now,
    )
    assert ch.challenge_id == "tok123"
    assert ch.challenge == "challenge_b64url"
    assert ch.user_id is None
    assert ch.type == "registration"


def test_webauthn_challenge_authentication_type():
    """WebAuthnChallenge が authentication type で生成できる。"""
    from shared.models import WebAuthnChallenge

    now = datetime.now(timezone.utc)
    ch = WebAuthnChallenge(
        challenge_id="tok456",
        challenge="auth_challenge_b64",
        user_id="user1",
        type="authentication",
        expires_at=now,
        created_at=now,
    )
    assert ch.type == "authentication"
    assert ch.user_id == "user1"


def test_audit_action_includes_passkey_register():
    """AuditAction Literal に passkey_register が含まれる。"""
    from shared.models import AuditAction

    args = typing.get_args(AuditAction)
    assert "passkey_register" in args


def test_audit_action_includes_passkey_used():
    """AuditAction Literal に passkey_used が含まれる。"""
    from shared.models import AuditAction

    args = typing.get_args(AuditAction)
    assert "passkey_used" in args


def test_audit_action_includes_passkey_removed():
    """AuditAction Literal に passkey_removed が含まれる。"""
    from shared.models import AuditAction

    args = typing.get_args(AuditAction)
    assert "passkey_removed" in args


def test_webauthn_challenge_type_is_literal():
    """WebAuthnChallenge.type は 'registration' と 'authentication' のみ許容する。"""
    import pydantic
    from shared.models import WebAuthnChallenge

    now = datetime.now(timezone.utc)
    try:
        WebAuthnChallenge(
            challenge_id="x",
            challenge="y",
            user_id=None,
            type="invalid_type",
            expires_at=now,
            created_at=now,
        )
        assert False, "ValidationError expected"
    except pydantic.ValidationError:
        pass  # expected
