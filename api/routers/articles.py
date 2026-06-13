"""POST /articles/{id}/star, /articles/{id}/dismiss エンドポイント。"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from api.dependencies import get_firestore_client, get_job_trigger, get_user_id
from api.schemas import ActionResponse
from shared.firestore_client import FirestoreClient

router = APIRouter()


@router.post("/articles/{article_id}/star", response_model=ActionResponse)
def star_article(
    article_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
    job_trigger=Depends(get_job_trigger),
):
    if not db.article_exists(article_id):
        raise HTTPException(status_code=404, detail="Article not found")

    db.add_starred_article(user_id, article_id)

    # スターは正のシグナル: レコメンド再計算と当該記事の Podcast 生成を自動起動する。
    # 重い処理（TTS で数分）はジョブ側コンテナで非同期実行されるため、ここでは起動だけを
    # レスポンス送出後の BackgroundTask に委ね、ユーザー操作の応答性を保つ。
    background_tasks.add_task(job_trigger.trigger, "recommendation", user_id)
    background_tasks.add_task(job_trigger.trigger, "podcast-generator", user_id)

    return ActionResponse(status="starred", article_id=article_id)


@router.post("/articles/{article_id}/dismiss", response_model=ActionResponse)
def dismiss_article(
    article_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
    job_trigger=Depends(get_job_trigger),
):
    if not db.article_exists(article_id):
        raise HTTPException(status_code=404, detail="Article not found")

    db.add_dismissed_article(user_id, article_id)

    # dismiss は負のシグナル: レコメンド再計算のみ起動する（Podcast は生成しない）。
    background_tasks.add_task(job_trigger.trigger, "recommendation", user_id)

    return ActionResponse(status="dismissed", article_id=article_id)
