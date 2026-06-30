from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PodcastType = Literal["single", "digest"]
PodcastStatus = Literal["processing", "completed", "failed", "partial_failed"]

# 監査ログのアクション種別。
AuditAction = Literal[
    "login_success",
    "login_failure",
    "logout",
    "login_lockout",
    "user_create",
    "user_update",
    "user_role_change",
    "user_password_reset",
    "user_delete",
    "session_revoke",
    "article_star",
    "article_dismiss",
    "article_mark_read",
    "generation_limit_reached",
    "rss_source_add",
    "rss_source_remove",
    "preferences_update",
    "onboarding_complete",
    "storage_cleanup",
    "password_reset_requested",
    "password_reset_completed",
    "passkey_register",
    "passkey_used",
    "passkey_removed",
]

# クロスユーザーキャッシュのデフォルト言語。現状は固定値（言語切替 UI は未実装）。
# キャッシュキーの言語次元として将来の多言語化に備えて用意する。
DEFAULT_PODCAST_LANGUAGE: str = "ja-en"

# PodcastStatus の partial_failed はキャッシュに存在しない概念のため別型にする。
# 型レベルで混入を防ぎ、キャッシュの状態機械を明確に表現する。
CacheStatus = Literal["processing", "completed", "failed"]

# script_generator._DIFFICULTY_INSTRUCTIONS のキーと 1:1 対応させる。
# 新しい難易度を追加する場合は両ファイルを同時に変更すること。
DifficultyLevel = Literal["toeic_600", "toeic_900", "ielts_55", "ielts_7", "eiken_2", "eiken_p1"]

# ユーザーの権限ロール。admin はユーザー管理 API（/admin/users）を操作できる。
UserRole = Literal["admin", "user"]


class User(BaseModel):
    """アプリ利用ユーザー。

    Firestore コレクション `users/{username}` に対応する。
    - `username`: ログイン ID 兼ドキュメントキー（不変・小文字スラッグ）。
    - `user_id`: 各種データ（userPrefs/podcasts/recommendations）のパーティションキー。
      username とは分離し、ログイン ID を変更してもデータ参照が壊れないようにする（現状 username は不変）。
    - `password_hash`: bcrypt ハッシュ。平文は決して保存しない。API レスポンスにも含めない。
    - `email`: メールアドレス（パスワードリセット用）。既存 Firestore ドキュメントには無いため Optional。
    """

    username: str
    user_id: str
    password_hash: str
    role: UserRole = "user"
    display_name: str
    email: str | None = None
    created_at: datetime
    updated_at: datetime


class Session(BaseModel):
    """ログインセッション。

    Firestore コレクション `sessions/{session_id}` に対応する。
    `session_id` は発行トークンの SHA-256 ハッシュ（生トークンは保存しない）。
    リクエスト毎に存在と `expires_at` を検証し、期限切れは無効として扱う。
    """

    session_id: str
    user_id: str
    username: str
    role: UserRole
    created_at: datetime
    expires_at: datetime
    # issue #84: 本人のセッション管理（一覧・個別失効）用メタ情報。
    # 既存 Firestore ドキュメントには存在しないため、全て Optional+default で後方互換を保つ。
    # device_label は User-Agent 由来の表示名。ip_hash は生 IP を保存しないための SHA-256 ハッシュ
    # （ADR-014 の方針を踏襲）。IPv4 はエントロピーが低く無塩ハッシュは総当たりで復元しうるため、
    # 強い匿名化ではなく「生値非保存・等価照合」目的に留める（クライアントには返さない）。
    # last_used_at は直近アクセス時刻（スロットル更新）。
    device_label: str | None = None
    ip_hash: str | None = None
    last_used_at: datetime | None = None


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
    read_article_ids: list[str] = Field(default_factory=list)
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
    # WHY: 既存 Firestore ドキュメントには無いため default=0.0 で後方互換
    # （onboarding_completed と同手法。クライアント送信値は信用せず duration_seconds で clamp）
    playback_position_seconds: float = 0.0
    # WHY: 既存 Firestore ドキュメントには無いため default="" で後方互換
    # （playback_position_seconds と同手法。Gemini 1 回呼び出しで生成される台本タイトル）
    title: str = ""
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
    # WHY: 既存 Firestore ドキュメントには無いため default=None で後方互換
    # （japanese_intro_text と同手法。completed 時のみタイトルを格納する）
    title: str | None = None
    created_at: datetime


class AuditLog(BaseModel):
    """監査ログエントリー。

    Firestore コレクション `auditLogs/{doc_id}` に対応する。
    doc_id は Firestore の自動採番（add()）で生成される。
    action は必須。timestamp / ip / actor_user_id / actor_username / target_username / details は optional。
    IP は生値で保存する（ログイン試行レートリミット（ADR-014）と異なり、ハッシュ化しない）。
    """

    action: AuditAction
    timestamp: datetime
    actor_user_id: str | None = None
    actor_username: str | None = None
    target_username: str | None = None
    ip: str | None = None
    details: dict | None = None


class PushSubscription(BaseModel):
    """Web Push 購読情報。

    Firestore コレクション `pushSubscriptions/{doc_id}` に対応する。
    doc_id は endpoint の SHA-256 ハッシュ（冪等 upsert のため）。
    """

    user_id: str
    endpoint: str
    p256dh: str
    auth: str
    platform: str = "webpush"
    created_at: datetime


class ApnsDeviceToken(BaseModel):
    """iOS APNs デバイストークン（issue #80）。

    Firestore コレクション `apnsDeviceTokens/{doc_id}` に対応する。
    doc_id は device_token の SHA-256 ハッシュ（冪等 upsert のため）。
    Web Push の `PushSubscription` とは別コレクションで管理し、関心を分離する。
    """

    user_id: str
    device_token: str
    created_at: datetime


class WebAuthnCredential(BaseModel):
    """WebAuthn (Passkey) クレデンシャル。

    Firestore コレクション `credentials/{doc_id}` に対応する。
    doc_id は `hash_token(credential_id)` で O(1) 直引き可能にする。
    - `credential_id`: base64url エンコードされたクレデンシャル ID。
    - `public_key`: base64url エンコードされた公開鍵（API レスポンスには含めない）。
    - `sign_count`: リプレイアタック検出用カウンタ（後退は拒否）。
    - `transports`: 認証器が対応するトランスポート（"internal", "usb" 等）。
    """

    credential_id: str  # base64url
    user_id: str
    username: str
    public_key: str  # base64url — API レスポンスに出さない
    sign_count: int
    transports: list[str]
    aaguid: str | None = None
    name: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None


class WebAuthnChallenge(BaseModel):
    """WebAuthn チャレンジ（ワンタイム）。

    Firestore コレクション `webauthnChallenges/{challenge_id}` に対応する。
    challenge_id は `secrets.token_urlsafe(32)` で採番し、消費時にドキュメントを削除する。
    - `type`: "registration" または "authentication"。
    - `user_id`: registration 時はログインユーザーの user_id、authentication 時は None。
    - `expires_at`: 有効期限（過ぎたら consume_challenge は None を返す）。
    """

    challenge_id: str
    challenge: str  # base64url
    user_id: str | None = None
    type: Literal["registration", "authentication"]
    expires_at: datetime
    created_at: datetime


class PasswordResetToken(BaseModel):
    """パスワードリセットトークン。

    Firestore コレクション `passwordResetTokens/{token_hash}` に対応する。
    doc_id は生トークンの SHA-256 ハッシュ（生トークンは DB に保存しない）。
    - token_hash: 生トークンの SHA-256 ハッシュ（doc-id）。
    - user_id: 対象ユーザー ID。
    - username: 対象ユーザー名（監査・復旧用）。
    - expires_at: トークン有効期限。
    - created_at: 発行時刻。
    - used_at: 消費時刻（None=未使用）。
    """

    token_hash: str
    user_id: str
    username: str
    expires_at: datetime
    created_at: datetime
    used_at: datetime | None = None
