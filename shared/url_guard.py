"""SSRF（Server-Side Request Forgery）対策：URL 検証とセキュアな HTTP フェッチ。

設計: 【issue #7】 SSRF 対策
- スキーム検証（http/https のみ許可）
- DNS 解決 + IP 判定（プライベート IP・ループバック・メタデータアドレス拒否）
- IP ピン留め接続（validate_url の解決 IP で接続，SNI・証明書検証は元ホスト名で実施）
  → DNSリバインディング攻撃（T1解決と接続時の独自解決で異なるIP返却）を防止
- ストリーミング + max_bytes 制限
- リダイレクト手動追跡（各ホップを再検証し、ホップ毎に新しいIPでピン留め）
"""
from __future__ import annotations

import ipaddress
import logging
import socket

import httpx

logger = logging.getLogger(__name__)

# === 定数（WHY コメント付き） ===

_ALLOWED_SCHEMES = ("http", "https")
"""http/https のみ許可。ftp:// や file:// は SSRF リスク。"""

_DEFAULT_TIMEOUT_SECONDS = 10.0
"""HTTP リクエストのタイムアウト。DNS 遅延・スローレスポンス対策。"""

_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB
"""レスポンスボディサイズ上限。DoS・メモリ枯渇対策。"""

_DEFAULT_MAX_REDIRECTS = 5
"""リダイレクト上限。無限ループ対策。"""

_EXTRA_DENIED_NETWORKS = (
    # RFC 6598 共有アドレス空間（Carrier-Grade NAT）。クラウド内部ネットワークでも
    # 使われ得るが Python の ipaddress では is_private/is_reserved いずれも False のため、
    # SSRF 経路にならないよう明示的に拒否する。
    ipaddress.ip_network("100.64.0.0/10"),
)
"""ipaddress の標準フラグで捕捉できない危険ネットワークの明示的拒否リスト。"""


# === IP ピン留めトランスポート ===


class _PinnedTransport(httpx.HTTPTransport):
    """接続先を検証済みIPに固定しつつ、SNI・証明書検証・Hostヘッダは元ホスト名で行う。

    httpcore が request.extensions["sni_hostname"] を尊重することを利用し、
    validate_url で得た安全IP へ接続する一方、TLS/証明書検証は元ホスト名で実施。
    これにより、DNSリバインディング（T1での解決結果と接続時の独自再解決が異なるIP）を防止。

    Args:
        pinned_ip: validate_url が返した安全IPアドレス（IPv4 or IPv6）
        **kwargs: 親 httpx.HTTPTransport に渡す（verify=True 等）
    """

    def __init__(self, pinned_ip: str, **kwargs):
        super().__init__(**kwargs)
        self._pinned_ip = pinned_ip

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """リクエストを処理。接続先IPを固定し、元ホスト名で証明書検証。"""
        original_host = request.url.host

        # === 接続先をピン留めIPに固定 ===
        # IPv6リテラルは URL内で [...] で囲む（RFC 3986）
        if ":" in self._pinned_ip:
            # IPv6 アドレス
            host_for_url = f"[{self._pinned_ip}]"
        else:
            # IPv4 アドレス
            host_for_url = self._pinned_ip

        # URL を copy_with で置き換え（ポート保持）
        request.url = request.url.copy_with(host=host_for_url)

        # === SNI・証明書検証は元ホスト名で実施 ===
        # httpcore が request.extensions["sni_hostname"] を尊重（SNI用）
        request.extensions = {**request.extensions, "sni_hostname": original_host}

        # === Hostヘッダを元ホスト名で固定 ===
        # 接続先をIPにしても、HTTP Hostヘッダは元ホスト名（サーバーが複数ホスト運用時に必須）
        request.headers["Host"] = original_host

        # 親トランスポートで実接続（接続先IP=ピン留め、SNI/証明書検証=元ホスト名）
        return super().handle_request(request)


class UnsafeUrlError(ValueError):
    """URL が SSRF 検査に不合格（スキーム・IP・形式）。

    資格情報・フルURL・解決 IP を str/repr に含めない。
    reason 属性で分類のみ公開。
    """

    def __init__(self, reason: str):
        """分類用の理由コードのみを保持して UnsafeUrlError を初期化する。

        Args:
            reason: 拒否理由の分類（例: "bad_scheme", "private_ip", "dns_resolution_failed"）
        """
        self.reason = reason
        # ValueError の msg には reason のみ（詳細非漏洩）
        super().__init__(f"unsafe url: {reason}")


def validate_url(url: str) -> list[str]:
    """URL を検証し，安全な解決 IP アドレスのリストを返す。

    1. スキーム検証（http/https のみ）
    2. ホスト名抽出
    3. DNS 解決（socket.getaddrinfo）
    4. 各 IP を判定（プライベート・ループバック・予約済み・マルチキャスト等）
    5. IPv4-mapped IPv6（::ffff:127.0.0.1 等）はバイパス防止で .ipv4_mapped で再判定

    Args:
        url: 検証対象 URL 文字列

    Returns:
        安全 IP アドレスの文字列リスト（≥1 個）

    Raises:
        UnsafeUrlError: スキーム不正・ホスト名欠落・危険 IP・DNS 失敗
    """
    # === スキーム検証 ===
    try:
        # urllib.parse を使って URL を分解
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise UnsafeUrlError("bad_scheme")
        hostname = parsed.hostname
        if not hostname:
            # http:/// のようにホスト名がない場合
            raise UnsafeUrlError("bad_scheme")
    except (ValueError, TypeError):
        raise UnsafeUrlError("bad_scheme")

    # === DNS 解決 ===
    try:
        # socket.getaddrinfo で全 IP を解決
        # getaddrinfo の返り値: (family, type, proto, canonname, sockaddr)
        # sockaddr は (ip, port) (IPv4) または (ip, port, flow, scopeid) (IPv6)
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise UnsafeUrlError("dns_resolution_failed")

    if not addr_infos:
        raise UnsafeUrlError("dns_resolution_failed")

    # === IP 判定 ===
    safe_ips = []
    for family, socktype, proto, canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            # IP パース失敗は拒否
            raise UnsafeUrlError("private_ip")

        # === IPv4-mapped IPv6 バイパス防止 ===
        # ::ffff:192.168.1.1 のような IPv6 アドレスで IPv4 を偽装するケースを対応
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
            ip_obj = ip_obj.ipv4_mapped

        # === 危険 IP 判定 ===
        # is_private: プライベート IP（10.0.0.0/8, 192.168.0.0/16, 172.16.0.0/12 等）
        # is_loopback: ループバック（127.0.0.0/8, ::1）
        # is_link_local: リンクローカル（169.254.0.0/16, fe80::/10）
        # is_reserved: 予約済み（240.0.0.0/4, 100.64.0.0/10 等）
        # is_multicast: マルチキャスト（224.0.0.0/4, ff00::/8）
        # is_unspecified: 未指定（0.0.0.0, ::）
        in_extra_denied = any(
            ip_obj.version == net.version and ip_obj in net
            for net in _EXTRA_DENIED_NETWORKS
        )
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_reserved
            or ip_obj.is_multicast
            or ip_obj.is_unspecified
            or in_extra_denied
        ):
            # 1 つでも危険 IP があれば拒否（複数解決で公開・プライベート混在時）
            raise UnsafeUrlError("private_ip")

        safe_ips.append(ip_str)

    if not safe_ips:
        raise UnsafeUrlError("private_ip")

    return safe_ips


def safe_fetch(
    url: str,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_redirects: int = _DEFAULT_MAX_REDIRECTS,
) -> bytes | None:
    """安全な URL フェッチ（SSRF 対策）。

    1. validate_url で初回 URL を検証，安全IP 取得
    2. IP ピン留めトランスポートで接続（接続先=検証済みIP、SNI/証明書検証=元ホスト名）
    3. follow_redirects=False で取得，リダイレクトは手動追跡
    4. 各 Location を validate_url で再検証，新しいIPでホップ毎にピン留めし直す
    5. レスポンスをストリーミング，max_bytes 超過で打ち切り

    DNSリバインディング対策: validate_url で解決した IP に必ず接続し、
    httpx の独自 DNS 再解決に依存しない。各ホップで再検証・再ピン留め。

    Args:
        url: フェッチ対象 URL
        max_bytes: レスポンスボディサイズ上限（バイト）
        timeout: HTTP リクエストタイムアウト（秒）
        max_redirects: リダイレクト上限回数

    Returns:
        レスポンスボディ（bytes）。フェッチ失敗時 None。

    Raises:
        UnsafeUrlError: 初回/リダイレクト先 URL が危険・max_bytes 超過・リダイレクト上限超過
    """
    from urllib.parse import urljoin

    # リダイレクト追跡用カウンター
    redirect_count = 0
    current_url = url

    while True:
        # === URL 検証・IP 解決 ===
        # 各ホップで安全性確認し、接続用IP を取得
        safe_ips = validate_url(current_url)
        # 最初の安全IP を使用（validate_url で複数返される場合）
        pinned_ip = safe_ips[0]

        # === IP ピン留めトランスポートで接続 ===
        # 接続先=ピン留めIP，SNI/証明書検証/Hostヘッダ=元ホスト名
        try:
            # 証明書検証(verify=True)はトランスポート側で行う。カスタム transport を
            # 渡した場合 httpx.Client の verify 引数は無視されるため重複指定しない。
            transport = _PinnedTransport(pinned_ip=pinned_ip, verify=True)
            with httpx.Client(transport=transport) as client:
                timeout_obj = httpx.Timeout(timeout)

                # リダイレクトなし、ストリーミング対応
                with client.stream(
                    "GET",
                    current_url,
                    follow_redirects=False,
                    timeout=timeout_obj,
                ) as response:
                    # === ステータスコード判定 ===
                    if response.status_code >= 400:
                        # 4xx, 5xx は失敗（None）
                        return None

                    if response.status_code in (301, 302, 303, 307, 308):
                        # === リダイレクト処理 ===
                        if redirect_count >= max_redirects:
                            raise UnsafeUrlError("too_many_redirects")

                        location = response.headers.get("location")
                        if not location:
                            # Location ヘッダなしは不正
                            return None

                        # 相対パスのリダイレクト対応
                        current_url = urljoin(current_url, location)
                        redirect_count += 1
                        # while True で次ホップへ（validate_url で再検証・ピン留めし直す）
                        continue

                    # === 2xx ステータス：本文取得 ===
                    if response.status_code < 200 or response.status_code >= 300:
                        # 2xx 以外（1xx, 3xx で follow_redirects=False なら到達しない）
                        return None

                    # ストリーミングで max_bytes 制限。
                    # bytes の += は毎回コピーが走り O(n^2) になるため bytearray に追記する。
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        body += chunk
                        if len(body) > max_bytes:
                            raise UnsafeUrlError("content_too_large")

                    return bytes(body)

        except httpx.TimeoutException:
            # タイムアウトは None
            return None
        except (httpx.ConnectError, httpx.RequestError):
            # 接続エラー等は None
            return None
