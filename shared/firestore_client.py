"""Firestore CRUD helpers for all domain entities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from google.cloud import firestore

from shared.models import Article, Podcast, PodcastCache, Recommendation, UserPrefs
from shared.utils import article_id_for_url


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

    def get_podcasts_for_user(self, user_id: str, limit: int = 50) -> list[Podcast]:
        docs = (
            self._db.collection("podcasts")
            .where("user_id", "==", user_id)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [Podcast(**{**doc.to_dict(), "id": doc.id}) for doc in docs]

    def podcast_exists_for_article(
        self, user_id: str, article_id: str, difficulty: str
    ) -> bool:
        docs = list(
            self._db.collection("podcasts")
            .where("user_id", "==", user_id)
            .where("article_ids", "array_contains", article_id)
            .where("difficulty", "==", difficulty)
            .where("type", "==", "single")
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
