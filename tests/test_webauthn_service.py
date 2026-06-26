"""T4: webauthn_service - py_webauthn ラッパーと sign_count 検証のテスト。"""
from unittest.mock import MagicMock, patch


# ── is_sign_count_valid ──────────────────────────────────────────────────────


def test_sign_count_both_zero_is_valid():
    """(0, 0) → True: カウントを追跡しない認証器は両方ゼロが有効。"""
    from shared.webauthn_service import is_sign_count_valid

    assert is_sign_count_valid(0, 0) is True


def test_sign_count_stored_zero_new_positive_is_valid():
    """(0, 1) → True: stored がゼロで new が正の値なら有効。"""
    from shared.webauthn_service import is_sign_count_valid

    assert is_sign_count_valid(0, 1) is True


def test_sign_count_new_greater_than_stored_is_valid():
    """(5, 6) → True: new > stored なら有効。"""
    from shared.webauthn_service import is_sign_count_valid

    assert is_sign_count_valid(5, 6) is True


def test_sign_count_equal_non_zero_is_invalid():
    """(5, 5) → False: 両方ゼロでなく new が strictly greater でもないため無効。"""
    from shared.webauthn_service import is_sign_count_valid

    assert is_sign_count_valid(5, 5) is False


def test_sign_count_regression_is_invalid():
    """(5, 4) → False: new < stored はリプレイアタックの可能性があり無効。"""
    from shared.webauthn_service import is_sign_count_valid

    assert is_sign_count_valid(5, 4) is False


def test_sign_count_stored_one_new_zero_is_invalid():
    """(1, 0) → False: カウントが巻き戻っているため無効。"""
    from shared.webauthn_service import is_sign_count_valid

    assert is_sign_count_valid(1, 0) is False


# ── generate_registration_options_wrapper ────────────────────────────────────


def test_generate_registration_options_wrapper_calls_library_correctly():
    """generate_registration_options を正しい引数で呼び、JSON 文字列を返す。"""
    from shared.webauthn_service import generate_registration_options_wrapper

    mock_options = MagicMock()
    mock_json = '{"type":"webauthn.create"}'

    with patch("webauthn.generate_registration_options", return_value=mock_options) as mock_gen, \
         patch("webauthn.options_to_json", return_value=mock_json) as mock_json_fn:

        result = generate_registration_options_wrapper(
            rp_id="example.com",
            rp_name="Example App",
            user_name="alice",
            timeout_ms=60000,
            exclude_credential_ids=["abc123", "def456"],
        )

    # options_to_json に options オブジェクトが渡されている
    mock_json_fn.assert_called_once_with(mock_options)

    # generate_registration_options に正しい引数が渡されている
    call_kwargs = mock_gen.call_args.kwargs
    assert call_kwargs["rp_id"] == "example.com"
    assert call_kwargs["rp_name"] == "Example App"
    assert call_kwargs["user_name"] == "alice"
    assert call_kwargs["timeout"] == 60000

    # exclude_credentials は PublicKeyCredentialDescriptor のリスト
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor
    from webauthn import base64url_to_bytes

    descriptors = call_kwargs["exclude_credentials"]
    assert len(descriptors) == 2
    assert isinstance(descriptors[0], PublicKeyCredentialDescriptor)
    assert descriptors[0].id == base64url_to_bytes("abc123")
    assert descriptors[1].id == base64url_to_bytes("def456")

    assert result == mock_json


def test_generate_registration_options_wrapper_empty_exclude_list():
    """除外クレデンシャルが空リストの場合も正常に動作する。"""
    from shared.webauthn_service import generate_registration_options_wrapper

    mock_options = MagicMock()

    with patch("webauthn.generate_registration_options", return_value=mock_options), \
         patch("webauthn.options_to_json", return_value="{}"):

        result = generate_registration_options_wrapper(
            rp_id="example.com",
            rp_name="Example App",
            user_name="alice",
            timeout_ms=60000,
            exclude_credential_ids=[],
        )

    assert result == "{}"


# ── generate_authentication_options_wrapper ──────────────────────────────────


def test_generate_authentication_options_wrapper_calls_library_correctly():
    """generate_authentication_options を正しい引数で呼び、JSON 文字列を返す。"""
    from shared.webauthn_service import generate_authentication_options_wrapper

    mock_options = MagicMock()
    mock_json = '{"type":"webauthn.get"}'

    with patch("webauthn.generate_authentication_options", return_value=mock_options) as mock_gen, \
         patch("webauthn.options_to_json", return_value=mock_json) as mock_json_fn:

        result = generate_authentication_options_wrapper(
            rp_id="example.com",
            timeout_ms=60000,
            allow_credential_ids=["AQID", "BAUI"],  # valid base64url strings
        )

    mock_json_fn.assert_called_once_with(mock_options)

    call_kwargs = mock_gen.call_args.kwargs
    assert call_kwargs["rp_id"] == "example.com"
    assert call_kwargs["timeout"] == 60000

    from webauthn.helpers.structs import PublicKeyCredentialDescriptor
    from webauthn import base64url_to_bytes

    descriptors = call_kwargs["allow_credentials"]
    assert len(descriptors) == 2
    assert isinstance(descriptors[0], PublicKeyCredentialDescriptor)
    assert descriptors[0].id == base64url_to_bytes("AQID")

    assert result == mock_json


def test_generate_authentication_options_wrapper_empty_allow_list():
    """allow_credentials が空リストの場合も正常に動作する。"""
    from shared.webauthn_service import generate_authentication_options_wrapper

    mock_options = MagicMock()

    with patch("webauthn.generate_authentication_options", return_value=mock_options) as mock_gen, \
         patch("webauthn.options_to_json", return_value="{}"):

        result = generate_authentication_options_wrapper(
            rp_id="example.com",
            timeout_ms=60000,
            allow_credential_ids=[],
        )

    call_kwargs = mock_gen.call_args.kwargs
    assert call_kwargs["allow_credentials"] == []
    assert result == "{}"


# ── verify_registration_response_wrapper ────────────────────────────────────


def test_verify_registration_response_wrapper_returns_dict():
    """verify_registration_response を呼び、正規化された dict を返す。"""
    from shared.webauthn_service import verify_registration_response_wrapper

    mock_result = MagicMock()
    mock_result.credential_id = b"\x01\x02\x03"
    mock_result.credential_public_key = b"\x04\x05\x06"
    mock_result.sign_count = 1
    mock_result.aaguid = "00000000-0000-0000-0000-000000000000"

    credential = {
        "id": "AQID",
        "response": {
            "attestationObject": "...",
            "clientDataJSON": "...",
            "transports": ["internal", "hybrid"],
        },
    }

    with patch("webauthn.verify_registration_response", return_value=mock_result) as mock_verify, \
         patch("webauthn.base64url_to_bytes", return_value=b"challenge_bytes") as mock_b64:

        result = verify_registration_response_wrapper(
            credential=credential,
            expected_challenge_b64url="abc123",
            rp_id="example.com",
            origins={"https://example.com"},
        )

    # challenge を bytes に変換している
    mock_b64.assert_called_once_with("abc123")

    # verify_registration_response の呼び出し確認
    call_kwargs = mock_verify.call_args.kwargs
    assert call_kwargs["expected_challenge"] == b"challenge_bytes"
    assert call_kwargs["expected_rp_id"] == "example.com"
    assert isinstance(call_kwargs["expected_origin"], list)
    assert set(call_kwargs["expected_origin"]) == {"https://example.com"}

    # 返却 dict のキーと型を確認
    assert "credential_id" in result
    assert "public_key" in result
    assert result["sign_count"] == 1
    assert result["aaguid"] == "00000000-0000-0000-0000-000000000000"
    assert result["transports"] == ["internal", "hybrid"]

    # bytes は base64url 文字列に変換されている
    import base64
    expected_cred_id = base64.urlsafe_b64encode(b"\x01\x02\x03").rstrip(b"=").decode("ascii")
    assert result["credential_id"] == expected_cred_id

    expected_pubkey = base64.urlsafe_b64encode(b"\x04\x05\x06").rstrip(b"=").decode("ascii")
    assert result["public_key"] == expected_pubkey


def test_verify_registration_response_wrapper_transports_fallback():
    """credential の response に transports キーがない場合、空リストになる。"""
    from shared.webauthn_service import verify_registration_response_wrapper

    mock_result = MagicMock()
    mock_result.credential_id = b"\x07"
    mock_result.credential_public_key = b"\x08"
    mock_result.sign_count = 0
    mock_result.aaguid = None

    credential = {
        "id": "Bw",
        "response": {
            "attestationObject": "...",
            "clientDataJSON": "...",
            # transports キーなし
        },
    }

    with patch("webauthn.verify_registration_response", return_value=mock_result), \
         patch("webauthn.base64url_to_bytes", return_value=b"ch"):

        result = verify_registration_response_wrapper(
            credential=credential,
            expected_challenge_b64url="ch",
            rp_id="example.com",
            origins={"https://example.com"},
        )

    assert result["transports"] == []
    assert result["aaguid"] is None


# ── verify_authentication_response_wrapper ───────────────────────────────────


def test_verify_authentication_response_wrapper_returns_dict():
    """verify_authentication_response を呼び、正規化された dict を返す。"""
    from shared.webauthn_service import verify_authentication_response_wrapper

    mock_result = MagicMock()
    mock_result.credential_id = b"\x0a\x0b\x0c"
    mock_result.new_sign_count = 42

    credential = {
        "id": "Cgss",
        "response": {
            "authenticatorData": "...",
            "clientDataJSON": "...",
            "signature": "...",
        },
    }

    with patch("webauthn.verify_authentication_response", return_value=mock_result) as mock_verify, \
         patch("webauthn.base64url_to_bytes", side_effect=lambda x: x.encode()) as mock_b64:

        result = verify_authentication_response_wrapper(
            credential=credential,
            expected_challenge_b64url="challenge_xyz",
            rp_id="example.com",
            origins={"https://example.com", "https://app.example.com"},
            credential_public_key_b64url="pubkey_b64url",
            credential_current_sign_count=41,
        )

    # base64url_to_bytes が challenge と public_key の両方に呼ばれている
    b64_calls = [call.args[0] for call in mock_b64.call_args_list]
    assert "challenge_xyz" in b64_calls
    assert "pubkey_b64url" in b64_calls

    call_kwargs = mock_verify.call_args.kwargs
    assert call_kwargs["expected_rp_id"] == "example.com"
    assert isinstance(call_kwargs["expected_origin"], list)
    assert set(call_kwargs["expected_origin"]) == {"https://example.com", "https://app.example.com"}
    assert call_kwargs["credential_current_sign_count"] == 41

    import base64
    expected_cred_id = base64.urlsafe_b64encode(b"\x0a\x0b\x0c").rstrip(b"=").decode("ascii")
    assert result["credential_id"] == expected_cred_id
    assert result["new_sign_count"] == 42
