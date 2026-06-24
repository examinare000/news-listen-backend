"""レート制限ポリシー evaluate_rate_limit の単体テスト。

user/ip 軸の複合判定ロジックをテストする（DB 非依存）。
"""


def test_both_allowed_returns_allowed_zero():
    """両軸 allowed: (True, 0) を返す。"""
    from api.ratelimit import evaluate_rate_limit

    allowed, retry = evaluate_rate_limit(True, 0, True, 0)
    assert allowed is True
    assert retry == 0


def test_user_blocked_returns_user_retry():
    """user軸 blocked: user の retry_after を返す。"""
    from api.ratelimit import evaluate_rate_limit

    allowed, retry = evaluate_rate_limit(False, 3600, True, 0)
    assert allowed is False
    assert retry == 3600


def test_ip_blocked_returns_ip_retry():
    """ip軸 blocked: ip の retry_after を返す。"""
    from api.ratelimit import evaluate_rate_limit

    allowed, retry = evaluate_rate_limit(True, 0, False, 7200)
    assert allowed is False
    assert retry == 7200


def test_both_blocked_returns_max_retry():
    """両軸 blocked: max(user, ip) の retry_after を返す。"""
    from api.ratelimit import evaluate_rate_limit

    allowed, retry = evaluate_rate_limit(False, 1800, False, 3600)
    assert allowed is False
    assert retry == 3600  # max(1800, 3600)
