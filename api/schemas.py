"""Pydantic request/response モデル。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

# DifficultyLevel は難易度 Literal の正本（shared/models.py）。
# from __future__ import annotations 下でも Pydantic がフィールド注釈を
# クラス生成時に解決するため、TYPE_CHECKING ではなく実行時 import が必要。
from shared.models import DifficultyLevel

if TYPE_CHECKING:
    from shared.models import Podcast


def _ensure_safe_url(value) -> None:
    """値が SSRF 検査に合格することを確認する（登録APIの多層防御）。

    url_guard.validate_url でスキーム・ホスト名・解決IP を検証し、危険なら
    Pydantic が 422 に変換できるよう ValueError へ正規化する。各リクエストモデルの
    field_validator から共通利用し、検証ロジックの重複を避ける。
    """
    from shared.url_guard import validate_url, UnsafeUrlError

    try:
        validate_url(str(value))
    except UnsafeUrlError as e:
        raise ValueError(f"unsafe url: {e.reason}")


class ArticleResponse(BaseModel):
    id: str
    title: str
    url: str
    source: str
    score: float
    published_at: str  # ISO 8601
    is_read: bool = False


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
    status: str
    error_message: str | None = None
    playback_position_seconds: float = 0.0
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
        # WHY: generator が processing 行を保存しない現状でも前方互換。
        # 将来 generator が processing 行を書けば本フィールドはそのまま反映される。
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
            status=podcast.status,
            error_message=podcast.error_message,
            playback_position_seconds=podcast.playback_position_seconds,
            created_at=podcast.created_at.isoformat(),
        )


class PodcastListResponse(BaseModel):
    podcasts: list[PodcastResponse]


class UpdatePlaybackPositionRequest(BaseModel):
    position_seconds: float = Field(ge=0)  # 負値は 422


class RssSourceRequest(BaseModel):
    name: str
    # spec-reviewer: HttpUrl で SSRF リスクを軽減する
    url: HttpUrl

    @field_validator("url")
    @classmethod
    def _validate_url_safe(cls, v):
        """URL が SSRF 検査に合格することを確認（プライベートIP・ループバック等は拒否）。"""
        _ensure_safe_url(v)
        return v


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

    @field_validator("url")
    @classmethod
    def _validate_url_safe(cls, v):
        """URL が SSRF 検査に合格することを確認。"""
        _ensure_safe_url(v)
        return v

    @field_validator("thumbnail_url")
    @classmethod
    def _validate_thumbnail_url_safe(cls, v):
        """thumbnail_url が SSRF 検査に合格することを確認（None は許容）。"""
        if v is None:
            return v
        _ensure_safe_url(v)
        return v


class OnboardingStatusResponse(BaseModel):
    onboarding_completed: bool


# ── 認証・ユーザー管理 ────────────────────────────────────────────
# パスワードは bcrypt の 72 バイト制限に合わせ最大長を制限する。
# _PASSWORD_MIN は 1 に（実際の最小値は validate_password_strength で強制）
_PASSWORD_MIN = 1
_PASSWORD_MAX = 72


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=_PASSWORD_MAX)


class UserResponse(BaseModel):
    """ユーザー公開情報。password_hash は決して含めない。"""

    username: str
    role: Literal["admin", "user"]
    display_name: str


class LoginResponse(BaseModel):
    """ログイン成功時のレスポンス。

    token は iOS など Cookie を使わないクライアント向け。Web は httpOnly Cookie
    （Set-Cookie）で受け取るため token をストレージに保存する必要はない。
    """

    token: str
    user: UserResponse


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=_PASSWORD_MAX)
    new_password: str = Field(min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)

    @field_validator("new_password", mode="after")
    @classmethod
    def _validate_new_password_strength(cls, v):
        """新しいパスワードの強度を検証（username=None, login exemption）。"""
        from shared.password_policy import validate_password_strength

        validate_password_strength(v, username=None)
        return v


class ForgotPasswordRequest(BaseModel):
    """パスワード忘れたリクエスト（認証不要）。"""

    username: str = Field(min_length=1, max_length=64)


class ResetPasswordRequest(BaseModel):
    """パスワードリセットリクエスト。"""

    token: str = Field(min_length=1)
    new_password: str = Field(min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)

    @field_validator("new_password", mode="after")
    @classmethod
    def _validate_new_password_strength(cls, v):
        """新しいパスワードの強度を検証。"""
        from shared.password_policy import validate_password_strength

        validate_password_strength(v, username=None)
        return v


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=64)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)
    display_name: str | None = Field(default=None, max_length=64)
    role: Literal["admin", "user"] = "user"

    @field_validator("password", mode="after")
    @classmethod
    def _validate_password_strength(cls, v, info):
        """パスワード強度を検証（ユーザー名を含める）。

        field_validator の mode="after" では info.data で他フィールドの値にアクセス可能。
        """
        from shared.password_policy import validate_password_strength

        username = info.data.get("username")
        validate_password_strength(v, username=username)
        return v


class UserUpdateRequest(BaseModel):
    """管理者によるユーザー更新。指定フィールドのみ変更する。"""

    role: Literal["admin", "user"] | None = None
    new_password: str | None = Field(default=None, min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)
    display_name: str | None = Field(default=None, max_length=64)

    @field_validator("new_password", mode="after")
    @classmethod
    def _validate_new_password_strength(cls, v):
        """新しいパスワードの強度を検証（None は許容）。"""
        if v is None:
            return v

        from shared.password_policy import validate_password_strength

        validate_password_strength(v, username=None)
        return v


class UserListResponse(BaseModel):
    users: list[UserResponse]


# ── 監査ログ ────────────────────────────────────────────


class AuditLogResponse(BaseModel):
    """監査ログエントリーのレスポンス。

    actor_user_id は内部 UUID であり、セキュリティ上の理由から API レスポンスに含めない。
    レスポンスには actor_username / target_username / ip / action / timestamp / details のみ返す。
    """

    action: str
    timestamp: str  # ISO 8601
    actor_username: str | None = None
    target_username: str | None = None
    ip: str | None = None
    details: dict | None = None


class AuditLogsResponse(BaseModel):
    logs: list[AuditLogResponse]


# ── ユーザー設定（デフォルト難易度・再生速度・ダイジェスト） ────────────────────────


class PreferencesResponse(BaseModel):
    """ユーザープリファレンス公開モデル。"""

    default_difficulty: str
    default_playback_speed: float
    digest_enabled: bool
    digest_article_count: int


class UpdatePreferencesRequest(BaseModel):
    """プリファレンス部分更新リクエスト。指定フィールドのみ変更する（他は保持）。"""

    default_difficulty: DifficultyLevel | None = None  # 不正値は Pydantic が 422 に変換
    default_playback_speed: float | None = Field(default=None, gt=0)
    digest_enabled: bool | None = None
    digest_article_count: int | None = Field(default=None, ge=1, le=20)


# ── ストレージ管理（使用量確認・一括削除） ────────────────────────


class StorageUsageItem(BaseModel):
    """ストレージ使用量レポートの個別 Podcast エントリー。"""

    id: str
    type: str
    size_bytes: int
    created_at: str  # ISO 8601


class StorageUsageResponse(BaseModel):
    """GET /podcasts/storage/usage のレスポンス。"""

    total_bytes: int
    podcast_count: int
    items: list[StorageUsageItem]


class StorageCleanupRequest(BaseModel):
    """POST /podcasts/storage/cleanup のリクエスト。"""

    older_than_days: int | None = Field(default=None, ge=0)


class StorageCleanupResponse(BaseModel):
    """POST /podcasts/storage/cleanup のレスポンス。"""

    deleted_podcast_count: int
    deleted_blob_count: int
    freed_bytes: int
