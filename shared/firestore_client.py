"""Firestore CRUD helpers for all domain entities."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from google.cloud import firestore

from shared.models import (
    Article,
    AuditLog,
    FeaturedSite,
    Podcast,
    PodcastCache,
    PushSubscription,
    Recommendation,
    Session,
    User,
    UserPrefs,
)

if TYPE_CHECKING:
    from shared.models import PasswordResetToken


class FirestoreClient:
    def __init__(self) -> None:
        self._db = firestore.Client()

    # ---------- Articles ----------

    def article_exists(self, article_id: str) -> bool:
        doc = self._db.collection("articles").document(article_id).get()
        return doc.exists

    def save_article(self, article: Article) -> None:
        # mode="json" converts datetime to ISO-8601 strings, preventing Firestore
        # type mismatches when reading back through Pydantic validation.
        data = article.model_dump(mode="json")
        data.pop("id")  # id はドキュメントキーのみ
        self._db.collection("articles").document(article.id).set(data, merge=True)

    def get_article(self, article_id: str) -> Article | None:
        doc = self._db.collection("articles").document(article_id).get()
        if not doc.exists:
            return None
        return Article(**{**doc.to_dict(), "id": doc.id})

    def get_recent_articles(self, limit: int = 200) -> list[Article]:
        docs = (
            self._db.collection("articles")
            .order_by("published_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [Article(**{**doc.to_dict(), "id": doc.id}) for doc in docs]

    # ---------- UserPrefs ----------

    def get_user_prefs(self, user_id: str) -> UserPrefs:
        doc = self._db.collection("userPrefs").document(user_id).get()
        if not doc.exists:
            return UserPrefs(user_id=user_id, default_difficulty="toeic_900")
        return UserPrefs(**doc.to_dict())

    def save_user_prefs(self, prefs: UserPrefs) -> None:
        self._db.collection("userPrefs").document(prefs.user_id).set(
            prefs.model_dump(mode="json")
        )

    def add_starred_article(self, user_id: str, article_id: str) -> None:
        self._db.collection("userPrefs").document(user_id).set(
            {"starred_article_ids": firestore.ArrayUnion([article_id])}, merge=True
        )

    def add_dismissed_article(self, user_id: str, article_id: str) -> None:
        self._db.collection("userPrefs").document(user_id).set(
            {"dismissed_article_ids": firestore.ArrayUnion([article_id])}, merge=True
        )

    def add_read_article(self, user_id: str, article_id: str) -> None:
        self._db.collection("userPrefs").document(user_id).set(
            {"read_article_ids": firestore.ArrayUnion([article_id])}, merge=True
        )

    # ---------- Users ----------

    def get_user(self, username: str) -> User | None:
        """users/{username} を O(1) 直引きする。username は doc-id。"""
        doc = self._db.collection("users").document(username).get()
        if not doc.exists:
            return None
        return User(**{**doc.to_dict(), "username": doc.id})

    def get_user_by_user_id(self, user_id: str) -> User | None:
        """user_id（パーティションキー）からユーザーを逆引きする。"""
        docs = list(
            self._db.collection("users")
            .where("user_id", "==", user_id)
            .limit(1)
            .stream()
        )
        if not docs:
            return None
        doc = docs[0]
        return User(**{**doc.to_dict(), "username": doc.id})

    def list_users(self) -> list[User]:
        """全ユーザーを username 昇順で取得する（管理用）。"""
        docs = self._db.collection("users").order_by("__name__").stream()
        return [User(**{**doc.to_dict(), "username": doc.id}) for doc in docs]

    def save_user(self, user: User) -> None:
        """users/{username} を全置換で書き込む。

        save_featured_site と同じく username は doc-id として使うためペイロードから除外する。
        """
        data = user.model_dump(mode="json")
        data.pop("username")
        self._db.collection("users").document(user.username).set(data)

    def delete_user(self, username: str) -> None:
        self._db.collection("users").document(username).delete()

    # ---------- Sessions ----------

    def create_session(self, session: Session) -> None:
        """sessions/{session_id} を書き込む。session_id は doc-id（トークンのハッシュ）。"""
        data = session.model_dump(mode="json")
        data.pop("session_id")
        self._db.collection("sessions").document(session.session_id).set(data)

    def get_session(self, session_id: str) -> Session | None:
        """セッションを取得する。期限切れなら削除して None を返す。

        session_id は受領トークンの SHA-256 ハッシュ（呼び出し側で算出済み）。
        """
        ref = self._db.collection("sessions").document(session_id)
        doc = ref.get()
        if not doc.exists:
            return None
        session = Session(**{**doc.to_dict(), "session_id": doc.id})
        if session.expires_at <= datetime.now(timezone.utc):
            # 期限切れセッションは遅延削除する（次回参照を高速化し、ゴミを残さない）。
            ref.delete()
            return None
        return session

    def delete_session(self, session_id: str) -> None:
        self._db.collection("sessions").document(session_id).delete()

    def delete_sessions_for_user(self, user_id: str) -> int:
        """指定ユーザーの全セッションを削除し、削除件数を返す（強制ログアウト）。

        ユーザー削除・降格・管理者によるパスワードリセット時に呼び、TTL 満了を待たずに
        既存セッションを失効させる。失効しないと削除済み/降格済みユーザーが TTL（既定7日）
        の間アクセスを継続できてしまう。
        """
        docs = list(
            self._db.collection("sessions").where("user_id", "==", user_id).stream()
        )
        for doc in docs:
            doc.reference.delete()
        return len(docs)

    # ---------- Featured sites (global) ----------

    def get_featured_sites(self) -> list[FeaturedSite]:
        """おすすめサイトを order 昇順で全件取得する。

        get_recent_articles と同じ order_by ストリーム流儀。id は doc.id から復元する。
        """
        docs = (
            self._db.collection("featuredSites")
            .order_by("order")
            .stream()
        )
        return [FeaturedSite(**{**doc.to_dict(), "id": doc.id}) for doc in docs]

    def get_featured_site(self, site_id: str) -> FeaturedSite | None:
        doc = self._db.collection("featuredSites").document(site_id).get()
        if not doc.exists:
            return None
        return FeaturedSite(**{**doc.to_dict(), "id": doc.id})

    def save_featured_site(self, site: FeaturedSite) -> None:
        """featuredSites/{id} を全置換で書き込む。

        save_podcast / save_article と同じく id は doc-id として使うためペイロードから除外する。
        """
        data = site.model_dump(mode="json")
        data.pop("id")
        self._db.collection("featuredSites").document(site.id).set(data)

    def delete_featured_site(self, site_id: str) -> None:
        self._db.collection("featuredSites").document(site_id).delete()

    # ---------- Recommendations ----------

    def save_recommendation(self, rec: Recommendation) -> None:
        doc_id = f"{rec.user_id}_{rec.date}"
        self._db.collection("recommendations").document(doc_id).set(
            rec.model_dump(mode="json")
        )

    def get_recommendation(self, user_id: str, date: str) -> Recommendation | None:
        doc_id = f"{user_id}_{date}"
        doc = self._db.collection("recommendations").document(doc_id).get()
        if not doc.exists:
            return None
        return Recommendation(**doc.to_dict())

    # ---------- Podcasts ----------

    def save_podcast(self, podcast: Podcast) -> None:
        data = podcast.model_dump(mode="json")
        data.pop("id")
        self._db.collection("podcasts").document(podcast.id).set(data)

    def get_podcast(self, podcast_id: str) -> Podcast | None:
        """O(1) で Firestore から直接1件取得する。全件取得後フィルタより効率的。"""
        doc = self._db.collection("podcasts").document(podcast_id).get()
        if not doc.exists:
            return None
        return Podcast(**{**doc.to_dict(), "id": doc.id})

    def _user_single_podcast_query(
        self, user_id: str, article_id: str, difficulty: str
    ):
        """(user_id, article_id, difficulty, type='single') を引く per-user Podcast 共通クエリ。

        WHY: get_user_podcast_for_article / podcast_exists_for_article /
        try_acquire_user_podcast が同一の where 連鎖を持つため一元化し、
        スキーマ変更時に一部だけ更新し損なう不整合（status フィルタ漏れ等）を防ぐ。
        呼び出し元が status フィルタ・limit・stream（transaction 含む）を付与する。
        """
        return (
            self._db.collection("podcasts")
            .where("user_id", "==", user_id)
            .where("article_ids", "array_contains", article_id)
            .where("difficulty", "==", difficulty)
            .where("type", "==", "single")
        )

    def get_user_podcast_for_article(
        self, user_id: str, article_id: str, difficulty: str
    ) -> Podcast | None:
        """(user_id, article_id, difficulty, type='single') の per-user Podcast を返す。

        status 不問（processing・completed・failed いずれでも返す）。
        id は doc.id から復元される。

        WHY: 生成中の processing 行を確認したり、既に存在する per-user Podcast を
        取得するため、status フィルタを入れない。呼び出し元で必要に応じてフィルタ。
        """
        docs = list(
            self._user_single_podcast_query(user_id, article_id, difficulty)
            .limit(1)
            .stream()
        )
        if not docs:
            return None
        doc = docs[0]
        return Podcast(**{**doc.to_dict(), "id": doc.id})

    def get_podcasts_for_user(self, user_id: str, limit: int = 50) -> list[Podcast]:
        docs = (
            self._db.collection("podcasts")
            .where("user_id", "==", user_id)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [Podcast(**{**doc.to_dict(), "id": doc.id}) for doc in docs]

    def delete_podcast(self, podcast_id: str) -> None:
        """Podcast を削除する（冪等・type 問わず全削除）。

        podcasts/{podcast_id} ドキュメントを削除する。
        ドキュメント不在でも例外を上げない（Firestore delete() の仕様）。

        Args:
            podcast_id: 削除対象の Podcast ID。
        """
        self._db.collection("podcasts").document(podcast_id).delete()

    def podcast_exists_for_article(
        self, user_id: str, article_id: str, difficulty: str,
        statuses: tuple[str, ...] = ("completed",)
    ) -> bool:
        # WHY: statuses デフォルトを "completed" のみにして、processing 状態のポッドキャストを
        # 未存在扱いにする。これにより生成器が「既存 processing 行があっても」次の生成を開始できる
        # （per-user 単位で同時実行可能）。呼び出し元で異なる statuses が必要な場合は明示的に指定。
        docs = list(
            self._user_single_podcast_query(user_id, article_id, difficulty)
            .where("status", "in", statuses)
            .limit(1)
            .stream()
        )
        return len(docs) > 0

    # ---------- Podcast cache (cross-user) ----------

    def get_podcast_cache(self, cache_key: str) -> PodcastCache | None:
        """podcastCache/{cache_key} を O(1) 直引きする。

        Podcast.id / Article.id と同じ流儀で cache_key を doc.id から復元する。
        """
        doc = self._db.collection("podcastCache").document(cache_key).get()
        if not doc.exists:
            return None
        return PodcastCache(**{**doc.to_dict(), "cache_key": doc.id})

    def save_podcast_cache(self, cache: PodcastCache) -> None:
        """podcastCache/{cache_key} を全置換で書き込む。

        save_podcast / save_article と同じ mode='json' 流儀で datetime を
        ISO 文字列に変換し、Firestore との型不整合を防ぐ。
        cache_key は doc-id として使うためペイロードから除外する。
        """
        data = cache.model_dump(mode="json")
        data.pop("cache_key")
        self._db.collection("podcastCache").document(cache.cache_key).set(data)

    def try_acquire_user_podcast(
        self, user_id: str, article_id: str, difficulty: str, language: str
    ) -> str | None:
        """per-user Podcast の processing 確保をトランザクションで原子的に行う。

        (user_id, article_id, difficulty, type='single') タプルに対して、
        処理中の Podcast 行が無ければ新規作成して id を返す。
        既存行があれば None を返す（冪等・レース防御）。

        WHY: 複数の star リクエストが同時に同じ記事を処理するレースを防ぐため、
        per-user 単位で 1 回だけ processing 行を作成する。返された id で
        その後の生成を進める。None が返ったなら既に誰かが開始済みなので、
        呼び出し元は処理をスキップする。

        Returns:
            生成権を取得した場合は新規 id、既に存在すれば None。
        """
        podcast_id = str(uuid.uuid4())
        ref = self._db.collection("podcasts").document(podcast_id)
        now = datetime.now(timezone.utc)

        @firestore.transactional
        def _acquire(transaction) -> str | None:
            # 同じ (user_id, article_id, difficulty, type='single') の行が既にあるか確認。
            # WHY: stream(transaction=transaction) で読み取りをトランザクションに束縛する。
            # これを怠ると（try_acquire_cache の ref.get(transaction=...) と異なり）読み取りが
            # トランザクション外となり、2 つの star が同時に「不在」を見て二重行を作るレースを許す。
            existing_docs = list(
                self._user_single_podcast_query(user_id, article_id, difficulty)
                .limit(1)
                .stream(transaction=transaction)
            )
            if existing_docs:
                # 既存行がある → 誰かが既に確保した
                return None

            # 不在 → processing 行を作成
            transaction.set(
                ref,
                {
                    "type": "single",
                    "article_ids": [article_id],
                    "difficulty": difficulty,
                    "audio_url": "",
                    "japanese_intro_text": "",
                    "duration_seconds": 0,
                    "status": "processing",
                    "error_message": None,
                    "playback_position_seconds": 0.0,
                    "created_at": now,
                    "user_id": user_id,
                },
            )
            return podcast_id

        return _acquire(self._db.transaction())

    def try_acquire_cache(
        self,
        cache_key: str,
        article_id: str,
        difficulty: str,
        language: str,
    ) -> bool:
        """podcastCache の processing 確保をトランザクションで原子的に行う。

        try_acquire_job_lock と同型の read→条件付き write パターン。
        - 不存在 / failed → processing を書き込み True を返す（生成権取得）。
        - processing / completed → 書き込まず False を返す（スキップ）。

        True を返した呼び出し元が生成権を持ち、生成後に save_podcast_cache で
        completed / failed に遷移させる責務を負う。
        """
        ref = self._db.collection("podcastCache").document(cache_key)
        now = datetime.now(timezone.utc)

        @firestore.transactional
        def _acquire(transaction) -> bool:
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:
                status = snapshot.to_dict().get("status")
                if status in ("processing", "completed"):
                    return False
            transaction.set(
                ref,
                {
                    "article_id": article_id,
                    "difficulty": difficulty,
                    "language": language,
                    "status": "processing",
                    "created_at": now,
                },
            )
            return True

        return _acquire(self._db.transaction())

    def promote_user_podcast(
        self,
        podcast_id: str,
        status: str,
        audio_url: str = "",
        japanese_intro_text: str = "",
        duration_seconds: int = 0,
        error_message: str | None = None,
    ) -> None:
        """processing 状態の per-user Podcast をステータス遷移させる（冪等）。

        podcast_id の現在の status を確認し、processing のときだけ
        新しいステータスと付随データ（audio_url など）に update する。
        既に completed / failed なら no-op（レース防御）。

        WHY: 生成完了後のステータス遷移時に、別リクエストが既に更新済みの場合に
        重複上書きを防ぐ。トランザクション内で processing 確認 → 条件付き update。

        Args:
            podcast_id: 遷移対象の Podcast id
            status: 遷移先ステータス（"completed" / "failed" など）
            audio_url: 完了時の音声 URL
            japanese_intro_text: 完了時のイントロテキスト
            duration_seconds: 完了時の音声長
            error_message: 失敗時のエラーメッセージ
        """
        ref = self._db.collection("podcasts").document(podcast_id)

        @firestore.transactional
        def _promote(transaction) -> None:
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                # ドキュメント不在 → no-op
                return

            current_status = snapshot.to_dict().get("status")
            # processing のときだけ遷移を許可。既に completed/failed なら no-op。
            if current_status != "processing":
                return

            # processing → 新ステータスへ更新
            update_data = {
                "status": status,
                "audio_url": audio_url,
                "japanese_intro_text": japanese_intro_text,
                "duration_seconds": duration_seconds,
            }
            if error_message is not None:
                update_data["error_message"] = error_message

            transaction.update(ref, update_data)

        _promote(self._db.transaction())

    # ---------- Job locks (debounce) ----------

    def try_acquire_job_lock(
        self, user_id: str, job_name: str, ttl_seconds: int
    ) -> bool:
        """ジョブ起動の debounce ロックを原子的に取得する。

        TTL 内に有効なロックが既にあれば False を返す（多重起動の抑止）。
        トランザクションで read→条件付き write を行い、複数 api インスタンスが
        同時に同じジョブを起動するレースを防ぐ。

        ジョブ完了時の明示的な解放は行わず、TTL 失効による自然解放（debounce）とする。
        ジョブ側が starred/dismissed の全件を都度走査するため、ウィンドウ内の連続操作を
        1 回の実行へまとめても取りこぼさない。
        """
        doc_id = f"{user_id}_{job_name}"
        ref = self._db.collection("jobLocks").document(doc_id)
        now = datetime.now(timezone.utc)

        @firestore.transactional
        def _acquire(transaction) -> bool:
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:
                expires_at = snapshot.to_dict().get("expires_at")
                if expires_at and expires_at > now:
                    return False
            transaction.set(
                ref, {"expires_at": now + timedelta(seconds=ttl_seconds)}
            )
            return True

        return _acquire(self._db.transaction())

    # ---------- Login rate limiting ----------

    def check_login_lock(self, key: str, now: datetime | None = None) -> datetime | None:
        """ログイン試行のロック状態を確認する。

        key（IP or username）がロック中なら locked_until datetime を返す。
        読み取りのみで、ロック中でなければ None を返す。

        Args:
            key: IP or username のハッシュ化キー（"ip:..." or "user:..."）
            now: 現在時刻（デフォルト: UTC now）

        Returns:
            ロック中なら locked_until datetime、ロック中でなければ None
        """
        if now is None:
            now = datetime.now(timezone.utc)

        doc_id = f"loginAttempts_{key}"
        ref = self._db.collection("loginAttempts").document(doc_id)
        snapshot = ref.get()
        if not snapshot.exists:
            return None

        data = snapshot.to_dict()
        locked_until = data.get("locked_until")
        if locked_until and locked_until > now:
            return locked_until
        return None

    def register_failed_login(
        self,
        key: str,
        now: datetime | None = None,
        max_attempts: int = 5,
        window_seconds: int = 900,
        lockout_seconds: int = 900,
    ) -> bool:
        """失敗したログイン試行を記録し、閾値超過で新規ロックを設定する。

        トランザクションで read→write を行い、複数インスタンスの同時アクセスに対応する。
        ウィンドウ失効していれば count を 1 でリセット、内なら count を +1。
        count >= max_attempts でロック状態を設定し、新規ロック時 True を返す。

        Args:
            key: IP or username のハッシュ化キー（"ip:..." or "user:..."）
            now: 現在時刻（デフォルト: UTC now）
            max_attempts: 閾値（最大試行回数）
            window_seconds: 計数ウィンドウの長さ（秒）
            lockout_seconds: ロック期間（秒）

        Returns:
            新規ロック発生なら True、閾値未超過なら False
        """
        if now is None:
            now = datetime.now(timezone.utc)

        doc_id = f"loginAttempts_{key}"
        ref = self._db.collection("loginAttempts").document(doc_id)

        @firestore.transactional
        def _update(transaction) -> bool:
            snapshot = ref.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else {}
            count = data.get("count", 0)
            window_start = data.get("window_start")

            # ウィンドウ失効判定
            if window_start is None or (now - window_start).total_seconds() > window_seconds:
                # ウィンドウ失効。count をリセット
                count = 1
                window_start = now
            else:
                # ウィンドウ内。count を +1
                count += 1

            new_lock = False
            locked_until = data.get("locked_until")

            # 閾値超過でロック設定
            if count >= max_attempts:
                locked_until = now + timedelta(seconds=lockout_seconds)
                new_lock = True

            # トランザクション内で更新
            transaction.set(
                ref,
                {
                    "count": count,
                    "window_start": window_start,
                    "locked_until": locked_until,
                },
            )
            return new_lock

        return _update(self._db.transaction())

    def clear_login_attempts(self, key: str) -> None:
        """ログイン成功時に当該キーの試行カウンタをリセットする。

        Args:
            key: IP or username のハッシュ化キー（"ip:..." or "user:..."）
        """
        doc_id = f"loginAttempts_{key}"
        ref = self._db.collection("loginAttempts").document(doc_id)
        ref.delete()

    def consume_rate_limit(
        self, key: str, now: datetime | None = None, max_requests: int = 0, window_seconds: int = 60
    ) -> tuple[bool, int]:
        """汎用 API レート制限。固定ウィンドウ内のカウンタをトランザクションで管理する。

        コレクション rateLimits、doc_id f"rateLimits_{key}" に記録。スキーマ:
        {"key": str, "count": int, "window_start": timestamp}。

        ウィンドウが失効した（経過秒数 >= window_seconds）か、または初回なら
        count=1, window_start=now でリセット。ウィンドウ内かつ count < max_requests なら
        count を +1。超過（count >= max_requests）なら、allowed=False を返しカウント非更新。

        超過試行（allowed=False）は doc を更新しない（非カウント）。

        Args:
            key: レート制限キー（"api:ip:..."、"api:user:..." 等）
            now: 現在時刻（デフォルト: UTC now）
            max_requests: 閾値（0 で無効化・即座に return）
            window_seconds: ウィンドウ幅（秒）

        Returns:
            (allowed: bool, retry_after: int)
                allowed=True: カウント許可。retry_after=0。
                allowed=False: 超過。retry_after=窓終了までの秒数（最小1）。
        """
        if max_requests <= 0:
            # 無効化: DB アクセス無しで即座に return
            return True, 0

        if now is None:
            now = datetime.now(timezone.utc)

        doc_id = f"rateLimits_{key}"
        ref = self._db.collection("rateLimits").document(doc_id)

        @firestore.transactional
        def _consume(transaction) -> tuple[bool, int]:
            snapshot = ref.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else {}
            count = data.get("count", 0)
            window_start = data.get("window_start")

            # ウィンドウ失効判定。失効条件: window_start 無しか、経過秒 >= window_seconds
            if window_start is None or (now - window_start).total_seconds() >= window_seconds:
                # ウィンドウ失効。リセット。
                count = 1
                window_start = now
                transaction.set(
                    ref,
                    {
                        "key": key,
                        "count": count,
                        "window_start": window_start,
                    },
                )
                return True, 0

            # ウィンドウ内。count をチェック。
            if count >= max_requests:
                # 既に max に達している。超過試行。
                retry_after = max(1, int(window_seconds - (now - window_start).total_seconds()))
                return False, retry_after

            # ウィンドウ内かつ max 未到達。+1 して保存。
            count += 1
            transaction.set(
                ref,
                {
                    "key": key,
                    "count": count,
                    "window_start": window_start,
                },
            )
            return True, 0

        return _consume(self._db.transaction())

    # ---------- Audit logs ----------

    def append_audit_log(self, audit: AuditLog) -> str:
        """監査ログを auditLogs コレクションに追記する（自動採番）。

        Firestore の add() メソッドで新規ドキュメント ID を採番し、
        監査ログを追記専用で保存する。更新・削除メソッドは存在しない。

        例外は握り潰さず呼び出し元へ伝播させる。ベストエフォート（書き込み失敗でも
        本操作を成功させ、失敗を error ログ化する）の責務は AuditLogger.record に集約し、
        失敗の可観測性を確保するため。

        Args:
            audit: 追記する AuditLog エントリー

        Returns:
            生成されたドキュメント ID
        """
        data = audit.model_dump(mode="json")
        # add() は (WriteResult, DocumentReference) を返す。
        _, doc_ref = self._db.collection("auditLogs").add(data)
        return doc_ref.id

    def list_audit_logs(
        self, action: str | None = None, limit: int = 50
    ) -> list[AuditLog]:
        """監査ログを timestamp DESC で取得（任意フィルタ）。

        auditLogs コレクションから timestamp 降順で取得し、
        任意の action でフィルタリング可能。

        Args:
            action: フィルタ対象のアクション（None なら全件）
            limit: 取得上限件数（既定 50）

        Returns:
            AuditLog リスト（timestamp DESC）
        """
        query = self._db.collection("auditLogs")
        if action is not None:
            query = query.where("action", "==", action)
        docs = query.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(
            limit
        ).stream()
        return [AuditLog(**doc.to_dict()) for doc in docs]

    # ---------- Push Subscriptions ----------

    def _subscription_doc_id(self, endpoint: str) -> str:
        """endpoint の SHA-256 ハッシュを doc-id として使う（冪等 upsert）。"""
        return hashlib.sha256(endpoint.encode()).hexdigest()

    def save_push_subscription(self, sub: PushSubscription) -> None:
        """Web Push 購読を保存する（冪等）。

        同じ endpoint を複数回 save した場合、同じ doc-id で上書き（upsert）される。
        """
        doc_id = self._subscription_doc_id(sub.endpoint)
        data = sub.model_dump(mode="json")
        self._db.collection("pushSubscriptions").document(doc_id).set(data, merge=True)

    def get_push_subscriptions(self, user_id: str) -> list[PushSubscription]:
        """指定ユーザーの Web Push 購読一覧を取得する。"""
        docs = (
            self._db.collection("pushSubscriptions")
            .where("user_id", "==", user_id)
            .stream()
        )
        return [PushSubscription(**doc.to_dict()) for doc in docs]

    def delete_push_subscription(self, user_id: str, endpoint: str) -> None:
        """Web Push 購読を削除する（冪等・所有権検証付き）。

        指定ユーザーが所有する購読のみを削除する（他ユーザーのデータを削除できない）。
        ドキュメント不在でも例外を出さない。
        """
        doc_id = self._subscription_doc_id(endpoint)
        doc = self._db.collection("pushSubscriptions").document(doc_id).get()
        if doc.exists and doc.to_dict().get("user_id") == user_id:
            self._db.collection("pushSubscriptions").document(doc_id).delete()

    # ---------- Password Reset Tokens ----------

    def save_reset_token(self, token: "PasswordResetToken") -> None:
        """パスワードリセットトークンを保存する。

        Firestore コレクション `passwordResetTokens/{token_hash}` に保存。
        **ペイロードに生トークンを含めない**（token_hash のみ）。
        """
        data = token.model_dump(mode="json")
        # token_hash を doc-id として使うため、ペイロードからは除外しない（firestore で id を復元）
        self._db.collection("passwordResetTokens").document(token.token_hash).set(data)

    def get_reset_token(self, token_hash: str) -> "PasswordResetToken | None":
        """パスワードリセットトークンを取得する（O(1) 直引き）。

        Args:
            token_hash: 生トークンの SHA-256 ハッシュ

        Returns:
            PasswordResetToken or None（不在の場合）
        """
        from shared.models import PasswordResetToken

        doc = self._db.collection("passwordResetTokens").document(token_hash).get()
        if not doc.exists:
            return None
        return PasswordResetToken(**doc.to_dict())

    def consume_reset_token(self, token_hash: str, now: datetime) -> bool:
        """パスワードリセットトークンを消費する（トランザクション）。

        未使用かつ期限内のトークンのみ消費可能。
        used_at = now で原子的に更新し、他のリクエストとの競合を防ぐ。

        Args:
            token_hash: 生トークンの SHA-256 ハッシュ
            now: 現在時刻（テスト容易性のため注入可能）

        Returns:
            True: 消費成功。False: 不在・期限切れ・既用。
        """
        ref = self._db.collection("passwordResetTokens").document(token_hash)

        @firestore.transactional
        def _consume(transaction) -> bool:
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                return False

            data = snapshot.to_dict()
            # 検証: used_at が None（未使用）か確認
            if data.get("used_at") is not None:
                return False

            # 検証: 期限内か確認
            expires_at = data.get("expires_at")
            if expires_at is None or now >= expires_at:
                return False

            # 検証成功: used_at を now に更新
            transaction.update(ref, {"used_at": now})
            return True

        return _consume(self._db.transaction())
