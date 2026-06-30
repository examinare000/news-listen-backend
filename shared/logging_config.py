"""構造化ログ・機微情報スクラブ・メトリクス出力（issue #83 エラー可観測性）。

目的:
- 資格情報・PII をログに出さない（`agent-rules/12-security-guidelines.md`）。**送出時にスクラブ**する。
- Cloud Logging が severity を解釈できる JSON 構造化ログ（LOG_FORMAT=json で有効）。
- 生成所要時間など SLO 信号を構造化フィールド付きで emit する。

設計方針:
- スクラブは **フォーマッタ段階**で行う（LogRecord を破壊的に書き換えない）。これにより
  メッセージ本文だけでなく**例外トレースバックも**スクラブされ（テキスト/JSON 両モード）、
  pytest の caplog（別フォーマッタ）が共有レコードの汚染を受けない。
- configure_logging() は冪等。多重呼び出しでハンドラを増殖させない。
"""
from __future__ import annotations

import json
import logging
import os
import re

_REDACTED = "[REDACTED]"

# 機微フィールド語幹。複合キー（refresh_token / client_secret / secret_key / my_password 等）も
# 拾えるよう、前後の識別子文字を許容する。
_SENSITIVE_STEM = r"(?:password|secret|token|api[_-]?key|authorization|nl_session|csrf_token)"

# key=value 形式（広い識別子）。散文の "Password: A Memoir" を壊さないため `=` 限定。
_KV_EQ_RE = re.compile(
    r"(?i)\b([\w-]*" + _SENSITIVE_STEM + r"[\w-]*)(\s*=\s*)([^\s,;'\")]+)"
)
# key: value 形式は、散文と紛れない明確なヘッダ/クッキー名のみ対象にする
# （password/secret/token などの一般語は `:` 形では伏せない＝記事タイトル等を壊さない）。
_HEADER_COLON_RE = re.compile(
    r"(?i)\b(x-api-key|authorization|nl_session|csrf_token)(\s*:\s*)([^\s,;]+)"
)
# Authorization 等の "Bearer <token>"。
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
# メールアドレス（PII）。TLD は英字 2 文字以上に限定し "v1.2@3.x" 等の誤爆を抑える。
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")


def scrub(text: str | None) -> str | None:
    """ログ文字列から資格情報・PII を伏せる。

    `key=value`（refresh_token= 等の複合キー含む）・ヘッダ/クッキーの `Key: value`・
    Bearer トークン・メールアドレスを [REDACTED] / [EMAIL] に置換する。
    一般語を含む自然文（"token based" / "Password: A Memoir" 等）は壊さない。
    """
    if not text:
        return text
    text = _BEARER_RE.sub("Bearer " + _REDACTED, text)
    text = _KV_EQ_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    text = _HEADER_COLON_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    return text


class ScrubbingFormatter(logging.Formatter):
    """整形後の最終文字列（メッセージ＋例外トレース）をスクラブするテキストフォーマッタ。

    レコードを書き換えず、出力文字列のみをスクラブする（非破壊）。
    """

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


class JsonFormatter(logging.Formatter):
    """Cloud Logging 互換の JSON 1 行を出力する（メッセージ・例外スタックをスクラブ）。

    severity（Cloud Logging が解釈）・message・logger・extra フィールドを含める。
    """

    _RESERVED = frozenset(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "message": scrub(record.getMessage()),
            "logger": record.name,
        }
        if record.exc_info:
            payload["stack"] = scrub(self.formatException(record.exc_info))
        # extra で渡された任意フィールド（duration_ms, metric, status など）を載せる。
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


_CONFIGURED = False


def _resolve_level(level: str | None) -> int:
    """LOG_LEVEL を解決する。未知・不正値は INFO にフォールバック（起動クラッシュを防ぐ）。"""
    name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    resolved = logging.getLevelName(name)
    return resolved if isinstance(resolved, int) else logging.INFO


def configure_logging(level: str | None = None) -> None:
    """ルートロガーにスクラブするハンドラを設定する（冪等）。

    LOG_FORMAT=json で JSON 構造化ログ（本番 Cloud Run 推奨）、それ以外はテキスト。
    どちらのモードでもメッセージ・例外トレースをスクラブする。LOG_LEVEL（既定 INFO・
    不正値は INFO）でレベル制御。多重呼び出しではハンドラを再設定しない。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(_resolve_level(level))

    handler = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            ScrubbingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root.addHandler(handler)

    _CONFIGURED = True


# logging.info(extra=...) で上書きできない予約属性（衝突すると logging が例外を投げる）。
_RESERVED_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__.keys()) | {"message"}


def emit_metric(logger: logging.Logger, name: str, **fields) -> None:
    """構造化メトリクスを 1 行のログとして emit する（issue #83・SLO 信号源）。

    Cloud Logging の log-based metric で抽出・集計する想定。`metric` フィールドに
    メトリクス名、残りを extra フィールドとして載せる。テレメトリは本処理を壊さない:
    予約属性と衝突するキーは `metric_<key>` へ退避し、何があっても例外を投げない。
    """
    safe_fields = {"metric": name}
    for key, value in fields.items():
        safe_key = f"metric_{key}" if key in _RESERVED_RECORD_ATTRS else key
        safe_fields[safe_key] = value
    try:
        logger.info(name, extra=safe_fields)
    except Exception:  # noqa: BLE001 - テレメトリ失敗で呼び出し元を壊さない
        pass
