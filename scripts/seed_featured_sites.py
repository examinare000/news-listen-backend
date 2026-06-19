"""featuredSites コレクションへ初期おすすめサイトを投入するブートストラップスクリプト。

DB 管理の正は管理用 API（/admin/featured-sites）だが、初回投入用に
FirestoreClient.save_featured_site を直接呼ぶ最小スクリプトを用意する。

実行（GOOGLE_APPLICATION_CREDENTIALS / プロジェクト設定が必要）:

    cd backend
    python -m scripts.seed_featured_sites

冪等: 既存 doc は同一 slug で全置換されるため、繰り返し実行しても重複しない。
"""
from __future__ import annotations

from shared.firestore_client import FirestoreClient
from shared.models import FeaturedSite
from shared.utils import slugify

# 暫定デフォルト（order 昇順）。RSS URL は各サイトの公式フィード。
# thumbnail_url は各サイトの favicon を暫定利用（管理 API で後から差し替え可能）。
_DEFAULT_SITES: list[dict] = [
    {
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "thumbnail_url": "https://www.theverge.com/favicon.ico",
        "description": "テクノロジー・科学・カルチャー全般",
    },
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "thumbnail_url": "https://techcrunch.com/favicon.ico",
        "description": "スタートアップとテクノロジーのニュース",
    },
    {
        "name": "Engadget",
        "url": "https://www.engadget.com/rss.xml",
        "thumbnail_url": "https://www.engadget.com/favicon.ico",
        "description": "ガジェット・コンシューマーテック",
    },
    {
        "name": "Wired (Technology)",
        "url": "https://www.wired.com/feed/category/business/latest/rss",
        "thumbnail_url": "https://www.wired.com/favicon.ico",
        "description": "テクノロジーがもたらす変化を読み解く",
    },
    {
        "name": "Mashable",
        "url": "https://mashable.com/feeds/rss/all",
        "thumbnail_url": "https://mashable.com/favicon.ico",
        "description": "テック・カルチャー・エンタメ",
    },
    {
        "name": "HackerNews",
        "url": "https://hnrss.org/frontpage",
        "thumbnail_url": "https://news.ycombinator.com/favicon.ico",
        "description": "開発者コミュニティの話題のリンク",
    },
    {
        "name": "VentureBeat",
        "url": "https://venturebeat.com/feed/",
        "thumbnail_url": "https://venturebeat.com/favicon.ico",
        "description": "AI・エンタープライズテックのニュース",
    },
]


def seed(db: FirestoreClient | None = None) -> int:
    """デフォルトサイトを Firestore に投入し、投入件数を返す。"""
    db = db or FirestoreClient()
    for order, entry in enumerate(_DEFAULT_SITES):
        site = FeaturedSite(
            id=slugify(entry["name"]),
            name=entry["name"],
            url=entry["url"],
            thumbnail_url=entry.get("thumbnail_url"),
            description=entry.get("description"),
            order=order,
        )
        db.save_featured_site(site)
        print(f"seeded: {site.order:>2}  {site.id}  {site.url}")
    return len(_DEFAULT_SITES)


if __name__ == "__main__":
    count = seed()
    print(f"done: {count} featured sites seeded")
