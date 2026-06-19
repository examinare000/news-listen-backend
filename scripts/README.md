# scripts

運用・ブートストラップ用スクリプト。

## seed_featured_sites.py

`featuredSites` コレクション（システム提供のおすすめサイト）へ初期データを投入する。
DB 管理の正は管理用 API（`/admin/featured-sites`）だが、初回投入をこのスクリプトで行う。

```bash
cd backend
# GOOGLE_APPLICATION_CREDENTIALS / GCP プロジェクト設定が有効な環境で実行
python -m scripts.seed_featured_sites
```

- 冪等: 各サイトは name から生成した slug を doc-id とし、全置換で書き込むため繰り返し実行しても重複しない。
- 投入後は管理 API（`POST/PUT/DELETE /admin/featured-sites`）で追加・更新・削除できる。
