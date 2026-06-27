"""T2: WebAuthnConfig - 環境変数からの設定読み込みのテスト。"""


def test_from_env_missing_rp_id_returns_none():
    """WEBAUTHN_RP_ID が未設定の場合 None を返す。"""
    from shared.webauthn_config import WebAuthnConfig

    result = WebAuthnConfig.from_env({})
    assert result is None


def test_from_env_empty_rp_id_returns_none():
    """WEBAUTHN_RP_ID が空文字の場合 None を返す。"""
    from shared.webauthn_config import WebAuthnConfig

    result = WebAuthnConfig.from_env({"WEBAUTHN_RP_ID": ""})
    assert result is None


def test_from_env_minimal_config_uses_defaults():
    """WEBAUTHN_RP_ID のみ設定した場合、残フィールドはデフォルト値になる。"""
    from shared.webauthn_config import WebAuthnConfig

    result = WebAuthnConfig.from_env({"WEBAUTHN_RP_ID": "localhost"})
    assert result is not None
    assert result.rp_id == "localhost"
    assert result.rp_name == "News Listen"
    assert result.origins == set()
    assert result.timeout_ms == 60000


def test_from_env_all_fields_parsed_correctly():
    """全環境変数を設定した場合、正しく解析される。"""
    from shared.webauthn_config import WebAuthnConfig

    env = {
        "WEBAUTHN_RP_ID": "example.com",
        "WEBAUTHN_RP_NAME": "My App",
        "WEBAUTHN_ORIGIN": "https://example.com, https://dev.example.com",
        "WEBAUTHN_TIMEOUT_MS": "90000",
    }
    result = WebAuthnConfig.from_env(env)
    assert result is not None
    assert result.rp_id == "example.com"
    assert result.rp_name == "My App"
    assert result.origins == {"https://example.com", "https://dev.example.com"}
    assert result.timeout_ms == 90000


def test_from_env_csv_origin_strips_whitespace():
    """WEBAUTHN_ORIGIN のCSVはホワイトスペースをトリムして解析する。"""
    from shared.webauthn_config import WebAuthnConfig

    result = WebAuthnConfig.from_env(
        {
            "WEBAUTHN_RP_ID": "example.com",
            "WEBAUTHN_ORIGIN": "https://a.com , https://b.com",
        }
    )
    assert result is not None
    assert result.origins == {"https://a.com", "https://b.com"}
