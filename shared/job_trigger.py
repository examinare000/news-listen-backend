"""star/dismiss を契機に recommendation / podcast-generator ジョブを起動する仕組み。

api サービスは長時間処理（TTS で数分）をリクエスト内で完結できない。Cloud Run の
リクエストタイムアウト・レスポンス後の CPU 割り当て制約があるため、ジョブは別コンテナで
非同期に走らせる。起動経路は環境で切り替える:

- 本番(Cloud Run): Cloud Run Jobs Admin API の `jobs:run` を SA 認証で叩く。
  Cloud Scheduler と同一の起動経路で、ジョブは専用コンテナ(1Gi/長 timeout)で実行される。
- ローカル(docker compose): api コンテナ内で `python -m <module>` をサブプロセス起動する。
  api イメージに jobs/ コードと Gemini 鍵を同梱することで Cloud Run を介さず動かす。

多重実行ガード: 連打や短時間の連続操作でジョブが積み上がり Gemini コストが膨らむのを防ぐため、
Firestore のロックドキュメントで debounce する（一定 TTL 内の再起動はスキップ）。ジョブは
starred/dismissed の全件を都度走査する設計なので、連続操作を 1 回の実行へまとめても取りこぼさない。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Protocol

logger = logging.getLogger(__name__)

# 論理ジョブ名 → ローカル実行モジュール（Cloud Run ジョブ名とローカルモジュールの対応表）
JOB_MODULES: dict[str, str] = {
    "recommendation": "jobs.recommendation.main",
    "podcast-generator": "jobs.podcast_generator.main",
}

# debounce 既定ウィンドウ（秒）。明示マッピングが無いジョブに適用する。
DEFAULT_DEBOUNCE_SECONDS = 120

# ジョブ別の debounce ウィンドウ。実行時間が長いジョブほど窓を広げ、
# 前の実行が走っている最中に次が起動して二重生成・コスト増になるのを抑える。
JOB_DEBOUNCE_SECONDS: dict[str, int] = {
    "recommendation": 120,
    "podcast-generator": 600,
}


class JobDispatcher(Protocol):
    def dispatch(self, job_name: str) -> None: ...


class CloudRunJobDispatcher:
    """Cloud Run Jobs Admin API の `jobs:run` を叩く（本番）。"""

    def __init__(self, project_id: str, region: str, session=None) -> None:
        self._project_id = project_id
        self._region = region
        # session を注入可能にしてユニットテストで認証を差し替えられるようにする。
        self._session = session

    def _ensure_session(self):
        if self._session is None:
            # 遅延 import: ローカルモードでは google-auth の認証情報が無くても
            # モジュール import を通したいため、ここで初めて読み込む。
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._session = AuthorizedSession(credentials)
        return self._session

    def dispatch(self, job_name: str) -> None:
        session = self._ensure_session()
        url = (
            f"https://{self._region}-run.googleapis.com/apis/run.googleapis.com/v1/"
            f"namespaces/{self._project_id}/jobs/{job_name}:run"
        )
        resp = session.post(url, timeout=30)
        if resp.status_code >= 400:
            # 2xx 以外は例外化し、JobTrigger 側のロギングに委ねる。
            raise RuntimeError(
                f"Cloud Run jobs:run failed: job={job_name} "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
        logger.info("Triggered Cloud Run job: %s", job_name)


class LocalProcessJobDispatcher:
    """api コンテナ内で `python -m <module>` をサブプロセス起動する（ローカル）。"""

    def __init__(self, job_modules: dict[str, str] | None = None, popen=subprocess.Popen) -> None:
        self._job_modules = JOB_MODULES if job_modules is None else job_modules
        self._popen = popen

    def dispatch(self, job_name: str) -> None:
        module = self._job_modules.get(job_name)
        if not module:
            raise ValueError(f"Unknown job name: {job_name}")
        # fire-and-forget。子プロセスの出力は親（api）のログにそのまま流す。
        self._popen([sys.executable, "-m", module])
        logger.info("Spawned local job process: %s (%s)", job_name, module)


class JobTrigger:
    """debounce ガード付きでジョブ起動を行うファサード。"""

    def __init__(
        self,
        dispatcher: JobDispatcher,
        lock,
        default_debounce: int = DEFAULT_DEBOUNCE_SECONDS,
        debounce_overrides: dict[str, int] | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._lock = lock  # FirestoreClient（try_acquire_job_lock を持つ）
        self._default_debounce = default_debounce
        self._debounce_overrides = debounce_overrides or {}

    def trigger(self, job_name: str, user_id: str) -> bool:
        """ジョブを起動する。debounce ウィンドウ内なら起動せず False を返す。"""
        ttl = self._debounce_overrides.get(job_name, self._default_debounce)
        if not self._lock.try_acquire_job_lock(user_id, job_name, ttl):
            logger.info("Skipped job (debounced): %s for %s", job_name, user_id)
            return False
        try:
            self._dispatcher.dispatch(job_name)
            return True
        except Exception as e:
            # ジョブ起動の失敗は star/dismiss の成否に波及させない（ベストエフォート）。
            logger.error("Failed to trigger job %s: %s", job_name, e)
            return False


class NoOpJobTrigger:
    """JOB_TRIGGER_BACKEND 未設定/不正時のフォールバック。何もしない。"""

    def trigger(self, job_name: str, user_id: str) -> bool:
        logger.warning("Job trigger disabled; skipping %s", job_name)
        return False


def build_job_trigger(db):
    """環境変数 JOB_TRIGGER_BACKEND からジョブトリガーを構築する。

    - "cloud_run":     Cloud Run Jobs Admin API（本番）
    - "local_process": api コンテナ内サブプロセス（ローカル docker compose）
    - その他/未設定:   NoOpJobTrigger（自動起動を無効化）
    """
    backend = os.environ.get("JOB_TRIGGER_BACKEND", "disabled").lower()
    default_debounce = int(os.environ.get("JOB_DEBOUNCE_SECONDS", DEFAULT_DEBOUNCE_SECONDS))

    if backend == "cloud_run":
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID")
        region = os.environ.get("GCP_REGION", "asia-northeast1")
        if not project_id:
            logger.error(
                "cloud_run backend requires GOOGLE_CLOUD_PROJECT/GCP_PROJECT_ID; "
                "disabling job auto-trigger"
            )
            return NoOpJobTrigger()
        return JobTrigger(
            CloudRunJobDispatcher(project_id, region),
            db,
            default_debounce=default_debounce,
            debounce_overrides=JOB_DEBOUNCE_SECONDS,
        )

    if backend == "local_process":
        return JobTrigger(
            LocalProcessJobDispatcher(),
            db,
            default_debounce=default_debounce,
            debounce_overrides=JOB_DEBOUNCE_SECONDS,
        )

    logger.warning("JOB_TRIGGER_BACKEND=%r; job auto-trigger disabled", backend)
    return NoOpJobTrigger()
