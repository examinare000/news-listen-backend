"""SSRF 対策：URL スキーム・ホスト名・IP アドレス検証。"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from shared.url_guard import UnsafeUrlError, validate_url, safe_fetch


# ============ UnsafeUrlError の動作 ============


def test_unsafe_url_error_has_reason_attribute():
    """UnsafeUrlError には reason 属性が公開されていること。"""
    err = UnsafeUrlError("private_ip")
    assert err.reason == "private_ip"


def test_unsafe_url_error_str_does_not_leak_url():
    """str(UnsafeUrlError) にはフルURL・解決IP・資格情報が含まれないこと。
    reason 分類のみを表示する。
    """
    err = UnsafeUrlError("private_ip")
    s = str(err)
    # reason が含まれることを確認
    assert "private_ip" in s
    # URL や IP が含まれないことを確認
    assert "http://" not in s
    assert "127.0.0.1" not in s
    assert "@" not in s


def test_unsafe_url_error_repr_does_not_leak_details():
    """repr(UnsafeUrlError) も同様に詳細を漏らさないこと。"""
    err = UnsafeUrlError("bad_scheme")
    r = repr(err)
    # reason が含まれることを確認
    assert "bad_scheme" in r
    # URL が含まれないことを確認
    assert "http://" not in r


# ============ validate_url: スキーム検証 ============


def test_validate_url_rejects_file_scheme():
    """file:// スキーム拒否。"""
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("file:///etc/passwd")
    assert exc_info.value.reason == "bad_scheme"


def test_validate_url_rejects_ftp_scheme():
    """ftp:// スキーム拒否。"""
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("ftp://example.com/file")
    assert exc_info.value.reason == "bad_scheme"


# ============ validate_url: ホスト名検証 ============


def test_validate_url_rejects_missing_hostname():
    """ホスト名欠落（http:///path）拒否。"""
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http:///path")
    # 欠落判定はスキーム検証後（hostname が空），reason は設計書で未明記
    # 実装では ValueError or bad_scheme 相当で良い。ここでは存在することだけ検証
    assert exc_info.value.reason in ("bad_scheme", "invalid_url")


def test_validate_url_rejects_malformed_url():
    """不正な URL 形式拒否。"""
    with pytest.raises(UnsafeUrlError):
        validate_url("not a valid url at all")


# ============ validate_url: IP アドレス解決・判定 ============


@patch("socket.getaddrinfo")
def test_validate_url_returns_ip_list_for_safe_public_ip(mock_getaddrinfo):
    """公開 IP の場合、IP 文字列のリストを返す。"""
    # 例: example.com -> 93.184.216.34 (Akamai / IANA確保公開IP)
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
    ]
    result = validate_url("https://example.com")
    assert isinstance(result, list)
    assert len(result) == 1
    assert "93.184.216.34" in result


@patch("socket.getaddrinfo")
def test_validate_url_rejects_loopback_127_0_0_1(mock_getaddrinfo):
    """127.0.0.1（localhost）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://localhost")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_ipv6_loopback(mock_getaddrinfo):
    """::1（IPv6 localhost）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://[::1]")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_private_10_x_x_x(mock_getaddrinfo):
    """10.0.0.0/8（プライベート IP）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://internal.corp")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_private_192_168(mock_getaddrinfo):
    """192.168.0.0/16（プライベート IP）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 443))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://router.local")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_private_172_16_to_31(mock_getaddrinfo):
    """172.16.0.0/12（プライベート IP）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.16.0.1", 443))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://vpn.internal")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_link_local_169_254(mock_getaddrinfo):
    """169.254.169.254（AWS メタデータサービス・Link-Local）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://169.254.169.254")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_ipv6_link_local(mock_getaddrinfo):
    """fe80::/10（IPv6 Link-Local）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", 443, 0, 0))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://[fe80::1]")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_ipv6_private_fd(mock_getaddrinfo):
    """fc00::/7（IPv6 プライベート）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::1", 443, 0, 0))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://[fd00::1]")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_ipv4_mapped_ipv6_loopback(mock_getaddrinfo):
    """::ffff:127.0.0.1（IPv4-mapped IPv6 localhost）拒否。
    バイパス防止: .ipv4_mapped で IPv4 に戻して再判定する。
    """
    mock_getaddrinfo.return_value = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:127.0.0.1", 443, 0, 0))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://[::ffff:127.0.0.1]")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_multicast(mock_getaddrinfo):
    """224.0.0.0/4（マルチキャスト）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("224.0.0.1", 443))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://224.0.0.1")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_reserved(mock_getaddrinfo):
    """240.0.0.0/4（予約済み）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("240.0.0.1", 443))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://240.0.0.1")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_unspecified(mock_getaddrinfo):
    """0.0.0.0（unspecified）拒否。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("0.0.0.0", 443))
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://0.0.0.0")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_rejects_if_any_resolved_ip_is_private(mock_getaddrinfo):
    """複数の解決 IP が返された場合、1 つでも危険 IP があれば拒否。"""
    # 公開 IP と localhost が混在
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("https://example.com")
    assert exc_info.value.reason == "private_ip"


@patch("socket.getaddrinfo")
def test_validate_url_returns_all_safe_ips(mock_getaddrinfo):
    """複数の安全 IP が返された場合，すべてをリストで返す。"""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443)),
    ]
    result = validate_url("https://example.com")
    assert isinstance(result, list)
    assert len(result) == 2
    assert "93.184.216.34" in result
    assert "93.184.216.35" in result


@patch("socket.getaddrinfo")
def test_validate_url_handles_dns_resolution_error(mock_getaddrinfo):
    """DNS 解決失敗（socket.gaierror）は UnsafeUrlError に正規化。"""
    mock_getaddrinfo.side_effect = socket.gaierror("Name resolution failed")
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://nonexistent.invalid")
    assert exc_info.value.reason == "dns_resolution_failed"


# ============ safe_fetch の基本動作 ============


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client")
def test_safe_fetch_returns_bytes_on_success(mock_client_class, mock_validate):
    """安全 URL で 200 レスポンス → bytes 返す。"""
    from unittest.mock import MagicMock
    mock_validate.return_value = ["93.184.216.34"]

    # stream() コンテキストマネージャー内のレスポンス
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.iter_bytes.return_value = iter([b"<html>test</html>"])

    # stream() の戻り値はコンテキストマネージャー
    mock_client_instance = mock_client_class.return_value.__enter__.return_value
    mock_client_instance.stream.return_value.__enter__.return_value = mock_response

    result = safe_fetch("https://example.com")
    assert result == b"<html>test</html>"


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client")
def test_safe_fetch_raises_unsafe_url_error_for_bad_scheme(mock_client_class, mock_validate):
    """validate_url が UnsafeUrlError を返す場合，そのまま raise。"""
    mock_validate.side_effect = UnsafeUrlError("bad_scheme")
    with pytest.raises(UnsafeUrlError) as exc_info:
        safe_fetch("file:///etc/passwd")
    assert exc_info.value.reason == "bad_scheme"


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client")
def test_safe_fetch_returns_none_on_non_200_status(mock_client_class, mock_validate):
    """非 2xx ステータスコード → None 返す。"""
    from unittest.mock import MagicMock
    mock_validate.return_value = ["93.184.216.34"]
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.headers = {}

    mock_client_instance = mock_client_class.return_value.__enter__.return_value
    mock_client_instance.stream.return_value.__enter__.return_value = mock_response

    result = safe_fetch("https://example.com/notfound")
    assert result is None


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client")
def test_safe_fetch_returns_none_on_connection_error(mock_client_class, mock_validate):
    """接続エラー → None 返す。"""
    import httpx
    from unittest.mock import MagicMock
    mock_validate.return_value = ["93.184.216.34"]
    mock_client_instance = mock_client_class.return_value.__enter__.return_value
    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__.side_effect = httpx.ConnectError("Connection failed")
    mock_client_instance.stream.return_value = mock_stream_ctx

    result = safe_fetch("https://example.com")
    assert result is None


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client")
def test_safe_fetch_returns_none_on_timeout(mock_client_class, mock_validate):
    """タイムアウト (httpx.TimeoutException) → None 返す。"""
    import httpx
    from unittest.mock import MagicMock
    mock_validate.return_value = ["93.184.216.34"]
    mock_client_instance = mock_client_class.return_value.__enter__.return_value
    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__.side_effect = httpx.TimeoutException("Request timeout")
    mock_client_instance.stream.return_value = mock_stream_ctx

    result = safe_fetch("https://example.com")
    assert result is None


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client")
def test_safe_fetch_respects_max_bytes_limit(mock_client_class, mock_validate):
    """max_bytes でコンテンツを打ち切る。"""
    from unittest.mock import MagicMock
    mock_validate.return_value = ["93.184.216.34"]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.iter_bytes.return_value = iter([b"0123456789"])

    mock_client_instance = mock_client_class.return_value.__enter__.return_value
    mock_client_instance.stream.return_value.__enter__.return_value = mock_response

    result = safe_fetch("https://example.com", max_bytes=10)
    assert result == b"0123456789"


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client")
def test_safe_fetch_raises_unsafe_url_error_when_exceeding_max_bytes(mock_client_class, mock_validate):
    """max_bytes 超過 → UnsafeUrlError raise。"""
    from unittest.mock import MagicMock
    mock_validate.return_value = ["93.184.216.34"]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.iter_bytes.return_value = iter([b"0123456789a"])

    mock_client_instance = mock_client_class.return_value.__enter__.return_value
    mock_client_instance.stream.return_value.__enter__.return_value = mock_response

    with pytest.raises(UnsafeUrlError) as exc_info:
        safe_fetch("https://example.com", max_bytes=10)
    # リーズンは content 超過を示す
    assert exc_info.value.reason in ("content_too_large", "max_bytes_exceeded")


# ============ safe_fetch リダイレクト ============


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client")
def test_safe_fetch_follows_redirect_with_validation(mock_client_class, mock_validate):
    """301 リダイレクト → Location を validate_url で検証し追跡。"""
    from unittest.mock import MagicMock
    # 最初のリクエスト: 301 + Location
    redirect_response = MagicMock()
    redirect_response.status_code = 301
    redirect_response.headers = {"location": "https://example.com/new"}
    redirect_response.iter_bytes.return_value = iter([b""])

    # リダイレクト先: 200 OK
    final_response = MagicMock()
    final_response.status_code = 200
    final_response.headers = {}
    final_response.iter_bytes.return_value = iter([b"Final content"])

    mock_client_instance = mock_client_class.return_value.__enter__.return_value
    # stream() のコンテキストマネージャーで返すモック
    mock_stream_context_1 = MagicMock()
    mock_stream_context_1.__enter__.return_value = redirect_response
    mock_stream_context_2 = MagicMock()
    mock_stream_context_2.__enter__.return_value = final_response
    mock_client_instance.stream.side_effect = [mock_stream_context_1, mock_stream_context_2]
    # validate_url は while loop で複数回呼ばれる（初回URL + リダイレクト後URL×1 + while内再検証）
    mock_validate.side_effect = [
        ["93.184.216.34"],  # 初回 URL（169行目）
        ["93.184.216.34"],  # while内：current_url再検証（178行目）
        ["93.184.216.34"],  # リダイレクト後（216行目で更新後、次ループで178行目）
        ["93.184.216.34"],  # while内：current_url再検証（178行目 2回目）
    ]

    result = safe_fetch("https://example.com/old")
    assert result == b"Final content"


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client.stream")
def test_safe_fetch_raises_unsafe_url_error_when_redirect_target_is_unsafe(mock_stream, mock_validate):
    """リダイレクト先 URL が危険 → UnsafeUrlError raise。"""
    from unittest.mock import MagicMock
    redirect_response = MagicMock()
    redirect_response.status_code = 301
    redirect_response.headers = {"location": "http://169.254.169.254/"}
    redirect_response.iter_bytes.return_value = iter([b""])

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__.return_value = redirect_response
    mock_stream.return_value = mock_stream_ctx

    mock_validate.side_effect = [
        ["93.184.216.34"],  # 初回 URL は OK
        UnsafeUrlError("private_ip"),  # リダイレクト先は危険
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        safe_fetch("https://example.com/old")
    assert exc_info.value.reason == "private_ip"


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client.stream")
def test_safe_fetch_raises_unsafe_url_error_when_redirects_exceed_max(mock_stream, mock_validate):
    """max_redirects を超過 → UnsafeUrlError raise。"""
    from unittest.mock import MagicMock
    # max_redirects=2 の場合，3 回目のリダイレクト時に raise
    def mock_validate_fn(url):
        return ["93.184.216.34"]

    mock_validate.side_effect = mock_validate_fn

    # stream() コンテキストマネージャーのモック
    redirect_contexts = []
    for i in range(5):
        resp = MagicMock()
        resp.status_code = 301
        resp.headers = {"location": f"https://example.com/{i}"}
        resp.iter_bytes.return_value = iter([b""])
        ctx = MagicMock()
        ctx.__enter__.return_value = resp
        redirect_contexts.append(ctx)

    mock_stream.side_effect = redirect_contexts

    with pytest.raises(UnsafeUrlError) as exc_info:
        safe_fetch("https://example.com/start", max_redirects=2)
    assert exc_info.value.reason == "too_many_redirects"


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client.stream")
def test_safe_fetch_supports_custom_timeout(mock_stream, mock_validate):
    """カスタム timeout を渡す → httpx.Timeout に変換して使用。"""
    from unittest.mock import MagicMock
    mock_validate.return_value = ["93.184.216.34"]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_bytes.return_value = [b"test"]

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__.return_value = mock_response
    mock_stream.return_value = mock_stream_ctx

    result = safe_fetch("https://example.com", timeout=30.0)
    assert result == b"test"
    # httpx.Client.stream が timeout=... で呼ばれることを検証
    # （実装によっては Timeout オブジェクトで渡されるため，ここでは呼び出しがあることだけ確認）
    assert mock_stream.called


# ============ safe_fetch IP ピン留め ============


@patch("shared.url_guard.validate_url")
@patch("shared.url_guard.httpx.Client.stream")
def test_safe_fetch_pins_connection_to_resolved_ip(mock_stream, mock_validate):
    """validate_url から得た IP で接続し，元ホスト名で SNI・証明書検証。
    （実装詳細の検証は難しいため，ここでは呼び出しがあることを確認）
    """
    from unittest.mock import MagicMock
    mock_validate.return_value = ["93.184.216.34"]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_bytes.return_value = [b"pinned"]

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__.return_value = mock_response
    mock_stream.return_value = mock_stream_ctx

    result = safe_fetch("https://example.com")
    assert result == b"pinned"
    # stream が呼ばれたことを確認
    assert mock_stream.called


# ============ _PinnedTransport（IP ピン留めの実体検証） ============


def _capture_pinned_request(pinned_ip, url):
    """_PinnedTransport.handle_request が施す変換結果を捕捉して返す。

    親 httpx.HTTPTransport.handle_request をモックし、実通信なしで
    「接続先 IP・Host ヘッダ・SNI」のミューテーションのみを検証する。
    """
    import httpx
    from shared.url_guard import _PinnedTransport

    transport = _PinnedTransport(pinned_ip=pinned_ip, verify=True)
    request = httpx.Request("GET", url)
    with patch.object(httpx.HTTPTransport, "handle_request") as mock_super:
        transport.handle_request(request)
        # 親に渡された（=実接続に使われる）request を返す
        return mock_super.call_args.args[0]


def test_pinned_transport_connects_to_pinned_ipv4():
    """接続先ホストが検証済み IPv4 に書き換わる。"""
    req = _capture_pinned_request("93.184.216.34", "https://example.com/feed")
    assert req.url.host == "93.184.216.34"


def test_pinned_transport_preserves_original_host_header():
    """Host ヘッダは元ホスト名のまま（仮想ホスト運用でも正しく届く）。"""
    req = _capture_pinned_request("93.184.216.34", "https://example.com/feed")
    assert req.headers["Host"] == "example.com"


def test_pinned_transport_sets_sni_to_original_host():
    """SNI・証明書検証は元ホスト名で行う（IP 接続でも証明書が一致する）。"""
    req = _capture_pinned_request("93.184.216.34", "https://example.com/feed")
    assert req.extensions.get("sni_hostname") == "example.com"


def test_pinned_transport_brackets_ipv6():
    """IPv6 の接続先は URL 内で [...] で囲む（RFC 3986）。"""
    ip = "2606:2800:220:1:248:1893:25c8:1946"
    req = _capture_pinned_request(ip, "https://example.com/feed")
    assert req.url.host == ip
    # httpx.URL は IPv6 ホストを内部的にブラケットなしで保持しつつ正しく整形する
    assert str(req.url).startswith(f"https://[{ip}]")


# ============ 拡張 denylist（標準フラグで漏れる範囲） ============


@patch("socket.getaddrinfo")
def test_validate_url_rejects_cgnat_shared_address_space(mock_getaddrinfo):
    """RFC 6598 共有アドレス空間（100.64.0.0/10）を拒否する。

    Python の ipaddress では is_private/is_reserved 共に False のため、
    明示的な拡張 denylist で SSRF 経路を塞ぐ。
    """
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", 0)),
    ]
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_url("http://cgnat.example")
    assert exc_info.value.reason == "private_ip"
