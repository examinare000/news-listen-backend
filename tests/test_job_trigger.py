"""shared.job_trigger のユニットテスト。

ジョブ起動の3要素を分離して検証する:
  1. debounce ガード（多重起動の抑止）
  2. ディスパッチャ選択（cloud_run / local_process / disabled）
  3. 各ディスパッチャの起動処理
"""
from unittest.mock import MagicMock, patch

import pytest

from shared.job_trigger import (
    CloudRunJobDispatcher,
    JobTrigger,
    LocalProcessJobDispatcher,
    NoOpJobTrigger,
    build_job_trigger,
)


# ---------- JobTrigger（debounce ガード） ----------

def test_trigger_dispatches_when_lock_acquired():
    dispatcher = MagicMock()
    lock = MagicMock()
    lock.try_acquire_job_lock.return_value = True
    trigger = JobTrigger(dispatcher, lock, default_debounce=60)

    assert trigger.trigger("recommendation", "user1") is True
    dispatcher.dispatch.assert_called_once_with("recommendation")


def test_trigger_skips_when_lock_not_acquired():
    """debounce ウィンドウ内ならディスパッチせず False を返す。"""
    dispatcher = MagicMock()
    lock = MagicMock()
    lock.try_acquire_job_lock.return_value = False
    trigger = JobTrigger(dispatcher, lock, default_debounce=60)

    assert trigger.trigger("recommendation", "user1") is False
    dispatcher.dispatch.assert_not_called()


def test_trigger_uses_per_job_debounce_window():
    """ジョブごとに異なる debounce 秒数でロックを取得する。"""
    dispatcher = MagicMock()
    lock = MagicMock()
    lock.try_acquire_job_lock.return_value = True
    trigger = JobTrigger(
        dispatcher, lock, default_debounce=60,
        debounce_overrides={"podcast-generator": 600},
    )

    trigger.trigger("podcast-generator", "user1")
    lock.try_acquire_job_lock.assert_called_once_with("user1", "podcast-generator", 600)


def test_trigger_returns_false_and_swallows_dispatch_error():
    """ジョブ起動失敗が star/dismiss の成否に波及しないよう例外を握りつぶす。"""
    dispatcher = MagicMock()
    dispatcher.dispatch.side_effect = RuntimeError("boom")
    lock = MagicMock()
    lock.try_acquire_job_lock.return_value = True
    trigger = JobTrigger(dispatcher, lock, default_debounce=60)

    assert trigger.trigger("recommendation", "user1") is False


# ---------- LocalProcessJobDispatcher ----------

def test_local_dispatcher_spawns_module_subprocess():
    popen = MagicMock()
    dispatcher = LocalProcessJobDispatcher(
        job_modules={"recommendation": "jobs.recommendation.main"}, popen=popen
    )

    dispatcher.dispatch("recommendation")

    args = popen.call_args[0][0]
    assert args[1:] == ["-m", "jobs.recommendation.main"]


def test_local_dispatcher_rejects_unknown_job():
    dispatcher = LocalProcessJobDispatcher(job_modules={}, popen=MagicMock())
    with pytest.raises(ValueError):
        dispatcher.dispatch("unknown-job")


# ---------- CloudRunJobDispatcher ----------

def test_cloud_run_dispatcher_posts_to_jobs_run_endpoint():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200)
    dispatcher = CloudRunJobDispatcher(
        project_id="proj", region="asia-northeast1", session=session
    )

    dispatcher.dispatch("podcast-generator")

    url = session.post.call_args[0][0]
    assert url == (
        "https://asia-northeast1-run.googleapis.com/apis/run.googleapis.com/v1/"
        "namespaces/proj/jobs/podcast-generator:run"
    )


def test_cloud_run_dispatcher_raises_on_error_status():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=403, text="forbidden")
    dispatcher = CloudRunJobDispatcher(
        project_id="proj", region="asia-northeast1", session=session
    )
    with pytest.raises(RuntimeError):
        dispatcher.dispatch("recommendation")


# ---------- build_job_trigger（ファクトリ） ----------

def test_build_job_trigger_local_process():
    with patch.dict("os.environ", {"JOB_TRIGGER_BACKEND": "local_process"}):
        trigger = build_job_trigger(MagicMock())
    assert isinstance(trigger, JobTrigger)
    assert isinstance(trigger._dispatcher, LocalProcessJobDispatcher)


def test_build_job_trigger_cloud_run():
    with patch.dict(
        "os.environ",
        {"JOB_TRIGGER_BACKEND": "cloud_run", "GOOGLE_CLOUD_PROJECT": "proj"},
    ):
        trigger = build_job_trigger(MagicMock())
    assert isinstance(trigger, JobTrigger)
    assert isinstance(trigger._dispatcher, CloudRunJobDispatcher)


def test_build_job_trigger_cloud_run_without_project_falls_back_to_noop():
    import os
    env = {k: v for k, v in os.environ.items()
           if k not in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT_ID")}
    env["JOB_TRIGGER_BACKEND"] = "cloud_run"
    with patch.dict("os.environ", env, clear=True):
        trigger = build_job_trigger(MagicMock())
    assert isinstance(trigger, NoOpJobTrigger)


def test_build_job_trigger_defaults_to_noop_when_unset():
    import os
    env = {k: v for k, v in os.environ.items() if k != "JOB_TRIGGER_BACKEND"}
    with patch.dict("os.environ", env, clear=True):
        trigger = build_job_trigger(MagicMock())
    assert isinstance(trigger, NoOpJobTrigger)
