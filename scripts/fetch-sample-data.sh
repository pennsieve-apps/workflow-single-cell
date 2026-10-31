#!/usr/bin/env bash
# Downloads the demo data of the single-cell workflows from Pennsieve Discover (public, CC BY 4.0)
# into data/. Discover files sit in a requester-pays S3 bucket, so any AWS credentials work
# (set AWS_PROFILE); you pay the (small) transfer. Idempotent: files already present are skipped.
#
#   scripts/fetch-sample-data.sh snrna     # Discover 475 + 480: Cell Ranger count .h5 (filtered + raw) + SPARC sheets
#   scripts/fetch-sample-data.sh multiome  # Discover 465: aggregated MTX + ATAC fragments (~20 GB)
set -euo pipefail
cd "$(dirname "$0")/.."
API=https://api.pennsieve.io/discover

uri() { curl -fsS "$API/datasets/$1/versions/$2/files?path=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$3")" | python3 -c 'import json,sys;print(json.load(sys.stdin)["uri"])'; }
browse() { curl -fsS "$API/datasets/$1/versions/$2/files/browse?path=$3&limit=1000"; }
# walk <id> <version> <path>: every file path under <path>
walk() {
  browse "$1" "$2" "$3" | python3 -c 'import json,sys
for f in json.load(sys.stdin).get("files", []): print(f["type"], f["path"])' | while read -r t p; do
    if [ "$t" = Directory ]; then walk "$1" "$2" "$p"; else echo "$p"; fi
  done
}
get() { # get <id> <version> <path> <dest>
  [ -s "$4" ] && return 0
  mkdir -p "$(dirname "$4")"
  aws s3 cp --only-show-errors --request-payer requester "$(uri "$1" "$2" "$3")" "$4.part" && mv "$4.part" "$4"
}
fetch() { # fetch <id> <version> <dest dir> <regex of paths to keep>
  for f in files/subjects.xlsx files/samples.xlsx files/dataset_description.xlsx; do get "$1" "$2" "$f" "$3/${f#files/}"; done
  walk "$1" "$2" files/primary | grep -E "$4" | while read -r p; do get "$1" "$2" "$p" "$3/${p#files/}"; done
}

case "${1:-snrna}" in
  snrna)
    fetch 475 1 data/drg-c2 'count/sample_(filtered|raw)_feature_bc_matrix(\.h5$|/)'
    fetch 480 1 data/drg-atlas 'count/sample_(filtered|raw)_feature_bc_matrix(\.h5$|/)'
    ;;
  multiome)
    fetch 465 1 data/sympathetic-multiome 'filtered_feature_bc_matrix/|atac_fragments\.tsv\.gz$|meta\.data\.csv$'
    ;;
  *) echo "usage: $0 snrna|multiome" >&2; exit 2 ;;
esac
echo done
