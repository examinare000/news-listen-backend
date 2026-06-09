from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PodcastType = Literal["single", "digest"]
PodcastStatus = Literal["processing", "completed", "failed", "partial_failed"]


class Article(BaseModel):
    id: str
    title: str
    url: str
    source: str
    content: str
    published_at: datetime
    fetched_at: datetime
    content_fetched_at: datetime | None = None


class RssSource(BaseModel):
    name: str
    url: str


class UserPrefs(BaseModel):
    user_id: str
    starred_article_ids: list[str] = Field(default_factory=list)
    dismissed_article_ids: list[str] = Field(default_factory=list)
    rss_sources: list[RssSource] = Field(default_factory=list)
    default_difficulty: str
    default_playback_speed: float = 1.0
    digest_enabled: bool = True
    digest_article_count: int = 5


class RecommendedArticle(BaseModel):
    article_id: str
    score: float


class Recommendation(BaseModel):
    user_id: str
    date: str  # "YYYY-MM-DD"
    articles: list[RecommendedArticle] = Field(default_factory=list)
    generated_at: datetime


class Podcast(BaseModel):
    id: str
    type: PodcastType
    article_ids: list[str]
    difficulty: str
    audio_url: str
    japanese_intro_text: str
    duration_seconds: int
    status: PodcastStatus
    error_message: str | None = None
    created_at: datetime
    user_id: str
