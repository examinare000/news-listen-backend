"""issue #83: 構造化ログ・機微情報スクラブ・メトリクス出力のテスト。

ログに資格情報・PII を出さないこと（agent-rules/12）と、生成所要時間などの
構造化メトリクスを emit できることを検証する。スクラブはフォーマッタ段階で行い
（レコード非破壊）、メッセージ本文だけでなく例外トレースも対象にする。
"""
import json
import logging

import pytest


# ── 機微情報スクラブ（値を伏せる） ─────────────────────────────


@pytest.mark.parametrize(
    "raw,must_not_contain",
    [
        ("Authorization: Bearer abc123tok.en_value", "abc123tok.en_value"),
        ("login password=hunter2 done", "hunter2"),
        ("X-API-Key=supersecretkey", "supersecretkey"),
        ("X-API-Key: headersecret", "headersecret"),
        ("user email alice@example.com signed in", "alice@example.com"),
        ("cookie nl_session=deadbeefsession; path=/", "deadbeefsession"),
        # 複合キー（よくある資格情報フィールド名）も伏せる（レビュー指摘 #3）
        ("refresh_token=rrrsecret next", "rrrsecret"),
        ("access_token=aaasecret", "aaasecret"),
        ("client_secret=zzzsecret", "zzzsecret"),
        ("secret_key=kkksecret", "kkksecret"),
        ("my_password = ppp123", "ppp123"),
    ],
)
def test_scrub_removes_sensitive_values(raw, must_not_contain):
    from shared.logging_config import scrub

    out = scrub(raw)
    assert must_not_contain not in out
    assert "[REDACTED]" in out or "[EMAIL]" in out


@pytest.mark.parametrize(
    "text",
    [
        "This token based article about secret gardens is great",
        "Password: A Memoir of Secrets",          # 散文の `:`（key=value でない）
        "The Secret: Hidden Truths of AI",
        "Article token: future of work in tech",
        "Build v1.2@3.x release notes",            # メールでない @
    ],
)
def test_scrub_does_not_over_redact_prose(text):
    """一般語を含む自然文・記事タイトルは壊さない（レビュー指摘 #2/#3）。"""
    from shared.logging_config import scrub

    assert scrub(text) == text


# ── フォーマッタ段階のスクラブ（メッセージ＋例外トレース） ─────────


def test_scrubbing_formatter_redacts_message_and_exception_trace():
    """テキストモードでも例外トレースの機密がスクラブされる（must-fix #1）。"""
    from shared.logging_config import ScrubbingFormatter

    fmt = ScrubbingFormatter("%(levelname)s %(message)s")
    try:
        raise ValueError("db failed token=leak_me_token email=bob@example.com")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="x", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="unhandled password=topsecret", args=(), exc_info=sys.exc_info(),
        )
    out = fmt.format(record)
    assert "topsecret" not in out          # メッセージ本文
    assert "leak_me_token" not in out      # 例外メッセージ
    assert "bob@example.com" not in out    # 例外トレース中の PII
    assert "[REDACTED]" in out


def test_json_formatter_emits_valid_json_with_severity():
    from shared.logging_config import JsonFormatter

    fmt = JsonFormatter()
    record = logging.LogRecord(
        name="api", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="boom %s", args=("x",), exc_info=None,
    )
    record.duration_ms = 1234  # extra フィールド
    out = json.loads(fmt.format(record))
    assert out["severity"] == "ERROR"
    assert out["message"] == "boom x"
    assert out["duration_ms"] == 1234


# ── 構造化ログ設定 ───────────────────────────────────────────


def test_configure_logging_is_idempotent():
    """二重呼び出しでハンドラが重複増殖しない。"""
    from shared.logging_config import configure_logging

    root = logging.getLogger()
    configure_logging()
    after_first = len(root.handlers)
    configure_logging()
    assert len(root.handlers) == after_first


def test_configure_logging_handler_scrubs_output():
    """設定後のハンドラのフォーマッタがメッセージをスクラブする。"""
    from shared.logging_config import configure_logging, ScrubbingFormatter, JsonFormatter

    configure_logging()
    root = logging.getLogger()
    scrubbing = [
        h for h in root.handlers
        if isinstance(h.formatter, (ScrubbingFormatter, JsonFormatter))
    ]
    assert scrubbing, "スクラブするフォーマッタを持つハンドラが無い"
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname=__file__, lineno=1,
        msg="api_key=leakme", args=(), exc_info=None,
    )
    assert "leakme" not in scrubbing[0].formatter.format(record)


def test_invalid_log_level_falls_back_without_crash(monkeypatch):
    """不正な LOG_LEVEL でも例外を投げず INFO へフォールバックする（#5）。"""
    from shared.logging_config import _resolve_level

    assert _resolve_level("not-a-level") == logging.INFO
    assert _resolve_level("DEBUG") == logging.DEBUG


# ── メトリクス（構造化ログ行） ───────────────────────────────


def test_emit_metric_logs_structured_fields(caplog):
    from shared.logging_config import emit_metric

    logger = logging.getLogger("metric-test")
    with caplog.at_level(logging.INFO, logger="metric-test"):
        emit_metric(logger, "podcast_generation_duration", status="completed", duration_ms=900)

    rec = next(r for r in caplog.records if getattr(r, "metric", None) == "podcast_generation_duration")
    assert rec.status == "completed"
    assert rec.duration_ms == 900


def test_emit_metric_is_exception_safe_with_reserved_keys(caplog):
    """予約属性と衝突するフィールド名でも例外を投げない（#4）。"""
    from shared.logging_config import emit_metric

    logger = logging.getLogger("metric-test2")
    with caplog.at_level(logging.INFO, logger="metric-test2"):
        # "module"/"args" は LogRecord 予約属性。衝突しても落ちないこと。
        emit_metric(logger, "m", module="evil", args="evil", status="ok")

    rec = next((r for r in caplog.records if getattr(r, "metric", None) == "m"), None)
    assert rec is not None
    assert rec.status == "ok"
