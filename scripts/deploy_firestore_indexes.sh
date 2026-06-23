#!/usr/bin/env bash
#
# firestore.indexes.json を読み、複合インデックスを Firestore に冪等適用する。
#
# firebase CLI（firebase deploy --only firestore:indexes）が使えない環境向けに、
# gcloud firestore indexes composite create でインデックスを作成する。
# 既存と同一のインデックスは ALREADY_EXISTS となるため、それを正常終了として扱う。
#
# 正本は firestore.indexes.json。新しいクエリで複合インデックスが必要になったら
# まず JSON を更新し、本スクリプトで適用する（コードと定義の二重管理を避けるため）。
#
# 実行（gcloud auth 済み・プロジェクト設定が有効な環境で）:
#   cd backend
#   GCP_PROJECT_ID=news-listen-20260610 ./scripts/deploy_firestore_indexes.sh
#   ./scripts/deploy_firestore_indexes.sh --dry-run   # 実行内容の確認のみ
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INDEXES_JSON="$SCRIPT_DIR/../firestore.indexes.json"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
if [[ -z "$PROJECT_ID" ]]; then
  echo "ERROR: GCP_PROJECT_ID が未設定で gcloud のデフォルトプロジェクトも取得できません。" >&2
  exit 1
fi

if [[ ! -f "$INDEXES_JSON" ]]; then
  echo "ERROR: $INDEXES_JSON が見つかりません。" >&2
  exit 1
fi

# JSON を gcloud の --field-config 引数列へ変換する。
# order 指定フィールドは order=ascending/descending、配列フィールドは array-config=contains。
# 各インデックスを1行（collection-group<TAB>field-config群<TAB>...）で出力する。
# macOS 標準の bash 3.2 には mapfile が無いため while read で読み込む。
INDEX_LINES=()
while IFS= read -r line; do
  [[ -n "$line" ]] && INDEX_LINES+=("$line")
done < <(python3 - "$INDEXES_JSON" <<'PY'
import json
import sys

with open(sys.argv[1]) as f:
    data = json.load(f)

for idx in data.get("indexes", []):
    parts = [idx["collectionGroup"]]
    for field in idx["fields"]:
        path = field["fieldPath"]
        if "arrayConfig" in field:
            cfg = field["arrayConfig"].lower()  # CONTAINS -> contains
            parts.append(f"field-path={path},array-config={cfg}")
        else:
            order = field["order"].lower()  # ASCENDING -> ascending
            parts.append(f"field-path={path},order={order}")
    print("\t".join(parts))
PY
)

echo "対象プロジェクト: $PROJECT_ID"
echo "インデックス定義: ${INDEXES_JSON}（${#INDEX_LINES[@]} 件）"

for line in "${INDEX_LINES[@]}"; do
  IFS=$'\t' read -r collection_group field_configs <<<"$line"
  # field_configs には残りのタブ区切りフィールドが入るため配列に展開する。
  IFS=$'\t' read -r -a fields <<<"$field_configs"

  args=(firestore indexes composite create
    "--project=$PROJECT_ID"
    "--collection-group=$collection_group")
  for fc in "${fields[@]}"; do
    args+=("--field-config=$fc")
  done
  args+=(--async)

  echo "→ ${collection_group}: ${fields[*]}"
  if $DRY_RUN; then
    echo "  [dry-run] gcloud ${args[*]}"
    continue
  fi

  # 同一インデックスが既に存在する場合は ALREADY_EXISTS。冪等にするため正常終了扱い。
  if ! output=$(gcloud "${args[@]}" 2>&1); then
    if echo "$output" | grep -qi "already exists"; then
      echo "  既存（スキップ）"
    else
      echo "$output" >&2
      exit 1
    fi
  else
    echo "  作成リクエスト発行"
  fi
done

echo "完了。インデックスのビルドは非同期です。状態確認:"
echo "  gcloud firestore indexes composite list --project=$PROJECT_ID"
