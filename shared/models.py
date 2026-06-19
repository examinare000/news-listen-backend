from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PodcastType = Literal["single", "digest"]
PodcastStatus = Literal["processing", "completed", "failed", "partial_failed"]

# クロスユーザーキャッシュのデフォルト言語。現状は固定値（言語切替 UI は未実装）。
# キャッシュキーの言語次元として将来の多言語化に備えて用意する。
DEFAULT_PODCAST_LANGUAGE: str = "ja-en"

# PodcastStatus の partial_failed はキャッシュに存在しない概念のため別型にする。
# 型レベルで混入を防ぎ、キャッシュの状態機械を明確に表現する。
CacheStatus = Literal["processing", "completed", "failed"]

# script_generator._DIFFICULTY_INSTRUCTIONS のキーと 1:1 対応させる。
# 新しい難易度を追加する場合は両ファイルを同時に変更すること。
DifficultyLevel = Literal["toeic_600", "toeic_900", "ielts_55", "ielts_7", "eiken_2", "eiken_p1"]


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
    default_difficulty: DifficultyLevel
    default_playback_speed: float = 1.0
    digest_enabled: bool = True
    digest_article_count: int = 5
    # 初回オンボーディング（おすすめサイト追加ステップ）の完了フラグ。
    # 既存ドキュメントには存在しないため default=False で後方互換を保つ。
    onboarding_completed: bool = False


class FeaturedSite(BaseModel):
    """システム提供のおすすめサイト。

    Firestore コレクション `featuredSites/{id}`（グローバル・ユーザー横断）に対応する。
    `id` はドキュメントキー（slug）。表示順は `order` 昇順で制御する。
    """

    id: str
    name: str
    url: str
    thumbnail_url: str | None = None
    description: str | None = None
    order: int = 0


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
    difficulty: DifficultyLevel
    audio_url: str
    japanese_intro_text: str
    duration_seconds: int
    status: PodcastStatus
    error_message: str | None = None
    created_at: datetime
    user_id: str


class PodcastCache(BaseModel):
    """クロスユーザー共有 Podcast キャッシュ。

    Firestore コレクション `podcastCache/{cache_key}` に対応する。
    processing 確保時点では成果物が未確定のため audio_url 等を Optional とする。
    """

    cache_key: str
    article_id: str
    difficulty: DifficultyLevel
    language: str
    status: CacheStatus
    audio_url: str | None = None
    japanese_intro_text: str | None = None
    duration_seconds: int | None = None
    created_at: datetime
