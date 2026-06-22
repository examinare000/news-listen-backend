# backend — CLAUDE.md

> Submodule (`news-listen-backend`). 親リポジトリ `news-listen` 配下で作業する場合、
> `../agent-rules/` のルールが正本。本ファイルはこのモジュール固有の補足のみ。

## スタック
- Python `>=3.12` / FastAPI 系 API + ジョブ。Lint: `ruff`。
- テスト: `pytest`（`testpaths=["tests"]`, `pythonpath=["."]`）。
- コンテナ: `Dockerfile.api` / `Dockerfile.jobs`（`agent-rules/70-docker-environments.md` 準拠）。

## 作業規約
- TDD 必須（`agent-rules/11-testing-strategy.md`）。実装前にテストを書く。
- シークレット・認証は `agent-rules/12-security-guidelines.md` 準拠。ログに資格情報を出さない。
- テスト実行: `pytest` ／ Lint: `ruff check .`。

## このモジュールで触らないこと
- `*.bundle` 等の生成物・依存物は手動編集しない。
