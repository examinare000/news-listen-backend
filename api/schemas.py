"""Pydantic request/response モデル。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, HttpUrl

if TYPE_CHECKING:
    from shared.models import Podcast


class ArticleResponse(BaseModel):
    id: str
    title: str
    url: str
    source: str
    score: float
    published_at: str  # ISO 8601


class FeedResponse(BaseModel):
    articles: list[ArticleResponse]
    date: str


class PodcastResponse(BaseModel):
    id: str
    type: str
    article_ids: list[str]
    difficulty: str
    audio_url: str
    japanese_intro_text: str
    duration_seconds: int
    created_at: str  # ISO 8601

    @classmethod
    def from_podcast(
        cls, podcast: "Podcast", audio_url: str | None = None
    ) -> "PodcastResponse":
        """Podcast モデルから PodcastResponse を生成するファクトリ。
        list_podcasts と get_podcast の重複コードを一元化する。

        Args:
            podcast: 変換元の Podcast モデル。
            audio_url: 署名付き URL。指定された場合は podcast.audio_url（GCS blob path）
                の代わりに使用する。API ルーターは StorageClient.generate_audio_url() で
                変換してからこの引数に渡す。
        """
        return cls(
            id=podcast.id,
            type=podcast.type,
            article_ids=podcast.article_ids,
            difficulty=podcast.difficulty,
            # audio_url が指定された場合（API レイヤーで署名付き URL に変換済み）を優先する。
            # None の場合は Podcast.audio_url をそのまま使用（テストや内部処理向け）。
            audio_url=audio_url if audio_url is not None else podcast.audio_url,
            japanese_intro_text=podcast.japanese_intro_text,
            duration_seconds=podcast.duration_seconds,
            created_at=podcast.created_at.isoformat(),
        )


class PodcastListResponse(BaseModel):
    podcasts: list[PodcastResponse]


class RssSourceRequest(BaseModel):
    name: str
    # spec-reviewer: HttpUrl で SSRF リスクを軽減する
    url: HttpUrl


class RssSourcesResponse(BaseModel):
    # list[dict[str, str]] で OpenAPI スキーマに型情報を反映させる
    # RssSource モデルは shared.models にあり循環 import を避けるため dict[str, str] を使用
    sources: list[dict[str, str]]


class ActionResponse(BaseModel):
    status: str
    article_id: str


class FeaturedSiteResponse(BaseModel):
    id: str
    name: str
    url: str
    thumbnail_url: str | None = None
    description: str | None = None


class FeaturedSitesResponse(BaseModel):
    sites: list[FeaturedSiteResponse]


class FeaturedSiteRequest(BaseModel):
    """管理用おすすめサイト登録/更新リクエスト。"""

    name: str
    # RssSourceRequest と同様 HttpUrl で SSRF リスクを軽減する
    url: HttpUrl
    thumbnail_url: HttpUrl | None = None
    description: str | None = None
    order: int = 0


class OnboardingStatusResponse(BaseModel):
    onboarding_completed: bool
