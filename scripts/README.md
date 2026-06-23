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

## deploy_firestore_indexes.sh

Firestore の複合インデックスを `../firestore.indexes.json`（正本）から冪等に適用する。
`where + order_by` や複数等価 + `array_contains` のクエリは複合インデックスが無いと
実行時に `FailedPrecondition: The query requires an index` で 500 になるため、
定義をコードと一緒にバージョン管理し、本スクリプトでデプロイする。

```bash
cd backend
# gcloud auth 済み・プロジェクト設定が有効な環境で
GCP_PROJECT_ID=<project> ./scripts/deploy_firestore_indexes.sh
./scripts/deploy_firestore_indexes.sh --dry-run   # 実行内容の確認のみ
```

- 冪等: 既存と同一のインデックスは ALREADY_EXISTS となり、スキップ扱いで正常終了する。
- 新しいクエリで複合インデックスが必要になったら、まず `firestore.indexes.json` を更新して本スクリプトを再実行する。
- firebase CLI 導入済みなら `firebase deploy --only firestore:indexes` でも同 JSON を適用できる。
